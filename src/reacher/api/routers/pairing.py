"""Pairing code claim endpoint for zero-config machine setup.

No Bearer authentication is required — the pairing code itself serves as the
credential.  This endpoint is called server-to-server (local REACHER →
remote REACHER), not from the browser, so CORS is irrelevant.

Rate limited to 5 attempts per IP per 60 seconds to prevent brute-forcing
the 6-digit code space.
"""

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ...api.middleware.auth import API_KEY, require_api_key
from ... import pairing

router = APIRouter()
logger = logging.getLogger(__name__)

# Sliding-window rate limiter: 5 attempts / IP / 60 s
_RATE_LIMIT = 5
_RATE_WINDOW = 60.0
_attempt_timestamps: dict[str, deque] = defaultdict(deque)


class ClaimRequest(BaseModel):
    code: str


@router.post("/claim")
async def claim_pairing_code(body: ClaimRequest, request: Request) -> dict:
    """Exchange a pairing code for this device's API key.

    Called by a remote REACHER server's /api/discovery/{id}/pair handler
    when a user enters the pairing code shown in this device's terminal.
    """
    if pairing.is_paired():
        raise HTTPException(status_code=409, detail="Device is already paired")

    client_ip = request.client.host if request.client else "unknown"

    # Sliding-window rate limit per source IP
    now = time.monotonic()
    window = _attempt_timestamps[client_ip]
    while window and window[0] <= now - _RATE_WINDOW:
        window.popleft()
    if len(window) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many pairing attempts — try again shortly")
    window.append(now)

    if not pairing.verify_code(body.code):
        logger.warning("Failed pairing attempt from %s", client_ip)
        raise HTTPException(status_code=401, detail="Invalid or expired pairing code")

    pairing.set_paired()
    logger.info("Pairing code accepted from %s — API key returned", client_ip)
    return {"api_key": API_KEY}


@router.post("/unpair", dependencies=[Depends(require_api_key)])
async def unpair(request: Request) -> dict:
    """Clear the paired state, allowing new pairing attempts.

    Requires Bearer authentication — only the paired controller (which holds
    the API key) can unpair this device.
    """
    if not pairing.is_paired():
        raise HTTPException(status_code=409, detail="Device is not currently paired")
    pairing.set_unpaired()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("Device unpaired by %s", client_ip)
    return {"status": "unpaired"}
