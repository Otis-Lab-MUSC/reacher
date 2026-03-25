"""Rotating 6-digit pairing code for zero-config machine discovery.

Generates a new numeric code every 5 minutes and prints it to stdout.
The /api/pairing/claim endpoint validates submitted codes without exposing
the API key through mDNS advertisements or QR codes.
"""

import logging
import secrets
import threading

logger = logging.getLogger(__name__)

_CODE_INTERVAL = 300  # seconds (5 minutes)
_current_code: str = ""
_code_lock = threading.Lock()
_timer: threading.Timer | None = None
_started = False


def _rotate() -> None:
    """Generate a new pairing code and schedule the next rotation."""
    global _current_code, _timer
    with _code_lock:
        _current_code = str(secrets.randbelow(1_000_000)).zfill(6)
        _timer = threading.Timer(_CODE_INTERVAL, _rotate)
        _timer.daemon = True
        _timer.start()
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


def verify_code(candidate: str) -> bool:
    """Constant-time comparison of candidate against the current code."""
    with _code_lock:
        current = _current_code
    if not current:
        return False
    return secrets.compare_digest(candidate.strip(), current)
