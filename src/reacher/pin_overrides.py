"""Persistent storage and validation for per-rig Arduino pin overrides.

Pin overrides are stored at ~/.reacher/pin_overrides.json (mode 0o600). The
file contains a dict mapping serial-port path -> {component_key: pin}, e.g.

    {
        "/dev/ttyACM0": {"cue": 11, "lever_rh": 12, "pump": 4},
        "/dev/ttyUSB0": {"laser": 9}
    }

Per-port keying matches the granularity of SessionManager's existing
port-locking and corresponds to the physical rig identity that the user
cares about (which Arduino is plugged in where). All reads and writes are
protected by a threading.Lock.

This module also owns the pin-validation metadata (board pin sets, role
constraints, component-to-command mapping) so both the HTTP router and the
serial-connect replay path can share a single source of truth.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

from .kernel.commands import CommandCode

logger = logging.getLogger(__name__)

_DIR = os.path.expanduser("~/.reacher")
_FILE = os.path.join(_DIR, "pin_overrides.json")
_lock = threading.Lock()
_cache: dict[str, dict[str, int]] = {}


# --- Pin validation metadata ---------------------------------------------
#
# UNO digital usable pins: 2..13 (0/1 reserved for serial RX/TX)
# Mega digital usable pins: 2..53 (0/1 reserved for Serial0)
# UNO PWM: 3, 5, 6, 9, 10, 11
# Mega PWM: 2..13, 44, 45, 46
# UNO interrupt-capable: 2 (INT0), 3 (INT1)
# Mega interrupt-capable: 2, 3, 18, 19, 20, 21
UNO_DIGITAL = frozenset(range(2, 14))
UNO_PWM = frozenset({3, 5, 6, 9, 10, 11})
UNO_INT = frozenset({2, 3})
MEGA_DIGITAL = frozenset(range(2, 54))
MEGA_PWM = frozenset(range(2, 14)) | {44, 45, 46}
MEGA_INT = frozenset({2, 3, 18, 19, 20, 21})


@dataclass(frozen=True)
class PinConstraint:
    """Per-component pin role constraint for the SET_PIN command family."""

    component_key: str          # canonical key used by the bulk-pins endpoint
    requires_pwm: bool = False
    requires_interrupt: bool = False


# Map command code -> PinConstraint. Cue/Cue2/Laser drive PWM; Microscope
# trigger is a plain digital output; the timestamp pin is fixed in firmware
# (not remappable) so no entry exists for it.
PIN_CONSTRAINTS: dict[int, PinConstraint] = {
    int(CommandCode.CUE_SET_PIN):              PinConstraint("cue",                requires_pwm=True),
    int(CommandCode.CUE2_SET_PIN):             PinConstraint("cue2",               requires_pwm=True),
    int(CommandCode.PUMP_SET_PIN):             PinConstraint("pump"),
    int(CommandCode.PUMP2_SET_PIN):            PinConstraint("pump2"),
    int(CommandCode.LICK_SET_PIN):             PinConstraint("lick"),
    int(CommandCode.LASER_SET_PIN):            PinConstraint("laser",              requires_pwm=True),
    int(CommandCode.MICROSCOPE_SET_TRIG_PIN):  PinConstraint("microscope_trigger"),
    int(CommandCode.LEVER_RH_SET_PIN):         PinConstraint("lever_rh"),
    int(CommandCode.LEVER_LH_SET_PIN):         PinConstraint("lever_lh"),
}

# Reverse lookup: component_key -> set-pin command code.
SET_PIN_CODE_FOR: dict[str, int] = {
    c.component_key: code for code, c in PIN_CONSTRAINTS.items()
}

# All canonical component keys (stable order — used by the GUI grid).
COMPONENT_KEYS: tuple[str, ...] = tuple(SET_PIN_CODE_FOR.keys())


def board_sets(board: Optional[str]) -> tuple[frozenset[int], frozenset[int], frozenset[int]]:
    """Return (digital, pwm, interrupt) sets for a board. Defaults to UNO."""
    if board and board.lower() == "mega":
        return MEGA_DIGITAL, MEGA_PWM, MEGA_INT
    return UNO_DIGITAL, UNO_PWM, UNO_INT


def validate_pin(code: int, pin: int, board: Optional[str]) -> Optional[dict]:
    """Validate a single SET_PIN command for the given board.

    Returns None on success, or a dict describing the violation suitable for
    a 422 response body.
    """
    constraint = PIN_CONSTRAINTS.get(code)
    if constraint is None:
        return None
    digital, pwm, interrupt = board_sets(board)
    if pin not in digital:
        return {
            "error": "pin_out_of_range",
            "component": constraint.component_key,
            "got": pin,
            "board": (board or "uno").lower(),
            "allowed": sorted(digital),
        }
    if constraint.requires_pwm and pin not in pwm:
        return {
            "error": "pin_role_violation",
            "component": constraint.component_key,
            "required": "pwm",
            "got": pin,
            "allowed": sorted(pwm),
        }
    if constraint.requires_interrupt and pin not in interrupt:
        return {
            "error": "pin_role_violation",
            "component": constraint.component_key,
            "required": "interrupt",
            "got": pin,
            "allowed": sorted(interrupt),
        }
    return None


# --- Persistence ---------------------------------------------------------


def load() -> None:
    """Load pin overrides from disk into memory. Called at startup."""
    global _cache
    if not os.path.isfile(_FILE):
        _cache = {}
        return
    try:
        with open(_FILE) as f:
            data = json.load(f)
        _cache = {
            port: {k: int(v) for k, v in mapping.items() if isinstance(v, (int, float))}
            for port, mapping in data.items()
            if isinstance(mapping, dict)
        }
        logger.info("Loaded pin overrides for %d port(s) from %s", len(_cache), _FILE)
    except Exception:
        logger.exception("Failed to load pin_overrides.json — starting with empty overrides")
        _cache = {}


def get(port: str) -> dict[str, int]:
    """Return the pin override map for *port* (empty dict if none)."""
    with _lock:
        return dict(_cache.get(port, {}))


def get_all() -> dict[str, dict[str, int]]:
    """Return a shallow copy of all pin overrides."""
    with _lock:
        return {p: dict(m) for p, m in _cache.items()}


def save(port: str, assignments: dict[str, int]) -> None:
    """Persist a complete override map for *port* and flush to disk.

    *assignments* fully replaces any existing entry for the port. Pass an
    empty dict to clear all overrides for the port (use clear() for that
    intent — same effect, clearer name).
    """
    with _lock:
        if assignments:
            _cache[port] = {k: int(v) for k, v in assignments.items()}
        else:
            _cache.pop(port, None)
        _flush()


def clear(port: str) -> None:
    """Remove all overrides for *port* and flush."""
    save(port, {})


def _flush() -> None:
    """Write the in-memory cache to disk. Must be called under _lock."""
    os.makedirs(_DIR, exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump(_cache, f, indent=2)
    os.chmod(_FILE, 0o600)
