"""Ambient correlation state for diagnostic records.

The point of this module is the ``corr_id``: a UI interaction mints one, the
browser sends it as ``X-Reacher-Corr-Id``, request middleware binds it here, and
every record emitted while handling that request inherits it — including the
kernel's serial TX.  One filter over the log then reconstructs
click → HTTP → command → wire.

``contextvars`` is the right primitive because Starlette/FastAPI run sync
endpoints on an anyio worker thread that *copies* the context, so a binding made
in middleware survives into the endpoint and into anything it calls
synchronously.  It deliberately does **not** propagate into the kernel's
long-lived daemon threads: serial RX carries ``session_id`` but no ``corr_id``,
since the firmware cannot echo one back.
"""

import contextvars
import os
import secrets
import time
from contextlib import contextmanager
from typing import Iterator, Optional

#: Identifies one backend process, start to exit.  Every record carries it, so
#: records from a previous run in a rotated file are never mistaken for this one.
RUN_ID: str = secrets.token_hex(4)

#: Process start, used to derive the monotonic ``mono`` field on every record.
_T0 = time.monotonic()

_corr_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("reacher_corr_id", default=None)
_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("reacher_session_id", default=None)


def new_corr_id() -> str:
    """Mint a correlation ID.  Short — these appear on every line."""
    return secrets.token_hex(8)


def uptime() -> float:
    """Seconds since process start, from a monotonic clock.

    Preferred over wall-clock deltas for ordering because it is immune to NTP
    steps and manual clock changes mid-experiment.
    """
    return time.monotonic() - _T0


def get_corr_id() -> Optional[str]:
    return _corr_id.get()


def get_session_id() -> Optional[str]:
    return _session_id.get()


def set_corr_id(value: Optional[str]) -> None:
    _corr_id.set(value)


def set_session_id(value: Optional[str]) -> None:
    _session_id.set(value)


@contextmanager
def bind(corr_id: Optional[str] = None, session_id: Optional[str] = None) -> Iterator[str]:
    """Bind correlation state for the duration of the block.

    Yields the effective ``corr_id``.  Tokens are reset in a ``finally`` so an
    exception in the wrapped block cannot leak state onto the next request
    handled by the same worker thread.
    """
    effective = corr_id or _corr_id.get() or new_corr_id()
    corr_token = _corr_id.set(effective)
    sess_token = _session_id.set(session_id) if session_id is not None else None
    try:
        yield effective
    finally:
        _corr_id.reset(corr_token)
        if sess_token is not None:
            _session_id.reset(sess_token)


def process_meta() -> dict:
    """Static facts about this process, written once into ``meta.json``."""
    import platform
    import sys

    from .redact import redact_env

    try:
        from .. import __version__
    except Exception:  # pragma: no cover - defensive; version is always present
        __version__ = "unknown"

    return {
        "run_id": RUN_ID,
        "version": __version__,
        "pid": os.getpid(),
        "python": sys.version,
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "env": redact_env(dict(os.environ)),
    }
