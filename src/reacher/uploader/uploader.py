"""Firmware upload via avrdude.

Resolves hex file paths for both development and frozen (PyInstaller) modes,
spawns avrdude as a subprocess, and streams progress to a callback.
"""

import asyncio
import logging
import os
import re
import shutil
import sys
import warnings
from typing import Callable, Dict, List, Optional

from .boards import BOARD_PROFILES, DEFAULT_BOARD, get_board_profile

logger = logging.getLogger(__name__)

PARADIGMS = ("fr", "pr", "vi", "omission", "pavlovian")


def _frozen_base() -> Optional[str]:
    """Return the PyInstaller bundle directory, or None when running from source."""
    return getattr(sys, "_MEIPASS", None)


class FirmwareUploader:
    """Upload pre-compiled .hex firmware to an Arduino via avrdude."""

    def __init__(self, hex_dir: Optional[str] = None, avrdude_path: Optional[str] = None) -> None:
        self.hex_dir = hex_dir or self._resolve_hex_dir()
        self.avrdude_path = avrdude_path or self._resolve_avrdude()

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_hex_dir() -> str:
        base = _frozen_base()
        if base:
            return os.path.join(base, "hex")
        # Dev mode: check env var first, then CWD-relative, then home fallback
        env_dir = os.environ.get("REACHER_HEX_DIR", "")
        if env_dir and os.path.isdir(env_dir):
            return env_dir
        candidates = [
            os.path.join(os.getcwd(), "firmware", "hex"),
            os.path.expanduser("~/REACHER/hex"),
        ]
        for c in candidates:
            norm = os.path.normpath(c)
            if os.path.isdir(norm):
                return norm
        return os.path.normpath(candidates[0])

    @staticmethod
    def _resolve_avrdude() -> str:
        base = _frozen_base()
        if base:
            bundled = os.path.join(base, "avrdude", "avrdude")
            if os.path.isfile(bundled):
                return bundled
        # Fall back to system PATH
        found = shutil.which("avrdude")
        if found:
            return found
        return "avrdude"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_hex_path(self, paradigm: str, board: str = DEFAULT_BOARD) -> str:
        """Return the absolute path to the .hex file for *paradigm* and *board*.

        Looks for ``hex/{board}/{paradigm}.hex`` first.  Falls back to the
        flat ``hex/{paradigm}.hex`` layout only when *board* is ``"uno"``
        (backwards compatibility with pre-board-aware hex directories).

        Raises:
            ValueError: If *paradigm* is unknown or *board* is unsupported.
            FileNotFoundError: If the hex file does not exist.
        """
        paradigm = paradigm.lower()
        if paradigm not in PARADIGMS:
            raise ValueError(f"Unknown paradigm: {paradigm!r}. Must be one of {PARADIGMS}")
        get_board_profile(board)  # validates board

        # Preferred: board-specific subdirectory
        board_path = os.path.join(self.hex_dir, board.lower(), f"{paradigm}.hex")
        if os.path.isfile(board_path):
            return board_path

        # Fallback: flat layout (uno only, for backwards compat)
        if board.lower() == "uno":
            flat_path = os.path.join(self.hex_dir, f"{paradigm}.hex")
            if os.path.isfile(flat_path):
                warnings.warn(
                    f"Using flat hex layout ({flat_path}). "
                    "Migrate to hex/uno/{paradigm}.hex.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return flat_path

        raise FileNotFoundError(
            f"Hex file not found for board={board!r}, paradigm={paradigm!r}. "
            f"Expected: {board_path}"
        )

    def list_available(self, board: str = DEFAULT_BOARD) -> List[str]:
        """Return paradigms whose hex files are present on disk for *board*."""
        available = []
        for p in PARADIGMS:
            try:
                self.get_hex_path(p, board)
                available.append(p)
            except (FileNotFoundError, ValueError):
                pass
        return available

    @staticmethod
    def list_boards() -> List[Dict[str, str]]:
        """Return supported board types as a list of dicts."""
        return [
            {"id": profile.board_id, "name": profile.display_name}
            for profile in BOARD_PROFILES.values()
        ]

    async def upload(
        self,
        paradigm: str,
        port: str,
        board: str = DEFAULT_BOARD,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> bool:
        """Upload firmware asynchronously.

        Args:
            paradigm: One of PARADIGMS.
            port: Serial port (e.g. ``/dev/ttyUSB0`` or ``COM3``).
            board: Board identifier (e.g. ``"uno"``, ``"mega"``).
            progress_callback: Optional ``(percent, stage_message)`` callable.

        Returns:
            True on success, False on failure.
        """
        hex_path = self.get_hex_path(paradigm, board)
        profile = get_board_profile(board)

        cmd = [
            self.avrdude_path,
            *profile.avrdude_args,
            "-P", port,
            "-U", f"flash:w:{hex_path}:i",
        ]

        logger.info("Running: %s", " ".join(cmd))
        if progress_callback:
            progress_callback(0, "Starting upload")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # avrdude prints progress to stderr
        percent = 0
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            logger.debug("avrdude: %s", text)

            # Parse progress markers like "Writing | ################################################## | 100%"
            match = re.search(r"(\d+)%", text)
            if match:
                new_pct = int(match.group(1))
                if new_pct > percent:
                    percent = new_pct
                    stage = "Writing" if "Writing" in text else "Reading" if "Reading" in text else "Uploading"
                    if progress_callback:
                        progress_callback(percent, stage)

        # Fix: F-002 — avrdude can hang indefinitely on a bad USB connection; enforce timeout
        try:
            await asyncio.wait_for(proc.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error("avrdude timed out after 120s — process killed")
            if progress_callback:
                progress_callback(percent, "Failed (timeout)")
            return False

        if proc.returncode == 0:
            logger.info("Firmware upload succeeded for %s on %s", paradigm, port)
            if progress_callback:
                progress_callback(100, "Complete")
            return True
        else:
            stdout = (await proc.stdout.read()).decode(errors="replace") if proc.stdout else ""
            logger.error("avrdude exited %d. stdout=%s", proc.returncode, stdout)
            if progress_callback:
                progress_callback(percent, f"Failed (exit {proc.returncode})")
            return False
