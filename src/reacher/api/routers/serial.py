"""Serial connection endpoints."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from serial.tools import list_ports

from ...uploader.boards import detect_board_from_port

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ports")
async def get_ports():
    ports = [p.device for p in list_ports.comports() if p.vid and p.pid]
    ports.append("SIMULATOR")
    return {"ports": ports}


@router.post("/{session_id}/connect")
async def connect_serial(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = info.instance
    try:
        instance.set_COM_port(info.port)
        instance.open_serial()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    sm.set_state(session_id, "connected")

    # --- Non-fatal firmware detection probe ---
    detected_paradigm = None
    try:
        instance.send_command(102)  # IDENTIFY
        await asyncio.sleep(1)     # Give firmware time to respond
        detected_paradigm = instance.get_detected_paradigm()
        if detected_paradigm:
            sm.set_paradigm(session_id, detected_paradigm)
            logger.info("Auto-detected paradigm '%s' on session %s", detected_paradigm, session_id)
    except Exception as e:
        logger.warning("Firmware detection failed on session %s: %s", session_id, e)

    # --- Non-fatal board detection via USB VID/PID ---
    detected_board = None
    try:
        detected_board = detect_board_from_port(info.port)
        if detected_board:
            sm.set_board(session_id, detected_board)
            logger.info("Auto-detected board '%s' on session %s", detected_board, session_id)
    except Exception as e:
        logger.warning("Board detection failed on session %s: %s", session_id, e)

    return {"status": "connected", "port": info.port, "detected_paradigm": detected_paradigm, "detected_board": detected_board}


@router.post("/{session_id}/disconnect")
async def disconnect_serial(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        info.instance.close_serial()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    sm.set_state(session_id, "idle")
    return {"status": "disconnected"}
