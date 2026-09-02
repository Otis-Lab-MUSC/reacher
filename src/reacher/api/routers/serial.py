"""Serial connection endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from serial.tools import list_ports

from ...uploader.boards import detect_board_from_port
from ...kernel.commands import COMMAND_REGISTRY, CommandCode
from ... import pin_overrides, pump_target
from . import websocket as _ws

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ports")
async def get_ports():
    comports = list_ports.comports()
    ports = [p.device for p in comports if p.vid and p.pid]
    ports.append("SIMULATOR")
    port_boards = {p.device: detect_board_from_port(p.device) for p in comports if p.vid and p.pid}
    port_boards["SIMULATOR"] = None
    return {"ports": ports, "portBoards": port_boards}


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
    except ValueError as e:
        # Fix: PY-002 — Surface validation errors without leaking internals
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError:
        logger.warning("Permission denied opening serial port for session %s", session_id)
        raise HTTPException(
            status_code=403,
            detail=(
                "Permission denied opening serial port. "
                "On Linux, add the user to the 'dialout' group: "
                "sudo usermod -a -G dialout $USER  (then log out and back in)"
            ),
        )
    except Exception:
        # Fix: PY-002 — Generic message; details logged server-side
        logger.exception("Serial connect failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to open serial connection")

    # --- Non-fatal firmware detection probe ---
    # Fix: LAZ-001 — Wait for IDENTIFY response (firmware readiness gate)
    # Don't transition to "connected" until bootloader exits and firmware acks IDENTIFY.
    # This prevents commands from being silently dropped during the ~1.5–2s bootloader window.
    detected_paradigm = None
    try:
        instance.send_command(102)  # IDENTIFY
        instance._firmware_ready.clear()  # Reset gate for this reconnect
        # Wait up to 2s for firmware to respond (typical bootloader exit is 1.5–2s)
        if instance._firmware_ready.wait(timeout=2.0):
            detected_paradigm = instance.get_detected_paradigm()
            if detected_paradigm:
                sm.set_paradigm(session_id, detected_paradigm)
                logger.info("Auto-detected paradigm '%s' on session %s", detected_paradigm, session_id)
        else:
            logger.warning("IDENTIFY response timeout (bootloader may be slow) for session %s", session_id)
    except Exception as e:
        logger.warning("Firmware detection failed on session %s: %s", session_id, e)

    sm.set_state(session_id, "connected")

    # --- Non-fatal board detection via USB VID/PID ---
    detected_board = None
    try:
        detected_board = detect_board_from_port(info.port)
        if detected_board:
            sm.set_board(session_id, detected_board)
            logger.info("Auto-detected board '%s' on session %s", detected_board, session_id)
    except Exception as e:
        logger.warning("Board detection failed on session %s: %s", session_id, e)

    # --- Replay persisted pin overrides (per-port) ---
    # Saved overrides may be invalid for the now-connected board (e.g. pin 50
    # saved when the rig was Mega and an UNO is now plugged in). Drop those
    # with a warning rather than refusing to connect.
    replayed_pins: dict[str, int] = {}
    skipped_pins: list[dict] = []
    try:
        saved = pin_overrides.get(info.port, detected_board)
        for component, pin in saved.items():
            code = pin_overrides.SET_PIN_CODE_FOR.get(component)
            if code is None:
                skipped_pins.append({"component": component, "reason": "unknown_component"})
                continue
            violation = pin_overrides.validate_pin(code, pin, detected_board)
            if violation is not None:
                logger.warning("Skipping invalid saved pin override for session %s: %s", session_id, violation)
                skipped_pins.append({"component": component, "reason": violation})
                continue
            try:
                instance.send_command(code, pin)
                replayed_pins[component] = pin
            except Exception as e:
                logger.warning("Failed to replay pin %s=%d on session %s: %s", component, pin, session_id, e)
                skipped_pins.append({"component": component, "reason": "send_failed"})
    except Exception:
        logger.exception("Pin override replay failed on session %s", session_id)

    if replayed_pins or skipped_pins:
        parts = [f"{k}={v}" for k, v in replayed_pins.items()]
        msg = f"Replayed {len(replayed_pins)} pin override(s) on connect"
        if parts:
            msg += f": {', '.join(parts)}"
        if skipped_pins:
            skipped_names = [s.get('component', '?') for s in skipped_pins]
            msg += f"; skipped {len(skipped_pins)}: {', '.join(skipped_names)}"
        _ws.enqueue_event(session_id, "log", {"level": "warn", "message": msg})

    # --- Replay persisted reward-chain pump target (per-port) ---
    # Firmware's activePumpTarget lives only in Arduino RAM and resets to the
    # primary pump on every boot, so without this a researcher who selected
    # the secondary pump last session silently reverts to the primary one.
    # Only replay when the detected paradigm still supports SET_ACTIVE_PUMP
    # (e.g. not pavlovian) — mirrors the paradigm gate the /command endpoint
    # enforces for a live send.
    replayed_pump_target: Optional[bool] = None
    try:
        saved_pump_target = pump_target.get(info.port)
        spec = COMMAND_REGISTRY.get(int(CommandCode.SET_ACTIVE_PUMP))
        paradigm_ok = (
            detected_paradigm is not None
            and spec is not None
            and (not spec.paradigms or detected_paradigm.lower() in spec.paradigms)
        )
        if saved_pump_target is not None and paradigm_ok:
            instance.send_command(int(CommandCode.SET_ACTIVE_PUMP), int(saved_pump_target))
            replayed_pump_target = saved_pump_target
            _ws.enqueue_event(
                session_id,
                "log",
                {
                    "level": "warn",
                    "message": f"Replayed reward-chain pump target on connect: pump2={saved_pump_target}",
                },
            )
    except Exception:
        logger.exception("Pump target replay failed on session %s", session_id)

    return {
        "status": "connected",
        "port": info.port,
        "detected_paradigm": detected_paradigm,
        "detected_board": detected_board,
        "replayed_pins": replayed_pins,
        "skipped_pins": skipped_pins,
        "replayed_pump_target": replayed_pump_target,
    }


@router.post("/{session_id}/disconnect")
async def disconnect_serial(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        info.instance.close_serial()
    except Exception:
        # Fix: PY-002 — Generic message; details logged server-side
        logger.exception("Serial disconnect failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to close serial connection")

    sm.set_state(session_id, "idle")
    return {"status": "disconnected"}


@router.get("/pin-overrides")
async def get_pin_overrides():
    """Return all persisted pin overrides keyed by port."""
    return pin_overrides.get_all()


@router.delete("/pin-overrides")
async def clear_pin_overrides(port: str):
    """Clear all pin overrides for the given port path (pass as query param)."""
    pin_overrides.clear(port)
    return {"status": "cleared", "port": port}


@router.get("/pump-target")
async def get_pump_targets():
    """Return the persisted reward-chain pump-target selection, keyed by port."""
    return pump_target.get_all()


@router.delete("/pump-target")
async def clear_pump_target(port: str):
    """Clear the persisted pump-target selection for the given port path (query param)."""
    pump_target.clear(port)
    return {"status": "cleared", "port": port}
