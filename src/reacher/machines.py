"""Persistent storage for paired machine credentials.

Paired machine configs are stored at ~/.reacher/machines.json (mode 0o600).
The file contains a dict mapping device_id → {url, api_key, hostname, name}.
All reads and writes are protected by a threading.Lock.
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_DIR = os.path.expanduser("~/.reacher")
_FILE = os.path.join(_DIR, "machines.json")
_lock = threading.Lock()
_cache: dict[str, dict] = {}


def load() -> None:
    """Load paired machine configs from disk into memory. Called at startup."""
    global _cache
    if not os.path.isfile(_FILE):
        _cache = {}
        return
    try:
        with open(_FILE) as f:
            data = json.load(f)
        _cache = {k: v for k, v in data.items() if isinstance(v, dict)}
        logger.info("Loaded %d paired machine(s) from %s", len(_cache), _FILE)
    except Exception:
        logger.exception("Failed to load machines.json — starting with empty machine list")
        _cache = {}


def get_all() -> dict[str, dict]:
    """Return a shallow copy of all paired machine entries."""
    with _lock:
        return dict(_cache)


def get(device_id: str) -> dict | None:
    """Return the machine entry for device_id, or None if not paired."""
    with _lock:
        return _cache.get(device_id)


def upsert(device_id: str, url: str, api_key: str, hostname: str, name: str) -> None:
    """Add or update a paired machine entry and flush to disk."""
    with _lock:
        _cache[device_id] = {"url": url, "api_key": api_key, "hostname": hostname, "name": name}
        _flush()


def remove(device_id: str) -> None:
    """Remove a paired machine entry and flush to disk."""
    with _lock:
        _cache.pop(device_id, None)
        _flush()


def _flush() -> None:
    """Write the in-memory cache to disk. Must be called under _lock."""
    os.makedirs(_DIR, exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump(_cache, f, indent=2)
    os.chmod(_FILE, 0o600)
