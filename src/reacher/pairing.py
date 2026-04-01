"""Rotating 6-digit pairing code for zero-config machine discovery.

Generates a new numeric code every 5 minutes and prints it to stdout.
The /api/pairing/claim endpoint validates submitted codes without exposing
the API key through mDNS advertisements or QR codes.
"""

import logging
import os
import secrets
import threading
import time

logger = logging.getLogger(__name__)

_PAIRED_DIR = os.path.expanduser("~/.reacher")
_PAIRED_FILE = os.path.join(_PAIRED_DIR, "paired")

_CODE_INTERVAL = 300  # seconds (5 minutes)
_STALE_TIMEOUT = 600  # seconds (10 minutes) — resume printing codes if no auth activity
_current_code: str = ""
_code_lock = threading.Lock()
_timer: threading.Timer | None = None
_started = False
_paired = False
_last_auth_time: float = 0.0
_rotation_start: float = 0.0


def load() -> None:
    """Load persisted paired state from disk. Called at startup before start_rotation()."""
    global _paired
    _paired = os.path.isfile(_PAIRED_FILE)
    if _paired:
        logger.info("Loaded paired state from %s", _PAIRED_FILE)


def _print_code(code: str) -> None:
    """Print the pairing code with a visually prominent bordered callout."""
    formatted = f"{code[:3]}-{code[3:]}"
    # Use box-drawing characters when the terminal supports UTF-8, otherwise ASCII.
    import sys
    use_box = getattr(sys.stdout, "encoding", "ascii").lower().replace("-", "") in ("utf8", "utf16", "utf32")
    if use_box:
        print(f"\n  ╔══════════════════════════════════════╗")
        print(f"  ║   PAIRING CODE :  {formatted:<18} ║")
        print(f"  ║   Rotates every 5 minutes            ║")
        print(f"  ╚══════════════════════════════════════╝\n")
    else:
        print(f"\n  +--------------------------------------+")
        print(f"  |  PAIRING CODE :  {formatted:<19}|")
        print(f"  |  Rotates every 5 minutes             |")
        print(f"  +--------------------------------------+\n")


def _rotate() -> None:
    """Generate a new pairing code and schedule the next rotation.

    Codes are always generated (even when paired) so that re-pairing with a
    valid code is possible.  Codes are only printed to stdout when the device
    is unpaired or when the paired controller has gone stale (no authenticated
    requests in ``_STALE_TIMEOUT`` seconds).
    """
    global _current_code, _timer, _rotation_start
    with _code_lock:
        _current_code = str(secrets.randbelow(1_000_000)).zfill(6)
        _rotation_start = time.monotonic()
        code = _current_code
        _timer = threading.Timer(_CODE_INTERVAL, _rotate)
        _timer.daemon = True
        _timer.start()
    if not _paired:
        _print_code(code)
    elif _is_stale():
        print("  [ STALE ] No controller activity — pairing codes resumed.")
        _print_code(code)
    logger.debug("Pairing code rotated")


def start_rotation() -> None:
    """Start the pairing code rotation. Idempotent — safe to call multiple times."""
    global _started
    if _started:
        return
    _started = True
    _rotate()


def stop_rotation() -> None:
    """Cancel the rotation timer. Called on server shutdown."""
    global _timer, _started
    _started = False
    with _code_lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None


def get_current_code() -> str:
    """Return the current pairing code (6 decimal digits, zero-padded)."""
    with _code_lock:
        return _current_code


def seconds_until_rotation() -> float:
    """Return the number of seconds remaining until the next code rotation."""
    with _code_lock:
        elapsed = time.monotonic() - _rotation_start
    return max(0.0, _CODE_INTERVAL - elapsed)


def verify_code(candidate: str) -> bool:
    """Constant-time comparison of candidate against the current code."""
    with _code_lock:
        current = _current_code
    if not current:
        return False
    return secrets.compare_digest(candidate.strip(), current)


def is_paired() -> bool:
    """Return True if this device is currently paired with a controller."""
    return _paired


def touch() -> None:
    """Record that an authenticated request was received.

    Called by the auth middleware on every successful Bearer-token validation.
    Resets the staleness timer so the device knows its controller is still active.
    """
    global _last_auth_time
    _last_auth_time = time.monotonic()


def _is_stale() -> bool:
    """Return True if paired but no authenticated requests in ``_STALE_TIMEOUT``."""
    if not _paired or _last_auth_time == 0.0:
        return False
    return (time.monotonic() - _last_auth_time) > _STALE_TIMEOUT


def set_paired() -> None:
    """Mark this device as paired. Code rotation continues silently."""
    global _paired
    _paired = True
    try:
        os.makedirs(_PAIRED_DIR, exist_ok=True)
        with open(_PAIRED_FILE, "w") as f:
            f.write("")
        os.chmod(_PAIRED_FILE, 0o600)
    except OSError:
        logger.warning("Could not persist paired state to %s", _PAIRED_FILE, exc_info=True)
    print("  [ PAIRED ] Pairing codes suppressed (codes still rotate internally).")
    logger.info("Device paired — pairing code printing suppressed")


def set_unpaired() -> None:
    """Clear the paired state. Resumes printing codes to stdout."""
    global _paired
    _paired = False
    try:
        os.remove(_PAIRED_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Could not remove paired state file %s", _PAIRED_FILE, exc_info=True)
    # Rotation is already running (codes rotate even when paired), so just
    # flip the flag — the next _rotate() call will print the code.
    print("  [ UNPAIRED ] Pairing codes re-enabled.")
    logger.info("Device unpaired — pairing codes re-enabled")
