"""Program control endpoints (start/stop/pause/limits)."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator, model_validator
from typing import Optional

from ...kernel.commands import CommandCode, get_commands_for_paradigm

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

    # Fix: F-010 — Cross-field validation: required fields per limit type
    @model_validator(mode="after")
    def check_required_fields(self) -> "LimitRequest":
        if self.type in ("Time", "Both") and self.time_limit is None:
            raise ValueError("time_limit is required when type is 'Time' or 'Both'")
        if self.type in ("Infusion", "Both") and self.infusion_limit is None:
            raise ValueError("infusion_limit is required when type is 'Infusion' or 'Both'")
        return self


@router.post("/{session_id}/start")
async def start_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    # A second /start while already running or paused would silently wipe
    # the buffered behavior data (start_program() resets buffers) with a
    # bare 200 and no state transition. "disconnected" is the same failure
    # mode by another door: it's exactly where a serial drop mid-run leaves
    # a session holding unexported behavior data, and a /start there can
    # never reach the firmware anyway (no open port) — so it would wipe the
    # buffer and then 500, instead of failing before touching anything.
    # "armed" is exempt: the manual override below disarms and starts it
    # deliberately.
    if info.state in ("running", "paused", "disconnected"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start the program in '{info.state}' state",
        )

    try:
        # Manual override from the armed state ("Start Now"). Disarm first:
        # leaving the firmware watching the pin means a later stray edge
        # re-enters StartSession() mid-run, which re-fires the microscope
        # trigger — a toggle, not a level — and stops the scope scanning.
        if info.state == "armed":
            info.instance.disarm_external_trigger()
        info.instance.start_program()
    except Exception:
        logger.error("start_program failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start program")

    sm.set_state(session_id, "running")
    return {"status": "started"}


@router.post("/{session_id}/arm-trigger")
async def arm_trigger(session_id: str, request: Request):
    """Arm the external TTL trigger: the next rising edge starts the session.

    Deliberately reachable only from "connected". Config and pin changes are
    rejected while armed (they go through the hardware router, which requires
    "connected"), because config is applied one serial command per request —
    there is no transactional apply, so a trigger landing mid-edit would start
    the session on a half-applied configuration. Cancel, edit, re-arm.
    """
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state != "connected":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot arm the external trigger in '{info.state}' state",
        )

    # Paradigm gate mirrors the frontend capability sniff: "_lite" sketches have
    # no ExternalTrigger and the UNO has no free external-interrupt pin.
    if int(CommandCode.EXT_TRIGGER_ARM) not in get_commands_for_paradigm(info.paradigm or "fr"):
        raise HTTPException(
            status_code=400,
            detail=f"Paradigm '{info.paradigm}' has no external trigger support",
        )

    try:
        info.instance.arm_external_trigger()
    except Exception:
        logger.error("arm_external_trigger failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to arm external trigger")

    sm.set_state(session_id, "armed")
    return {"status": "armed"}


@router.post("/{session_id}/disarm-trigger")
async def disarm_trigger(session_id: str, request: Request):
    """Cancel an armed external trigger and return the session to "connected"."""
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state != "armed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot disarm the external trigger in '{info.state}' state",
        )

    # Best-effort: if serial was closed while armed the firmware is unreachable,
    # but the session must still leave the "armed" state or it has no exit at
    # all. The board really may still be armed, so say so rather than implying
    # a clean disarm.
    released = info.instance.release_external_trigger()
    sm.set_state(session_id, "connected")
    return {"status": "disarmed", "firmware_notified": released}


@router.post("/{session_id}/stop")
async def stop_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    # An armed session has program_running False, so stop_program() returns at
    # its re-entrance guard and on_stop never fires. Without this the route
    # reported "stopped" while the board kept watching the pin, and a later edge
    # started recording a session the operator had explicitly stopped.
    # stop_program() releases the trigger itself; this only fixes the state.
    was_armed = info.state == "armed"

    try:
        # Fix: F-001 — run_in_executor so time.sleep(2) in stop_program() doesn't block the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, info.instance.stop_program)
    except Exception:
        logger.error("stop_program failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to stop program")

    if was_armed:
        sm.set_state(session_id, "stopped")

    return {"status": "stopped"}


@router.post("/{session_id}/pause")
async def pause_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fix: F-007 — Only allow pause/resume when session is running or paused
    if info.state not in ("running", "paused"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause/resume a session in '{info.state}' state",
        )

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
    """Set the session's time/infusion limits.

    Rejected while armed for the same reason device config is: the limits
    describe the run the trigger is about to start, and the edge can land at
    any instant. No serial write is involved, so this is contract consistency
    rather than a half-applied-firmware hazard.
    """
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state == "armed":
        raise HTTPException(
            status_code=409,
            detail=(
                "Session is armed and waiting for an external trigger; "
                "disarm it before changing limits"
            ),
        )

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


@router.post("/{session_id}/split")
async def split_segment(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state not in ("running", "paused"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot split in '{info.state}' state",
        )

    try:
        result = info.instance.split_segment()
    except Exception:
        logger.error("split_segment failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to split segment")

    return {"status": "split", **result}


@router.post("/{session_id}/restart")
async def restart_program(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state not in ("running", "paused"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot restart in '{info.state}' state",
        )

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, info.instance.restart_program)
    except Exception:
        logger.error("restart_program failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to restart program")

    sm.set_state(session_id, "running")
    return {"status": "restarted"}
