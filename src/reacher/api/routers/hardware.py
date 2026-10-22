"""Generic hardware command dispatch endpoint."""

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from ...kernel.commands import COMMAND_REGISTRY, CommandCode, get_commands_for_paradigm
from ... import pin_overrides, pump_target

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
    "delay":     (0, 600000),      # ms — per-device onset delay from trigger (CUE/PUMP/LASER)
    "timeout":   (0, 600000),      # ms — 0 disables timeout
    "timeout_mode": (0, 1),        # 0 = every active press starts the timeout, 1 = reward-triggering press only
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


class PinAssignmentsRequest(BaseModel):
    assignments: dict[str, int]


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

    if spec.requires_cap is not None and spec.requires_cap not in _firmware_caps(info):
        raise HTTPException(
            status_code=422,
            detail={"error": "firmware_lacks_cap", "cap": spec.requires_cap},
        )

    # Pin-reassignment commands have board- and role-aware validation, plus
    # a state gate (only allowed when the rig is connected but not running).
    if body.code in pin_overrides.PIN_CONSTRAINTS:
        if info.state != "connected":
            raise HTTPException(
                status_code=409,
                detail=f"Pin reassignment requires session state 'connected', got '{info.state}'",
            )
        if body.value is None:
            raise HTTPException(status_code=400, detail="pin command requires a 'value'")
        violation = pin_overrides.validate_pin(body.code, body.value, info.board)
        if violation is not None:
            raise HTTPException(status_code=422, detail=violation)
        if body.code == int(CommandCode.AUX_SET_PIN):
            # The shock output must never share a pin with another device.
            owner = _pin_owner(info, body.value, exclude="aux")
            if owner is not None:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "pin_conflict", "pin": body.value, "component": owner},
                )
    elif spec.payload_key is not None:
        # A payload command without its value would pass the range gate below
        # and reach the board as a bare {"cmd": N}, where the missing key reads
        # as 0 — while the host records the parameter as None.
        if body.value is None:
            raise HTTPException(status_code=400, detail=f"{spec.name} requires a 'value'")
        # Validate value against hardware-safe ranges (non-pin commands).
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
    except Exception as exc:
        if "serial port is not open" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Session not connected — connect to a serial port first")
        logger.error("Command %s failed", spec.name, exc_info=True)
        raise HTTPException(status_code=500, detail="Command failed")

    # Remember the reward-chain pump-target selection per-port so a fresh
    # serial connect (firmware's activePumpTarget resets to primary on boot)
    # can replay it instead of silently reverting to the primary pump.
    if body.code == int(CommandCode.SET_ACTIVE_PUMP):
        try:
            pump_target.save(info.port, bool(body.value))
        except Exception:
            logger.exception("Failed to persist pump target for session %s", session_id)

    response = {"status": "sent", "command": spec.name, "code": body.code}
    if body.code == int(CommandCode.SET_ACTIVE_PUMP):
        response["pump_target"] = "PUMP2" if body.value else "PUMP"
    return response


@router.get("/{session_id}/commands")
async def get_commands(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    paradigm = info.paradigm or "fr"
    caps = _firmware_caps(info)
    # A paradigm name cannot tell an AUX-capable fr build from an older one, so
    # capability-gated specs need the connected firmware's IDN to advertise them.
    cmds = {
        code: spec
        for code, spec in get_commands_for_paradigm(paradigm).items()
        if spec.requires_cap is None or spec.requires_cap in caps
    }
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


def _firmware_caps(info) -> set:
    instance = getattr(info, "instance", None)
    getter = getattr(instance, "get_firmware_caps", None)
    if getter is None:
        return set()
    caps = getter()
    return caps if isinstance(caps, set) else set()


# Pins.h defaults per component key (Mega); the microscope timestamp pin is fixed in firmware.
_DEFAULT_COMPONENT_PINS = {
    "cue": 3, "cue2": 7, "pump": 4, "pump2": 8, "lick": 5, "laser": 6,
    "microscope_trigger": 9, "microscope_ts": 2, "lever_rh": 10, "lever_lh": 13,
    "slm": 11, "aux": 22,
}


def _pin_owner(info, pin: int, exclude: str) -> Optional[str]:
    """Component other than *exclude* whose effective pin (override over default) is *pin*."""
    pins = {**_DEFAULT_COMPONENT_PINS, **pin_overrides.get(info.port, getattr(info, "board", None))}
    return next((c for c, p in pins.items() if c != exclude and p == pin), None)


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

    saved_pump2 = pump_target.get(info.port)
    return {
        "firmware_info": info.instance.get_firmware_information(),
        "hardware_settings": info.instance.get_hardware_settings(),
        # Last 221 sent for this port (None = never sent). Unlike the PUMP2
        # row's mirrored "active", this survives the firmware's Start dump.
        "pump_target": None if saved_pump2 is None else ("PUMP2" if saved_pump2 else "PUMP"),
    }


@router.put("/{session_id}/pins")
async def set_pins(session_id: str, body: PinAssignmentsRequest, request: Request):
    """Bulk-update Arduino pin assignments for a session's hardware components.

    Validates the whole assignment map atomically (no partial application
    if validation fails), dispatches the per-component SET_PIN commands in
    stable order, and persists the new map keyed by serial port so that
    future connects auto-replay.

    Returns ``{"applied": {component: pin}, "errors": [{component, ...}]}``.
    Whole-map validation errors raise 4xx; per-component send failures land
    in ``errors``.
    """
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.state != "connected":
        raise HTTPException(
            status_code=409,
            detail=f"Pin reassignment requires session state 'connected', got '{info.state}'",
        )

    assignments = body.assignments or {}

    # 1. Reject unknown component keys
    unknown = [c for c in assignments if c not in pin_overrides.SET_PIN_CODE_FOR]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown_components", "components": sorted(unknown)},
        )

    # 2. Reject components the connected firmware cannot honour (e.g. AUX on a non-aux build)
    caps = _firmware_caps(info)
    unsupported = [
        c for c in assignments
        if (cap := COMMAND_REGISTRY[pin_overrides.SET_PIN_CODE_FOR[c]].requires_cap) is not None and cap not in caps
    ]
    if unsupported:
        raise HTTPException(
            status_code=422,
            detail={"error": "unsupported_components", "components": sorted(unsupported)},
        )

    # 3. Reject pin collisions across components
    seen: dict[int, str] = {}
    collisions: list[dict] = []
    for component, pin in assignments.items():
        if pin in seen:
            collisions.append({"pin": pin, "components": sorted([seen[pin], component])})
        else:
            seen[pin] = component
    if collisions:
        raise HTTPException(
            status_code=422,
            detail={"error": "pin_collision", "collisions": collisions},
        )

    # 4. Validate each pin per its component's role constraint and the board
    violations: list[dict] = []
    for component, pin in assignments.items():
        code = pin_overrides.SET_PIN_CODE_FOR[component]
        violation = pin_overrides.validate_pin(code, pin, info.board)
        if violation is not None:
            violations.append(violation)
    if violations:
        raise HTTPException(status_code=422, detail={"error": "pin_violations", "violations": violations})

    # 5. Dispatch in stable order so the firmware processes them deterministically
    applied: dict[str, int] = {}
    errors: list[dict] = []
    for component in pin_overrides.COMPONENT_KEYS:
        if component not in assignments:
            continue
        pin = assignments[component]
        code = pin_overrides.SET_PIN_CODE_FOR[component]
        try:
            info.instance.send_command(code, pin)
            applied[component] = pin
        except Exception as exc:
            logger.error("Pin command %s (%d) failed on session %s", component, code, session_id, exc_info=True)
            errors.append({"component": component, "error": str(exc)})

    # 6. Persist the resulting map (only what successfully applied) per-port
    if applied:
        try:
            existing = pin_overrides.get(info.port)
            existing.update(applied)
            pin_overrides.save(info.port, existing, board=info.board)
        except Exception:
            logger.exception("Failed to persist pin overrides for session %s", session_id)

    return {"applied": applied, "errors": errors}
