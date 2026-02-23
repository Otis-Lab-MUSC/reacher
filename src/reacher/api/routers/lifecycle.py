"""Lifecycle endpoints for graceful app shutdown."""

import asyncio
import logging

from fastapi import APIRouter, Response

from .websocket import total_connections, _trigger_shutdown

router = APIRouter()
logger = logging.getLogger(__name__)

_SHUTDOWN_GRACE_PERIOD = 30  # seconds to wait for reconnection (e.g. F5 refresh)


@router.post("/shutdown")
async def shutdown(response: Response):
    """Signal that the browser tab is closing.

    Spawns a delayed task that waits for the grace period, then checks if any
    WebSocket clients have reconnected.  If none have, triggers process shutdown.
    Compatible with ``navigator.sendBeacon()`` (no request body required).
    """
    logger.info("Shutdown beacon received — waiting %ds grace period", _SHUTDOWN_GRACE_PERIOD)
    response.status_code = 202

    async def _delayed_shutdown():
        await asyncio.sleep(_SHUTDOWN_GRACE_PERIOD)
        if total_connections() == 0:
            logger.info("Grace period elapsed with 0 connections — shutting down")
            _trigger_shutdown()
        else:
            logger.info("Connections re-established during grace period — shutdown cancelled")

    asyncio.get_running_loop().create_task(_delayed_shutdown())
    return {"status": "shutdown_scheduled"}
