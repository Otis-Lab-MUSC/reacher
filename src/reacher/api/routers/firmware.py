"""Firmware upload endpoints."""

import asyncio
import base64
import hashlib
import logging
import os
import shutil
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ...uploader.boards import DEFAULT_BOARD, SUPPORTED_BOARDS
from ...uploader.uploader import PARADIGMS, FirmwareUploader
from . import websocket as ws_mod

_MAX_HEX_SIZE = 200 * 1024  # 200 KB — hex files are typically 15-40 KB

router = APIRouter()
diagnostics_router = APIRouter()  # Registered without auth in app.py
_logger = logging.getLogger(__name__)
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
    hex_data: Optional[str] = None  # base64-encoded Intel HEX file content


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

    # If the client supplied inline hex data, decode, validate, and cache it.
    cached_hex_path = None
    if body.hex_data:
        try:
            raw = base64.b64decode(body.hex_data, validate=True)
        except Exception:
            sm.set_state(session_id, "idle")
            raise HTTPException(status_code=400, detail="Invalid base64 in hex_data")
        if len(raw) > _MAX_HEX_SIZE:
            sm.set_state(session_id, "idle")
            raise HTTPException(status_code=400, detail=f"hex_data too large ({len(raw)} bytes, max {_MAX_HEX_SIZE})")
        if not raw.lstrip(b"\xef\xbb\xbf").startswith(b":"):
            sm.set_state(session_id, "idle")
            raise HTTPException(status_code=400, detail="hex_data does not look like Intel HEX (must start with ':')")
        cached_hex_path = FirmwareUploader.cache_hex(body.paradigm, body.board, raw)

    try:
        success = await _uploader.upload(body.paradigm, info.port, body.board, progress_cb, hex_path=cached_hex_path)
    except FileNotFoundError as e:
        _logger.error("Firmware file not found for session %s: %s", session_id, e)
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        _logger.error("Upload tool error for session %s: %s", session_id, e)
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        _logger.error("Firmware upload failed for session %s: %s", session_id, e, exc_info=True)
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=500, detail="Firmware upload failed")

    if not success:
        sm.set_state(session_id, "idle")
        detail = _uploader.last_error or "Firmware upload failed"
        raise HTTPException(status_code=500, detail=detail)

    # Wait for Arduino to reboot, then reconnect
    await asyncio.sleep(2)
    try:
        instance.set_COM_port(info.port)
        instance.open_serial()
        # Request firmware identification
        instance.send_command(102)  # IDENTIFY
    except Exception as e:
        _logger.error("Post-upload reconnect failed for session %s: %s", session_id, e, exc_info=True)
        sm.set_state(session_id, "idle")
        raise HTTPException(status_code=500, detail="Post-upload reconnect failed")

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


@diagnostics_router.get("/diagnostics")
async def firmware_diagnostics(board: str = Query(DEFAULT_BOARD)):
    """Return diagnostic information about hex file resolution for debugging."""
    if board.lower() not in SUPPORTED_BOARDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported board: {board!r}. Supported: {SUPPORTED_BOARDS}",
        )
    _avrdude_path = _uploader.avrdude_path
    _is_abs = os.path.isabs(_avrdude_path)

    # List files in the avrdude directory to verify companion DLLs were bundled
    avrdude_dir_contents = None
    if _is_abs:
        avrdude_dir = os.path.dirname(_avrdude_path)
        try:
            avrdude_dir_contents = sorted(os.listdir(avrdude_dir))
        except OSError:
            avrdude_dir_contents = None

    result: dict = {
        "frozen": getattr(sys, "frozen", False),
        "meipass": getattr(sys, "_MEIPASS", None),
        "resolved_hex_dir": _uploader.hex_dir,
        "avrdude_path": _avrdude_path,
        "avrdude_exists": os.path.isfile(_avrdude_path) if _is_abs else bool(shutil.which(_avrdude_path)),
        "avrdude_conf": _uploader.avrdude_conf,
        "avrdude_dir_contents": avrdude_dir_contents,
        "last_upload_error": _uploader.last_error or None,
        "board": board,
        "paradigms": {},
    }
    for p in PARADIGMS:
        try:
            path = _uploader.get_hex_path(p, board)
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()[:16]
            result["paradigms"][p] = {"path": path, "size": size, "sha256_prefix": sha}
        except (FileNotFoundError, ValueError) as exc:
            result["paradigms"][p] = {"error": str(exc)}
    return result
