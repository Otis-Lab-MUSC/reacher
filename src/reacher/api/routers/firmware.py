"""Firmware upload endpoints."""

import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ...uploader.boards import DEFAULT_BOARD, SUPPORTED_BOARDS
from ...uploader.uploader import FirmwareUploader
from . import websocket as ws_mod

router = APIRouter()
_uploader = FirmwareUploader()


@router.get("/boards")
async def list_boards():
    return {"boards": _uploader.list_boards()}


@router.get("/paradigms")
async def list_paradigms(board: str = Query(DEFAULT_BOARD)):
    if board.lower() not in SUPPORTED_BOARDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported board: {board!r}. Supported: {SUPPORTED_BOARDS}",
        )
    available = _uploader.list_available(board)
    return {"paradigms": available}


class UploadRequest(BaseModel):
    paradigm: str
    board: str = DEFAULT_BOARD


@router.post("/upload/{session_id}")
async def upload_firmware(session_id: str, body: UploadRequest, request: Request):
    sm = request.app.state.session_manager

    # Validate board early
    if body.board.lower() not in SUPPORTED_BOARDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported board: {body.board!r}. Supported: {SUPPORTED_BOARDS}",
        )

    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    # Close serial if open so avrdude can access the port
    instance = info.instance
    if instance.ser.is_open:
        instance.close_serial()

    sm.set_state(session_id, "uploading")

    def progress_cb(percent: int, stage: str):
        ws_mod.enqueue_event(session_id, "upload_progress", {
            "percent": percent,
            "stage": stage,
        })

    try:
        success = await _uploader.upload(body.paradigm, info.port, body.board, progress_cb)
    except FileNotFoundError as e:
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=500, detail=str(e))

    if not success:
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=500, detail="Firmware upload failed")

    # Wait for Arduino to reboot, then reconnect
    await asyncio.sleep(2)
    try:
        instance.set_COM_port(info.port)
        instance.open_serial()
        # Request firmware identification
        instance.send_command(102)  # IDENTIFY
    except Exception as e:
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=500, detail=f"Post-upload reconnect failed: {e}")

    sm.set_paradigm(session_id, body.paradigm)
    sm.set_board(session_id, body.board)
    sm.set_state(session_id, "connected")

    # Give firmware time to respond with identification
    await asyncio.sleep(1)
    return {
        "status": "uploaded",
        "paradigm": body.paradigm,
        "board": body.board,
        "firmware_info": instance.get_firmware_information(),
    }
