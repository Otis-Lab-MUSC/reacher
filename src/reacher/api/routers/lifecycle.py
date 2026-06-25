"""Lifecycle endpoints for graceful app shutdown."""

import asyncio
import logging

from fastapi import APIRouter, Response

from .websocket import (
    total_connections,
    _trigger_shutdown,
    is_suspended,
    had_connections,
    _any_session_active,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_SHUTDOWN_GRACE_PERIOD = 30  # seconds to wait for reconnection (e.g. F5 refresh)


async def _delayed_shutdown():
    """Wait the grace period, then shut down only if it is safe to do so.

    Module-level (not a closure) so the safety guards can be unit-tested directly,
    mirroring the watchdog (``websocket._watchdog``).
    """
    await asyncio.sleep(_SHUTDOWN_GRACE_PERIOD)
    if not had_connections():
        logger.info("Shutdown beacon: no WS connections ever seen — ignoring (pre-serial state)")
        return
    if is_suspended():
        logger.info("Shutdown beacon: server already suspended — hard-kill timer owns shutdown")
        return
    if _any_session_active():
        # Mirror the watchdog guard (Fix F-001): never let the headerless,
        # auth-free beacon kill the process while a session is recording —
        # even if its WS client is momentarily orphaned (total_connections() == 0).
        logger.info("Shutdown beacon: session running/paused — refusing shutdown (mid-acquisition guard)")
        return
    if total_connections() == 0:
        logger.info("Grace period elapsed with 0 connections — shutting down")
        _trigger_shutdown()
    else:
        logger.info("Connections re-established during grace period — shutdown cancelled")


@router.post("/shutdown")
async def shutdown(response: Response):
    """Signal that the browser tab is closing.

    Spawns a delayed task that waits for the grace period, then checks if any
    WebSocket clients have reconnected.  If none have, triggers process shutdown.
    Compatible with ``navigator.sendBeacon()`` (no request body required).
    """
    logger.info("Shutdown beacon received — waiting %ds grace period", _SHUTDOWN_GRACE_PERIOD)
    response.status_code = 202
    asyncio.get_running_loop().create_task(_delayed_shutdown())
    return {"status": "shutdown_scheduled"}
