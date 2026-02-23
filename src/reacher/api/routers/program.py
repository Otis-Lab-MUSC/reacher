"""Program control endpoints (start/stop/pause/limits)."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)

# Reasonable upper bounds for a lab experiment
MAX_TIME_LIMIT = 86400  # 24 hours in seconds
MAX_INFUSION_LIMIT = 10000
MAX_DELAY = 86400


class LimitRequest(BaseModel):
    type: str  # "Time", "Infusion", "Both", "Trials"
    time_limit: Optional[int] = None
    infusion_limit: Optional[int] = None
    delay: Optional[int] = None

    @field_validator("time_limit")
    @classmethod
    def validate_time_limit(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v <= 0 or v > MAX_TIME_LIMIT):
            raise ValueError(f"time_limit must be between 1 and {MAX_TIME_LIMIT}")
        return v

    @field_validator("infusion_limit")
    @classmethod
    def validate_infusion_limit(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v <= 0 or v > MAX_INFUSION_LIMIT):
            raise ValueError(f"infusion_limit must be between 1 and {MAX_INFUSION_LIMIT}")
        return v

    @field_validator("delay")
    @classmethod
    def validate_delay(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > MAX_DELAY):
            raise ValueError(f"delay must be between 0 and {MAX_DELAY}")
        return v


@router.post("/{session_id}/start")
async def start_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        info.instance.start_program()
    except Exception:
        logger.error("start_program failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start program")

    sm.set_state(session_id, "running")
    return {"status": "started"}


@router.post("/{session_id}/stop")
async def stop_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        info.instance.stop_program()
    except Exception:
        logger.error("stop_program failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to stop program")

    return {"status": "stopped"}


@router.post("/{session_id}/pause")
async def pause_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = info.instance
    if instance.get_program_running():
        instance.pause_program()
        sm.set_state(session_id, "paused")
        return {"status": "paused"}
    else:
        instance.resume_program()
        sm.set_state(session_id, "running")
        return {"status": "resumed"}


@router.post("/{session_id}/limit")
async def set_limit(session_id: str, body: LimitRequest, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = info.instance
    if body.type not in ("Time", "Infusion", "Both", "Trials"):
        raise HTTPException(status_code=400, detail=f"Invalid limit type: {body.type}")

    instance.set_limit_type(body.type)
    if body.infusion_limit is not None:
        instance.set_infusion_limit(body.infusion_limit)
    if body.time_limit is not None:
        instance.set_time_limit(body.time_limit)
    if body.delay is not None:
        instance.set_stop_delay(body.delay)

    return {"status": "limits_set", "type": body.type}
