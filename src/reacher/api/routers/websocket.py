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
_WATCHDOG_TIMEOUT = 120  # seconds with 0 connections before auto-shutdown


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


async def _watchdog():
    """Poll for 0 connections; auto-shutdown if no clients for WATCHDOG_TIMEOUT seconds."""
    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL)
        if total_connections() == 0 and _had_connections:
            idle = time.monotonic() - _last_connection_time
            if idle >= _WATCHDOG_TIMEOUT:
                logger.info("Watchdog: no connections for %.0fs — shutting down", idle)
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
    """Background task that drains the event queue and sends to connected clients."""
    global _notify
    _notify = asyncio.Event()

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

            dead: Set[WebSocket] = set()
            for ws in _connections.get(session_id, set()):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)

            # Clean up disconnected clients
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
    _ensure_broadcast_worker()
    if _orphan_task is None or _orphan_task.done():
        _orphan_task = asyncio.get_running_loop().create_task(_orphan_cleanup())
    logger.info("WebSocket connected for session %s", session_id)

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
    except WebSocketDisconnect:
        pass
    finally:
        _connections[session_id].discard(websocket)
        if not _connections[session_id]:
            del _connections[session_id]
            _session_disconnect_times[session_id] = time.monotonic()
        _last_connection_time = time.monotonic()
        logger.info("WebSocket disconnected for session %s", session_id)
