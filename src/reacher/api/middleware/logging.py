"""Request/response logging with correlation-ID propagation.

Implemented as pure ASGI rather than ``BaseHTTPMiddleware`` for two reasons:

* Reading the body for logging must not consume it.  A pure ASGI middleware can
  wrap ``receive`` and replay the bytes it observed, so the endpoint still sees
  its payload.  ``BaseHTTPMiddleware`` makes that awkward and adds a task group
  per request.
* The correlation ``contextvar`` must be set on the same task that runs the
  endpoint.  Starlette's ``BaseHTTPMiddleware`` runs the handler in a *separate*
  anyio task, which copies the context at spawn time — a value set in the
  middleware would not be visible downstream.

The ``corr_id`` links a browser interaction to everything it causes: the HTTP
call, the kernel command, and the serial bytes that reach the Arduino.
"""

import json
import sys
import time
from typing import TypedDict

from pydantic import JsonValue
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ... import diagnostics
from ...diagnostics import context
from ...diagnostics.redact import redact, scrub_message
from ...diagnostics.schema import TIER_API

#: Header the frontend uses to hand its correlation ID to the backend.
CORR_HEADER = b"x-reacher-corr-id"

#: Bodies larger than this are recorded by size only.  Firmware uploads carry a
#: whole hex payload; logging it verbatim would be megabytes per request.
MAX_BODY = 4096

#: Paths that would otherwise log themselves in a loop, or add nothing.
_SKIP_PATHS = frozenset({"/api/logs/ingest"})

#: Polled constantly by the monitor and by peer discovery; logging every hit
#: buries everything else.  Failures still surface via their status code.
_QUIET_PATHS = frozenset({"/health"})


class _RequestFields(TypedDict, total=False):
    """The keyed fields of one ``http.request`` record."""

    method: str
    path: str
    status: int
    duration_ms: float
    query_len: int
    error: str
    body: JsonValue


class RequestLoggingMiddleware:
    """ASGI middleware recording one record per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap *app*, the next ASGI application in the stack."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI request, logging HTTP requests and replaying their body downstream."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(CORR_HEADER)
        corr_id = incoming.decode("latin-1")[:64] if incoming else context.new_corr_id()

        method = scope.get("method", "")
        body_chunks: list[bytes] = []
        captured = 0

        async def receive_logging() -> Message:
            nonlocal captured
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if chunk and captured < MAX_BODY:
                    body_chunks.append(chunk[: MAX_BODY - captured])
                    captured += len(chunk)
            return message

        status: dict[str, int] = {"code": 0}

        async def send_logging(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        started = time.perf_counter()
        with context.bind(corr_id=corr_id):
            # No except clause: the failure is only observed here and always propagates, so any
            # exception type must reach the log.  Cancellation is not a request failure.
            try:
                await self.app(scope, receive_logging, send_logging)
            finally:
                failure = sys.exc_info()[1]
                if isinstance(failure, Exception):
                    # str(exc) is message-shaped text, not a keyed field — it reaches
                    # diagnostics.log() directly (bypasses the stdlib bridge, so
                    # bridge.py's scrub_message() is not on this path) and redact()
                    # only matches by key, so a credentialed URL embedded in an
                    # exception (e.g. websockets.InvalidURI) would otherwise pass
                    # through unscrubbed.
                    error = scrub_message(f"{type(failure).__name__}: {failure}")
                    self._record(method, path, scope, 500, started, body_chunks, error=error)
                elif failure is None:
                    self._record(method, path, scope, status["code"], started, body_chunks)

    def _record(
        self,
        method: str,
        path: str,
        scope: Scope,
        status: int,
        started: float,
        body_chunks: list[bytes],
        error: str | None = None,
    ) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        # Successful health polls are noise; anything else about them is not.
        if path in _QUIET_PATHS and status < 400 and error is None:
            return

        data: _RequestFields = {"method": method, "path": path, "status": status, "duration_ms": duration_ms}
        query = scope.get("query_string") or b""
        if query:
            # May carry ?token=... — redact() cannot see inside a raw string, so
            # the query is recorded by presence, never by value.
            data["query_len"] = len(query)
        if error:
            data["error"] = error

        body = b"".join(body_chunks)
        if body:
            data["body"] = _decode_body(body)

        lvl = "error" if status >= 500 or error else "warn" if status >= 400 else "info"
        diagnostics.log(
            "http.request",
            tier=TIER_API,
            lvl=lvl,
            msg=f"{method} {path} → {status} ({duration_ms}ms)",
            src="reacher.api",
            **data,
        )


def _decode_body(body: bytes) -> JsonValue:
    """Return a redacted, JSON-preferring representation of a request body."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return f"[{len(body)} bytes, non-utf8]"
    # RecursionError: json.loads gives up on deeply nested arrays rather than raising a ValueError.
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        parsed = text
    # redact() returns JSON-serialisable data by contract (its own annotation is Any).
    redacted: JsonValue = redact(parsed)
    return redacted
