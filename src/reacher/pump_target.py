"""Persistent storage for the per-rig reward-chain active-pump selection.

Mirrors ``pin_overrides.py``'s per-port persistence pattern: firmware's
``activePump``/``activePumpTarget`` (set via ``SET_ACTIVE_PUMP``, code 221)
lives only in Arduino RAM, so a fresh serial connect starts back at the
firmware default (primary pump) even though the researcher had selected the
secondary pump. This module remembers the last explicitly-sent value per
serial port and lets the connect flow replay it, the same way pin overrides
are replayed.

Stored at ~/.reacher/pump_target.json (mode 0o600):

    {"/dev/ttyACM0": true, "/dev/ttyUSB0": false}

where the value is the ``pump2`` payload last sent with SET_ACTIVE_PUMP
(``true`` = secondary pump is the reward-chain target).
"""

import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_DIR = os.path.expanduser("~/.reacher")
_FILE = os.path.join(_DIR, "pump_target.json")
_lock = threading.Lock()
_cache: dict[str, bool] = {}


def load() -> None:
    """Load persisted pump targets from disk into memory. Called at startup."""
    global _cache
    if not os.path.isfile(_FILE):
        _cache = {}
        return
    try:
        with open(_FILE) as f:
            data = json.load(f)
        _cache = {port: bool(value) for port, value in data.items()}
        logger.info("Loaded pump target overrides for %d port(s) from %s", len(_cache), _FILE)
    except Exception:
        logger.exception("Failed to load pump_target.json — starting with empty overrides")
        _cache = {}


def get(port: str) -> Optional[bool]:
    """Return the last-saved pump2-active flag for *port*, or None if never set."""
    with _lock:
        return _cache.get(port)


def get_all() -> dict[str, bool]:
    """Return a shallow copy of all persisted pump targets, keyed by port."""
    with _lock:
        return dict(_cache)


def save(port: str, pump2_active: bool) -> None:
    """Persist the reward-chain pump-target selection for *port* and flush to disk."""
    with _lock:
        _cache[port] = bool(pump2_active)
        _flush()


def clear(port: str) -> None:
    """Remove the persisted pump target for *port* and flush."""
    with _lock:
        _cache.pop(port, None)
        _flush()


def _flush() -> None:
    """Write the in-memory cache to disk. Must be called under _lock."""
    os.makedirs(_DIR, exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump(_cache, f, indent=2)
    os.chmod(_FILE, 0o600)
