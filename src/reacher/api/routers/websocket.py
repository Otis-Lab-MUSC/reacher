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
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..middleware.auth import verify_ws_token
from reacher import pairing

router = APIRouter()
logger = logging.getLogger(__name__)

# Per-session connected WebSocket clients
_connections: Dict[str, Set[WebSocket]] = defaultdict(set)

# Thread-safe stdlib queue (safe to call from background REACHER threads)
_EVENT_QUEUE_MAX = 10000
_event_queue: queue.Queue = queue.Queue(maxsize=_EVENT_QUEUE_MAX)

# Fix: PY-004 — Thread-safe dropped event counter
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
_server_suspended = False
_session_orphaned = False


def total_connections() -> int:
    """Return the total number of active WebSocket connections across all sessions."""
    return sum(len(ws_set) for ws_set in _connections.values())


def dropped_events() -> int:
    """Return the total number of events dropped due to a full queue."""
    with _dropped_events_lock:
        return _dropped_events


def _trigger_shutdown():
    """Send SIGINT to self to initiate uvicorn's graceful shutdown. Idempotent."""
    global _shutdown_scheduled
    if _shutdown_scheduled:
        return
    _shutdown_scheduled = True
    logger.info("Triggering graceful shutdown (SIGINT to self)")
    os.kill(os.getpid(), signal.SIGINT)


def _trigger_suspend():
    """Soft-suspend: broadcast timeout signal to connected clients; keep process alive."""
    global _server_suspended
    if _server_suspended:
        return
    _server_suspended = True
    logger.info("Watchdog: soft-suspending (no connections for %ds)", _WATCHDOG_TIMEOUT)
    for sid in list(_connections.keys()):
        enqueue_event(sid, "server_suspended", {
            "reason": "watchdog_timeout",
            "hard_kill_in": _WATCHDOG_HARD_KILL_TIMEOUT,
        })


def _notify_orphaned():
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
        enqueue_event(sid, "session_orphaned", {
            "reason": "no_clients",
            "hard_kill_in": _WATCHDOG_HARD_KILL_TIMEOUT,
        })


def is_suspended() -> bool:
    """Return True if the server is in the soft-suspended state."""
    return _server_suspended


def had_connections() -> bool:
    """Return True if at least one WS client has ever connected since process start."""
    return _had_connections


def _any_session_active() -> bool:
    """Return True if any session is in 'running', 'paused', or 'uploading' state.

    Fix: F-001 (#30) — mid-acquisition guard; F-002 (#36) — mid-flash guard.
    Prevents watchdog and shutdown beacon from killing the process while an
    experiment is recording OR while avrdude is flashing firmware.
    """
    if _app_ref is None:
        return False
    try:
        sm = _app_ref.state.session_manager
        for info in sm._sessions.values():
            if info.state in ("running", "paused", "uploading"):
                return True
    except Exception:
        pass
    return False


async def _watchdog():
    """Poll for 0 connections; auto-shutdown if no clients for WATCHDOG_TIMEOUT seconds.

    Fix: F-001 — Defers shutdown while any session is running or paused, using the
    orphan cleanup's extended timeout (600s) instead of the default 120s.
    """
    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL)
        if total_connections() == 0 and _had_connections:
            idle = time.monotonic() - _last_connection_time
            if _any_session_active():
                if idle >= _SESSION_ORPHAN_TIMEOUT_ACTIVE:
                    if not _session_orphaned:
                        logger.info(
                            "Watchdog: no connections for %.0fs with active sessions — notifying orphan", idle
                        )
                        _notify_orphaned()
                    elif idle >= _SESSION_ORPHAN_TIMEOUT_ACTIVE + _WATCHDOG_HARD_KILL_TIMEOUT:
                        logger.info(
                            "Watchdog: hard-kill timeout reached with active sessions — shutting down",
                        )
                        _trigger_shutdown()
                        return
                elif idle >= _WATCHDOG_TIMEOUT:
                    logger.info(
                        "Watchdog: no connections for %.0fs but sessions still active — deferring",
                        idle,
                    )
            elif idle >= _WATCHDOG_TIMEOUT:
                if pairing.is_active_pairing():
                    logger.info(
                        "Watchdog: no connections for %.0fs but device is actively paired — deferring",
                        idle,
                    )
                elif not _server_suspended:
                    logger.info("Watchdog: no connections for %.0fs — suspending", idle)
                    _trigger_suspend()
                elif idle >= _WATCHDOG_TIMEOUT + _WATCHDOG_HARD_KILL_TIMEOUT:
                    logger.info("Watchdog: hard-kill timeout reached — shutting down")
                    _trigger_shutdown()
                    return


def enqueue_event(session_id: str, event_type: str, data: dict):
    """Thread-safe: push an event into the queue.

    Called from REACHER background threads via the event_callback.
    Uses loop.call_soon_threadsafe to wake the async broadcast worker.
    Drops oldest event if the queue is full to prevent unbounded memory growth.
    """
    msg = {
        "type": event_type,
        "session_id": session_id,
        "data": data,
    }
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
            pass
        try:
            _event_queue.put_nowait(msg)
        except queue.Full:
            pass
    if _loop is not None and _notify is not None:
        _loop.call_soon_threadsafe(_notify.set)


async def _broadcast_worker():
    """Background task that drains the event queue and sends to connected clients.

    Fix: F-004 — Periodically checks for dropped events and broadcasts a warning
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

            session_id = msg.get("session_id", "")
            payload = json.dumps(msg)

            # Fix #15 (diagnostic): a fanout with 0 subscribers means the event
            # is dropped on the floor — the signature of the proxy late-connect
            # race. Gated behind DEBUG so it is silent in normal operation.
            logger.debug(
                "WS fanout sid=%s type=%s subscribers=%d",
                session_id, msg.get("type"), len(_connections.get(session_id, set())),
            )

            dead: Set[WebSocket] = set()
            for ws in _connections.get(session_id, set()):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)

            # Clean up disconnected clients
            if dead:
                _connections[session_id] -= dead

        # Fix: F-004 — Warn all connected clients when events are being dropped
        current_drops = dropped_events()
        if current_drops > _last_warned_drops:
            _last_warned_drops = current_drops
            warning_msg = json.dumps({
                "type": "warning",
                "data": {
                    "message": "Events are being dropped due to queue overflow",
                    "dropped_count": current_drops,
                },
            })
            for session_id, ws_set in _connections.items():
                dead: Set[WebSocket] = set()
                for ws in ws_set:
                    try:
                        await ws.send_text(warning_msg)
                    except Exception:
                        dead.add(ws)
                if dead:
                    _connections[session_id] -= dead


# Start the broadcast worker as a background task
_broadcast_task = None
_watchdog_task = None


def _ensure_broadcast_worker():
    global _broadcast_task, _watchdog_task, _loop
    if _broadcast_task is None or _broadcast_task.done():
        _loop = asyncio.get_running_loop()
        _broadcast_task = _loop.create_task(_broadcast_worker())
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = _loop.create_task(_watchdog())


# Per-session last-disconnect timestamps for orphan cleanup
_session_disconnect_times: Dict[str, float] = {}
_SESSION_ORPHAN_TIMEOUT = 60  # seconds with 0 WS clients before destroying a session
_SESSION_ORPHAN_TIMEOUT_ACTIVE = 600  # extended timeout for running/paused sessions
_orphan_task = None


async def _orphan_cleanup():
    """Periodically destroy sessions that have had no WebSocket clients for ORPHAN_TIMEOUT."""
    while True:
        await asyncio.sleep(15)
        now = time.monotonic()
        orphans = []
        for sid, ts in list(_session_disconnect_times.items()):
            if sid in _connections:
                continue
            # Use extended timeout for running/paused sessions
            timeout = _SESSION_ORPHAN_TIMEOUT
            if _app_ref is not None:
                try:
                    sm = _app_ref.state.session_manager
                    info = sm._sessions.get(sid)
                    if info and info.state in ("running", "paused"):
                        timeout = _SESSION_ORPHAN_TIMEOUT_ACTIVE
                except Exception:
                    pass
            if (now - ts) >= timeout:
                orphans.append(sid)
        for sid in orphans:
            _session_disconnect_times.pop(sid, None)
            # Access session manager via the app state if available
            if _loop is not None:
                try:
                    from ...session_manager import SessionManager
                    from .hardware import release_session
                    # The app reference is stored in the websocket router's state
                    # We'll use the global _app_ref set during first WS connect
                    if _app_ref is not None:
                        sm: SessionManager = _app_ref.state.session_manager
                        # Fix: F-001 — destroy_session is blocking; run off the event loop
                        await asyncio.get_event_loop().run_in_executor(None, sm.destroy_session, sid)
                        # Fix: F-008 — clean up rate-limit timestamps
                        release_session(sid)
                        logger.info("Orphan cleanup: destroyed session %s", sid)
                except Exception:
                    logger.debug("Orphan cleanup failed for %s", sid, exc_info=True)


_app_ref = None


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    global _had_connections, _last_connection_time, _app_ref, _orphan_task

    if not verify_ws_token(websocket):
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await websocket.accept()
    _connections[session_id].add(websocket)
    _had_connections = True
    _last_connection_time = time.monotonic()
    _session_disconnect_times.pop(session_id, None)
    _app_ref = websocket.app
    global _server_suspended, _session_orphaned
    if _server_suspended:
        _server_suspended = False
        logger.info("WS reconnect from %s — clearing suspended state", session_id)
    if _session_orphaned:
        _session_orphaned = False
        logger.info("WS reconnect from %s — clearing orphaned state", session_id)
    _ensure_broadcast_worker()
    if _orphan_task is None or _orphan_task.done():
        _orphan_task = asyncio.get_running_loop().create_task(_orphan_cleanup())
    logger.info("WebSocket connected for session %s", session_id)

    # Fix #15: snapshot the current session state to this just-connected client.
    # State transitions (idle->uploading->connected->running) are broadcast as
    # they happen; a client that connects *after* a transition never sees it. In
    # proxy/IoT mode the WS relay always connects late (after an async ws-token
    # fetch), so the Pi fans those transitions to an empty client set and the
    # browser session stays "idle" — pushEvent then silently drops every event.
    # Replaying the live state on connect lets any client (relay, direct browser,
    # or a reconnect) immediately learn the real state.
    try:
        sm = websocket.app.state.session_manager
        info = sm._sessions.get(session_id)
        if info is not None:
            await websocket.send_text(json.dumps({
                "type": "session_state",
                "session_id": session_id,
                "data": {"state": info.state},
            }))
    except Exception:
        logger.debug("session_state snapshot on connect failed for %s", session_id, exc_info=True)

    try:
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
