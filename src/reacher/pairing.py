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
_paired = False


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
    """Generate a new pairing code and schedule the next rotation."""
    global _current_code, _timer
    if _paired:
        return
    with _code_lock:
        _current_code = str(secrets.randbelow(1_000_000)).zfill(6)
        code = _current_code
        _timer = threading.Timer(_CODE_INTERVAL, _rotate)
        _timer.daemon = True
        _timer.start()
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


def set_paired() -> None:
    """Mark this device as paired. Stops code rotation."""
    global _paired
    _paired = True
    stop_rotation()
    print("  [ PAIRED ] Pairing codes disabled.")
    logger.info("Device paired — pairing codes disabled")


def set_unpaired() -> None:
    """Clear the paired state. Resumes code rotation."""
    global _paired, _started
    _paired = False
    _started = False  # Allow start_rotation() to re-enter
    start_rotation()
    print("  [ UNPAIRED ] Pairing codes re-enabled.")
    logger.info("Device unpaired — pairing codes re-enabled")
