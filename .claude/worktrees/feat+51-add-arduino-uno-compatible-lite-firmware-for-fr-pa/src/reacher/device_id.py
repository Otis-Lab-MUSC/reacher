"""Persistent device identity for REACHER.

Generates a unique UUID hex string on first run, writes it to
~/.reacher/device_id, and reloads it on subsequent starts.  This ID is
exposed via the /health endpoint so that Labrynth can fingerprint and
distinguish REACHER devices on the network.
"""

import os
import uuid

_ID_DIR = os.path.expanduser("~/.reacher")
_ID_FILE = os.path.join(_ID_DIR, "device_id")


def _resolve_device_id() -> str:
    """Return the persistent device ID, generating one if necessary."""
    if os.path.isfile(_ID_FILE):
        with open(_ID_FILE) as f:
            stored = f.read().strip()
        if stored:
            return stored

    device_id = uuid.uuid4().hex
    os.makedirs(_ID_DIR, exist_ok=True)
    with open(_ID_FILE, "w") as f:
        f.write(device_id)
    os.chmod(_ID_FILE, 0o600)
    return device_id


# Resolved once at import time — same pattern as API_KEY in auth.py
DEVICE_ID: str = _resolve_device_id()
