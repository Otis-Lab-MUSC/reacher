"""WebSocket endpoint for real-time event streaming."""

import asyncio
import json
import logging
import os
import queue
import signal
import threading
import time
from collections import defaultdict
from collections.abc import Mapping
from typing import TypedDict

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from pydantic import JsonValue

from ..errors import KERNEL_ERRORS, WS_SEND_ERRORS
from ..middleware.auth import verify_ws_token
from reacher import pairing
from ...diagnostics.schema import TIER_API as _TIER_API
from ...diagnostics.setup import log as _diag_log
from ...session_manager import SessionManager

router = APIRouter()
logger = logging.getLogger(__name__)

# Per-session connected WebSocket clients
_connections: defaultdict[str, set[WebSocket]] = defaultdict(set)

#: Payload of a session event: JSON-serialisable keyed fields.
EventData = Mapping[str, JsonValue]


class _Event(TypedDict):
    """One queued session event, as sent to WebSocket clients."""

    type: str
    session_id: str
    data: EventData


# Thread-safe stdlib queue (safe to call from background REACHER threads)
_EVENT_QUEUE_MAX = 10000
_event_queue: queue.Queue[_Event] = queue.Queue(maxsize=_EVENT_QUEUE_MAX)

# Thread-safe dropped event counter
_dropped_events_lock = threading.Lock()
_dropped_events: int = 0

# Async event loop + notification event (set lazily on first WS connect)
_loop: asyncio.AbstractEventLoop | None = None
_notify: asyncio.Event | None = None

# Shutdown / watchdog state
_shutdown_scheduled = False
_had_connections = False
_last_connection_time: float = 0.0
_WATCHDOG_INTERVAL = 10  # seconds between polls
_WATCHDOG_TIMEOUT = 300  # 5 minutes idle → soft suspend
_WATCHDOG_HARD_KILL_TIMEOUT = 3300  # 55 min more → hard kill (total: 60 min from last connection)
_HARD_KILL = _WATCHDOG_HARD_KILL_TIMEOUT  # short alias for the event payloads below
_server_suspended = False
_session_orphaned = False
_ACTIVE_STATES = ("running", "paused", "uploading")
# Orphan reaper: no session manager on the app (AttributeError), or destroy_session failing.
_REAP_ERRORS: tuple[type[Exception], ...] = (AttributeError, *KERNEL_ERRORS)
# Snapshot on connect: no session manager on the app (AttributeError), or the client already left.
_SNAPSHOT_ERRORS: tuple[type[Exception], ...] = (AttributeError, *WS_SEND_ERRORS)


def _diag(evt: str, msg: str, lvl: str = "info", session_id: str | None = None, **fields: str | int | None) -> None:
    """Emit a structured API-tier record from this module."""
    _diag_log(evt, tier=_TIER_API, lvl=lvl, msg=msg, src="reacher.api.websocket", session_id=session_id, **fields)


def total_connections() -> int:
    """Return the total number of active WebSocket connections across all sessions."""
    return sum(len(ws_set) for ws_set in _connections.values())


def dropped_events() -> int:
    """Return the total number of events dropped due to a full queue."""
    with _dropped_events_lock:
        return _dropped_events


def _trigger_shutdown() -> None:
    """Send SIGINT to self to initiate uvicorn's graceful shutdown. Idempotent."""
    global _shutdown_scheduled
    if _shutdown_scheduled:
        return
    _shutdown_scheduled = True
    logger.info("Triggering graceful shutdown (SIGINT to self)")
    # Without this the watchdog's self-SIGINT looks like a spontaneous death.
    _diag("ws.watchdog_shutdown", "Watchdog triggering graceful shutdown (SIGINT to self)", "warn")
    os.kill(os.getpid(), signal.SIGINT)


def _trigger_suspend() -> None:
    """Soft-suspend: broadcast timeout signal to connected clients; keep process alive."""
    global _server_suspended
    if _server_suspended:
        return
    _server_suspended = True
    logger.info("Watchdog: soft-suspending (no connections for %ds)", _WATCHDOG_TIMEOUT)
    _diag("ws.suspend", f"Soft-suspending after {_WATCHDOG_TIMEOUT}s idle", "warn", idle_s=_WATCHDOG_TIMEOUT)
    for sid in list(_connections.keys()):
        enqueue_event(sid, "server_suspended", {"reason": "watchdog_timeout", "hard_kill_in": _HARD_KILL})


def _notify_orphaned() -> None:
    """Notify clients that a session has been orphaned without marking the server as suspended.

    Sent when an active session has had no WS clients for _SESSION_ORPHAN_TIMEOUT_ACTIVE
    seconds. Unlike _trigger_suspend(), this does NOT set _server_suspended, so the process
    remains fully alive and clients can reconnect without a page reload.
    """
    global _session_orphaned
    if _session_orphaned:
        return
    _session_orphaned = True
    logger.info(
        "Watchdog: session orphaned (no connections for %ds with active session) — notifying clients",
        _SESSION_ORPHAN_TIMEOUT_ACTIVE,
    )
    for sid in list(_connections.keys()):
        enqueue_event(sid, "session_orphaned", {"reason": "no_clients", "hard_kill_in": _HARD_KILL})


def is_suspended() -> bool:
    """Return True if the server is in the soft-suspended state."""
    return _server_suspended


def had_connections() -> bool:
    """Return True if at least one WS client has ever connected since process start."""
    return _had_connections


def _any_session_active() -> bool:
    """Return True if any session is in 'running', 'paused' or 'uploading' state.

    Guards against mid-acquisition and mid-flash shutdown. Prevents watchdog and shutdown beacon from killing the process while an
    experiment is recording OR while avrdude is flashing firmware.
    """
    if _app_ref is None:
        return False
    try:
        sm: SessionManager = _app_ref.state.session_manager
        return any(info.state in _ACTIVE_STATES for info in sm._sessions.values())
    except (AttributeError, RuntimeError):
        # AttributeError: no session manager on the app yet.
        # RuntimeError: another thread resized the session dict mid-iteration.
        logger.debug("Active-session check failed; treating as inactive", exc_info=True)
        return False


async def _watchdog() -> None:
    """Poll for 0 connections; auto-shutdown if no clients for WATCHDOG_TIMEOUT seconds.

    Defers shutdown while any session is running or paused, using the
    orphan cleanup's extended timeout (600s) instead of the default 120s.
    """
    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL)
        if total_connections() == 0 and _had_connections:
            idle = time.monotonic() - _last_connection_time
            if _any_session_active():
                if idle >= _SESSION_ORPHAN_TIMEOUT_ACTIVE:
                    if not _session_orphaned:
                        logger.info("Watchdog: no connections for %.0fs with active sessions — notifying orphan", idle)
                        _notify_orphaned()
                    elif idle >= _SESSION_ORPHAN_TIMEOUT_ACTIVE + _WATCHDOG_HARD_KILL_TIMEOUT:
                        logger.info("Watchdog: hard-kill timeout reached with active sessions — shutting down")
                        _trigger_shutdown()
                        return
                elif idle >= _WATCHDOG_TIMEOUT:
                    logger.info("Watchdog: no connections for %.0fs but sessions still active — deferring", idle)
            elif idle >= _WATCHDOG_TIMEOUT:
                if pairing.is_active_pairing():
                    logger.info("Watchdog: no connections for %.0fs but device is actively paired — deferring", idle)
                elif not _server_suspended:
                    logger.info("Watchdog: no connections for %.0fs — suspending", idle)
                    _trigger_suspend()
                elif idle >= _WATCHDOG_TIMEOUT + _WATCHDOG_HARD_KILL_TIMEOUT:
                    logger.info("Watchdog: hard-kill timeout reached — shutting down")
                    _trigger_shutdown()
                    return


def enqueue_event(session_id: str, event_type: str, data: EventData) -> None:
    """Thread-safe: push an event into the queue.

    Called from REACHER background threads via the event_callback.
    Uses loop.call_soon_threadsafe to wake the async broadcast worker.
    Drops oldest event if the queue is full to prevent unbounded memory growth.
    """
    msg: _Event = {"type": event_type, "session_id": session_id, "data": data}
    global _dropped_events
    try:
        _event_queue.put_nowait(msg)
    except queue.Full:
        with _dropped_events_lock:
            _dropped_events += 1
        # Drop oldest to make room
        try:
            _event_queue.get_nowait()
        except queue.Empty:
            logger.debug("Event queue emptied by another thread while making room", exc_info=True)
        try:
            _event_queue.put_nowait(msg)
        except queue.Full:
            logger.debug("Event queue refilled by another thread; dropping event", exc_info=True)
    if _loop is not None and _notify is not None:
        _loop.call_soon_threadsafe(_notify.set)


async def _send_to_session(session_id: str, payload: str) -> None:
    """Send *payload* to every client of *session_id*, forgetting the ones that have gone away."""
    dead: set[WebSocket] = set()
    for client in list(_connections.get(session_id, ())):
        try:
            await client.send_text(payload)
        except WS_SEND_ERRORS:
            dead.add(client)
    if dead and session_id in _connections:
        _connections[session_id] -= dead


async def _broadcast_worker() -> None:
    """Background task that drains the event queue and sends to connected clients.

    Periodically checks for dropped events and broadcasts a warning
    to all connected clients so they know data may be missing.
    """
    global _notify
    _notify = asyncio.Event()
    _last_warned_drops: int = 0

    while True:
        await _notify.wait()
        _notify.clear()

        # Drain all pending events from the thread-safe queue
        while True:
            try:
                msg = _event_queue.get_nowait()
            except queue.Empty:
                break

            session_id = msg["session_id"]
            payload = json.dumps(msg)

            # A fanout with 0 subscribers means the event
            # is dropped on the floor — the signature of the proxy late-connect
            # race. Gated behind DEBUG so it is silent in normal operation.
            subscribers = len(_connections.get(session_id, ()))
            logger.debug("WS fanout sid=%s type=%s subscribers=%d", session_id, msg["type"], subscribers)
            await _send_to_session(session_id, payload)

        # Warn all connected clients when events are being dropped
        current_drops = dropped_events()
        if current_drops > _last_warned_drops:
            overflow = f"WebSocket event queue overflow: {current_drops} dropped"
            since = current_drops - _last_warned_drops
            _diag("ws.events_dropped", overflow, "warn", dropped=current_drops, since_last=since)
            _last_warned_drops = current_drops
            warning = {"message": "Events are being dropped due to queue overflow", "dropped_count": current_drops}
            warning_msg = json.dumps({"type": "warning", "data": warning})
            for session_id in list(_connections):
                await _send_to_session(session_id, warning_msg)


# Start the broadcast worker as a background task
_broadcast_task: asyncio.Task[None] | None = None
_watchdog_task: asyncio.Task[None] | None = None


def _log_task_exit(task: asyncio.Task[None]) -> None:
    """Log a background task that ended on an unexpected error; a cancel at shutdown is normal."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %s ended on an unexpected error", task.get_name(), exc_info=exc)


def _ensure_broadcast_worker() -> None:
    """Start the broadcast worker and the watchdog on the running loop if either has stopped."""
    global _broadcast_task, _watchdog_task, _loop
    loop = asyncio.get_running_loop()
    if _broadcast_task is None or _broadcast_task.done():
        _loop = loop
        _broadcast_task = loop.create_task(_broadcast_worker(), name="ws-broadcast")
        _broadcast_task.add_done_callback(_log_task_exit)
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = loop.create_task(_watchdog(), name="ws-watchdog")
        _watchdog_task.add_done_callback(_log_task_exit)


# Per-session last-disconnect timestamps for orphan cleanup
_session_disconnect_times: dict[str, float] = {}
_SESSION_ORPHAN_TIMEOUT = 60  # seconds with 0 WS clients before destroying a session
_SESSION_ORPHAN_TIMEOUT_ACTIVE = 600  # extended timeout for running/paused/uploading sessions
# A "stopped" session still holds data the user has not
# exported yet. destroy_session() drops the REACHER instance the export
# endpoints read from, so reaping it on the short default timeout turns a
# closed laptop lid or a flaky network into permanent, silent data loss.
# This window only applies while unexported (SessionManager.mark_exported
# clears it back to _SESSION_ORPHAN_TIMEOUT once the export endpoint
# succeeds), so a session that *is* exported is still reaped quickly, and a
# session that never gets exported is still bounded — just generously, since
# the alternative is destroying data the user came back for.
_SESSION_ORPHAN_TIMEOUT_STOPPED_UNEXPORTED = 86400  # 24h
_orphan_task: asyncio.Task[None] | None = None


def _orphan_timeout(sid: str) -> int:
    """Return how long session *sid* may stay without WebSocket clients before it is destroyed."""
    if _app_ref is None:
        return _SESSION_ORPHAN_TIMEOUT
    try:
        info = _app_ref.state.session_manager._sessions.get(sid)
    except AttributeError:
        logger.debug("Orphan timeout lookup failed for %s", sid, exc_info=True)
        return _SESSION_ORPHAN_TIMEOUT
    if info is None:
        return _SESSION_ORPHAN_TIMEOUT
    if info.state in _ACTIVE_STATES:
        return _SESSION_ORPHAN_TIMEOUT_ACTIVE
    if info.state == "stopped" and not info.exported:
        return _SESSION_ORPHAN_TIMEOUT_STOPPED_UNEXPORTED
    if info.state == "disconnected" and info.instance.program_running:
        # A serial drop mid-run holds unexported data a client may still come back for.
        return _SESSION_ORPHAN_TIMEOUT_ACTIVE
    return _SESSION_ORPHAN_TIMEOUT


async def _destroy_orphan(sid: str) -> None:
    """Destroy session *sid* off the event loop and drop its rate-limit state."""
    from .hardware import release_session  # imported here: hardware imports this module

    if _app_ref is None:
        return
    try:
        sm: SessionManager = _app_ref.state.session_manager
        # destroy_session is blocking; run off the event loop
        await asyncio.get_running_loop().run_in_executor(None, sm.destroy_session, sid)
        release_session(sid)
        logger.info("Orphan cleanup: destroyed session %s", sid)
    except _REAP_ERRORS:
        # AttributeError: no session manager on the app.
        logger.debug("Orphan cleanup failed for %s", sid, exc_info=True)


async def _orphan_cleanup() -> None:
    """Periodically destroy sessions that have had no WebSocket clients for ORPHAN_TIMEOUT."""
    while True:
        await asyncio.sleep(15)
        now = time.monotonic()
        orphans = [
            sid
            for sid, ts in list(_session_disconnect_times.items())
            if sid not in _connections and (now - ts) >= _orphan_timeout(sid)
        ]
        for sid in orphans:
            _session_disconnect_times.pop(sid, None)
            if _loop is not None:
                await _destroy_orphan(sid)


_app_ref: FastAPI | None = None


async def _send_state_snapshot(websocket: WebSocket, session_id: str) -> None:
    """Send the live state of *session_id* to a client that has just connected.

    Transitions are broadcast as they happen, so a client that connects after one never sees
    it. In proxy mode the relay always connects late, leaving the browser session "idle" and
    every later event dropped; replaying the live state on connect fixes that.
    """
    try:
        sm: SessionManager = websocket.app.state.session_manager
        info = sm._sessions.get(session_id)
        if info is not None:
            snapshot: _Event = {"type": "session_state", "session_id": session_id, "data": {"state": info.state}}
            await websocket.send_text(json.dumps(snapshot))
    except _SNAPSHOT_ERRORS:
        # AttributeError: the app has no session manager; the send errors: the client already left.
        logger.debug("session_state snapshot on connect failed for %s", session_id, exc_info=True)


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """Stream session events to one client and answer its keep-alive pings."""
    global _had_connections, _last_connection_time, _app_ref, _orphan_task, _server_suspended, _session_orphaned

    if not verify_ws_token(websocket):
        client = getattr(websocket.client, "host", None)
        _diag("ws.auth_failed", f"Rejected WebSocket for {session_id}: bad token", "warn", session_id, client=client)
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await websocket.accept()
    _connections[session_id].add(websocket)
    _had_connections = True
    _last_connection_time = time.monotonic()
    _session_disconnect_times.pop(session_id, None)
    _app_ref = websocket.app
    if _server_suspended:
        _server_suspended = False
        logger.info("WS reconnect from %s — clearing suspended state", session_id)
    if _session_orphaned:
        _session_orphaned = False
        logger.info("WS reconnect from %s — clearing orphaned state", session_id)
    _ensure_broadcast_worker()
    if _orphan_task is None or _orphan_task.done():
        _orphan_task = asyncio.get_running_loop().create_task(_orphan_cleanup(), name="ws-orphan-reaper")
        _orphan_task.add_done_callback(_log_task_exit)
    logger.info("WebSocket connected for session %s", session_id)
    client = getattr(websocket.client, "host", None)
    conns = total_connections()
    _diag(
        "ws.connect",
        f"WebSocket connected for session {session_id}",
        session_id=session_id,
        client=client,
        connections=conns,
    )

    try:
        await _send_state_snapshot(websocket, session_id)
        while True:
            # Keep the connection alive; validate input
            data = await websocket.receive_text()
            if len(data) > 1024:
                logger.warning("WS message too large from %s (%d bytes), discarding", session_id, len(data))
                continue
            try:
                parsed = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                logger.warning("WS non-JSON from %s, discarding", session_id)
                continue
            if not isinstance(parsed, dict) or parsed.get("type") != "ping":
                logger.debug("WS unknown message type from %s: %s", session_id, parsed.get("type"))
                continue
            logger.debug("WS ping from %s", session_id)
            await websocket.send_text(json.dumps({"type": "pong", "session_id": session_id}))
    except WebSocketDisconnect:
        pass
    finally:
        _connections[session_id].discard(websocket)
        if not _connections[session_id]:
            del _connections[session_id]
            _session_disconnect_times[session_id] = time.monotonic()
        _last_connection_time = time.monotonic()
        logger.info("WebSocket disconnected for session %s", session_id)
        conns = total_connections()
        _diag("ws.disconnect", f"WebSocket disconnected for {session_id}", session_id=session_id, connections=conns)
