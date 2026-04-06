"""Generic hardware command dispatch endpoint."""

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from ...kernel.commands import COMMAND_REGISTRY, get_commands_for_paradigm

router = APIRouter()
logger = logging.getLogger(__name__)

# Sliding-window rate limiter: max 20 commands/second per session
_RATE_LIMIT = 20
_RATE_WINDOW = 1.0  # seconds
_command_timestamps: dict[str, deque] = defaultdict(deque)

# Hardware-safe value ranges per payload_key
_VALUE_RANGES = {
    "frequency": (1, 65535),       # Hz — avoid 0 (division by zero in firmware)
    "duration":  (1, 600000),      # ms — up to 10 minutes
    "timeout":   (0, 600000),      # ms — 0 disables timeout
    "ratio":     (1, 255),         # uint8_t on firmware side
    "step":      (1, 255),
    "interval":  (0, 600000),      # ms
    "pulse_on":  (0, 60000),        # ms — 0 = continuous
    "pulse_off": (0, 60000),        # ms
    "probability": (0, 100),       # percentage
    "count":     (0, 128),         # MAX_PAVLOV_TRIALS
    "iti_mean":  (1, 600000),
    "iti_min":   (0, 600000),
    "iti_max":   (1, 600000),
}


class CommandRequest(BaseModel):
    code: int
    value: Optional[int] = None


@router.post("/{session_id}/command")
async def send_command(session_id: str, body: CommandRequest, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    # Sliding-window rate limit
    now = time.monotonic()
    window = _command_timestamps[session_id]
    while window and window[0] <= now - _RATE_WINDOW:
        window.popleft()
    if len(window) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded (20 commands/second)")
    window.append(now)

    if body.code not in COMMAND_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown command code: {body.code}")

    spec = COMMAND_REGISTRY[body.code]

    if spec.deprecated:
        raise HTTPException(status_code=400, detail=f"Command {spec.name} is deprecated")
    if info.paradigm and spec.paradigms and info.paradigm.lower() not in spec.paradigms:
        raise HTTPException(
            status_code=400,
            detail=f"Command {spec.name} not available for {info.paradigm} paradigm",
        )

    # Validate value against hardware-safe ranges
    if body.value is not None and spec.payload_key is not None:
        bounds = _VALUE_RANGES.get(spec.payload_key)
        if bounds is not None:
            lo, hi = bounds
            if not (lo <= body.value <= hi):
                raise HTTPException(
                    status_code=400,
                    detail=f"{spec.payload_key} must be between {lo} and {hi}",
                )

    try:
        info.instance.send_command(body.code, body.value)
    except Exception:
        logger.error("Command %s failed", spec.name, exc_info=True)
        raise HTTPException(status_code=500, detail="Command failed")

    return {"status": "sent", "command": spec.name, "code": body.code}


@router.get("/{session_id}/commands")
async def get_commands(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    paradigm = info.paradigm or "fr"
    cmds = get_commands_for_paradigm(paradigm)
    return {
        "paradigm": paradigm,
        "commands": [
            {
                "code": spec.code,
                "name": spec.name,
                "description": spec.description,
                "payload_key": spec.payload_key,
                "payload_type": spec.payload_type,
            }
            for spec in cmds.values()
        ],
    }


def release_session(session_id: str) -> None:
    """Fix: F-008 — Remove rate-limit state for a destroyed session to prevent memory leak."""
    _command_timestamps.pop(session_id, None)


@router.get("/{session_id}/config")
async def get_config(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "firmware_info": info.instance.get_firmware_information(),
        "hardware_settings": info.instance.get_hardware_settings(),
    }
