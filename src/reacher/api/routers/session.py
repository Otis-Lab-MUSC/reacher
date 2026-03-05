"""Session CRUD endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from ...kernel.commands import PARADIGMS

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    port: str
    paradigm: Optional[str] = None


@router.get("")
async def list_sessions(request: Request):
    sm = request.app.state.session_manager
    return {"sessions": sm.list_sessions()}


@router.post("", status_code=201)
async def create_session(body: CreateSessionRequest, request: Request):
    sm = request.app.state.session_manager
    if body.paradigm is not None and body.paradigm not in PARADIGMS:
        raise HTTPException(status_code=400, detail=f"Invalid paradigm. Must be one of: {', '.join(PARADIGMS)}")
    try:
        session_id = sm.create_session(body.port, body.paradigm)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"session_id": session_id}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
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
    }


@router.post("/{session_id}/reset")
async def reset_session(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        info.instance.reset()
        # Re-open serial since reset() closes it
        info.instance.set_COM_port(info.port)
        info.instance.open_serial()
    except Exception:
        logger.error("Reset failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Session reset failed")

    sm.set_state(session_id, "connected")
    return {"status": "reset"}


@router.delete("/{session_id}")
async def destroy_session(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    sm.destroy_session(session_id)
    return {"status": "destroyed"}
