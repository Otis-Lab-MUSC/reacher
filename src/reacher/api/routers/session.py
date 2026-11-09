"""Session CRUD endpoints."""

import asyncio
import logging
import queue

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, JsonValue
from typing import Optional

from ...kernel.commands import PARADIGMS
from ..errors import KERNEL_ERRORS

router = APIRouter()
logger = logging.getLogger(__name__)

# reset() drains the data queue, whose sentinel put raises queue.Full when the queue is full.
_RESET_ERRORS: tuple[type[Exception], ...] = (*KERNEL_ERRORS, queue.Full)


class CreateSessionRequest(BaseModel):
    port: str
    paradigm: Optional[str] = None


@router.get("", response_model=None)
async def list_sessions(request: Request) -> dict[str, list[dict[str, str | None]]]:
    sm = request.app.state.session_manager
    return {"sessions": sm.list_sessions()}


@router.post("", status_code=201, response_model=None)
async def create_session(body: CreateSessionRequest, request: Request) -> dict[str, str]:
    sm = request.app.state.session_manager
    if body.paradigm is not None and body.paradigm not in PARADIGMS:
        raise HTTPException(status_code=400, detail=f"Invalid paradigm. Must be one of: {', '.join(PARADIGMS)}")
    try:
        session_id = sm.create_session(body.port, body.paradigm)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # "port" is what the session is actually bound to: a request for the generic
    # SIMULATOR port comes back as its own SIMn instance.
    return {"session_id": session_id, "port": sm.get_session(session_id).port}


@router.get("/{session_id}", response_model=None)
async def get_session(session_id: str, request: Request) -> dict[str, JsonValue]:
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    instance = info.instance
    return {
        "session_id": info.session_id,
        "port": info.port,
        "paradigm": info.paradigm,
        "board": info.board,
        "state": info.state,
        "program_running": instance.get_program_running(),
        "firmware_info": instance.get_firmware_information(),
        "callback_failures": instance.emit_failure_count,
        "counted_pumps": instance.get_counted_pumps(),
        "follows_reward_pump": instance._counted_pumps is None,
        "infusions_by_pump": instance.get_infusions_by_pump(),
        "infusion_count": instance.get_total_infusion_count(),
    }


@router.post("/{session_id}/reset", response_model=None)
async def reset_session(session_id: str, request: Request) -> dict[str, str]:
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state == "paused":
        raise HTTPException(status_code=409, detail="Cannot reset while paused. Resume or stop first")
    if info.state == "disconnected":
        raise HTTPException(status_code=409, detail="Cannot reset while disconnected. Reconnect first")

    try:
        info.instance.reset()
        # Re-open serial since reset() closes it
        info.instance.set_COM_port(info.port, info.paradigm)
        info.instance.open_serial()
    except _RESET_ERRORS:
        logger.error("Reset failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Session reset failed")

    sm.set_state(session_id, "connected")
    return {"status": "reset"}


@router.delete("/{session_id}", response_model=None)
async def destroy_session(session_id: str, request: Request) -> dict[str, str]:
    sm = request.app.state.session_manager
    try:
        sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    # destroy_session calls stop_program (blocking); run off the event loop
    # Clean up rate-limit timestamps for this session
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, sm.destroy_session, session_id)
    from .hardware import release_session

    release_session(session_id)
    return {"status": "destroyed"}
