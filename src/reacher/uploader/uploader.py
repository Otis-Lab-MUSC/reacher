"""Firmware upload via avrdude.

Resolves hex file paths for both development and frozen (PyInstaller) modes,
spawns avrdude as a subprocess, and streams progress to a callback.
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sys
import time
import urllib.request
import warnings
from typing import Callable, Dict, List, Optional

from .boards import BOARD_PROFILES, DEFAULT_BOARD, get_board_profile

logger = logging.getLogger(__name__)

PARADIGMS = ("fr", "pr", "vi", "omission", "pavlovian")

# GitHub source for pre-compiled hex files.  Set REACHER_SKIP_HEX_FETCH=1 to
# disable network fetching (airgapped deployments must pre-populate hex dirs).
# Firmware source and hex artifacts live in this repo (firmware/ +
# src/reacher/hex/) since the reacher-firmware repo was archived.
_FIRMWARE_REPO = "Otis-Lab-MUSC/reacher"
_FIRMWARE_BRANCH = "develop"  # Will migrate to "main" in a future pass
_FIRMWARE_RAW_BASE = f"https://raw.githubusercontent.com/{_FIRMWARE_REPO}/{_FIRMWARE_BRANCH}/src/reacher/hex"
_HEX_CACHE_DIR = os.path.expanduser("~/.reacher/hex")
_CACHE_MAX_AGE_S = 86400  # 24 hours — re-download cached hex files older than this


def _dir_has_hex(path: str) -> bool:
    """Return True if *path* contains at least one ``.hex`` file.

    Checks both flat layout (``hex/fr.hex``) and board-aware subdirectories
    (``hex/uno/fr.hex``).
    """
    try:
        for entry in os.listdir(path):
            if entry.endswith(".hex"):
                return True
            subdir = os.path.join(path, entry)
            if os.path.isdir(subdir):
                if any(f.endswith(".hex") for f in os.listdir(subdir)):
                    return True
    except OSError:
        pass
    return False


def _frozen_base() -> Optional[str]:
    """Return the PyInstaller bundle directory, or None when running from source."""
    return getattr(sys, "_MEIPASS", None)


def _fetch_hex_from_github() -> Optional[str]:
    """Download all hex files from GitHub and cache them in ~/.reacher/hex/.

    The ``reacher`` repo ships hex artifacts as package data in a board-aware
    subdirectory layout (``src/reacher/hex/{board}/{paradigm}.hex``).
    Downloaded files are stored in the matching local cache at
    ``~/.reacher/hex/{board}/{paradigm}.hex``.

    Returns the cache directory path on success, None if fetching is disabled
    (``REACHER_SKIP_HEX_FETCH=1``) or if all downloads fail.
    """
    if os.getenv("REACHER_SKIP_HEX_FETCH"):
        return None

    failed = 0
    total = 0

    for board in BOARD_PROFILES:
        board_dir = os.path.join(_HEX_CACHE_DIR, board)
        os.makedirs(board_dir, exist_ok=True)

        for paradigm in PARADIGMS:
            dest = os.path.join(board_dir, f"{paradigm}.hex")
            if os.path.isfile(dest):
                age = time.time() - os.path.getmtime(dest)
                if age < _CACHE_MAX_AGE_S:
                    continue  # Fresh enough
                logger.info("Cached %s/%s.hex is %.1fh old — re-downloading", board, paradigm, age / 3600)
            url = f"{_FIRMWARE_RAW_BASE}/{board}/{paradigm}.hex"
            total += 1
            try:
                urllib.request.urlretrieve(url, dest)
                logger.info("Fetched firmware hex: %s/%s.hex", board, paradigm)
            except Exception as exc:
                failed += 1
                logger.warning("Could not fetch %s/%s.hex from GitHub: %s", board, paradigm, exc)

    if total > 0 and failed == total:
        # Every attempted download failed — don't return a broken cache dir
        return None

    return _HEX_CACHE_DIR if os.path.isdir(_HEX_CACHE_DIR) else None


class FirmwareUploader:
    """Upload pre-compiled .hex firmware to an Arduino via avrdude."""

    def __init__(self, hex_dir: Optional[str] = None, avrdude_path: Optional[str] = None) -> None:
        self.hex_dir = hex_dir or self._resolve_hex_dir()
        self.avrdude_path = avrdude_path or self._resolve_avrdude()
        self.avrdude_conf = self._resolve_avrdude_conf()
        self.last_error: str = ""  # stderr from the most recent failed upload

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_hex_dir() -> str:
        base = _frozen_base()
        if base:
            return os.path.join(base, "hex")
        # Dev mode: check env var first
        env_dir = os.environ.get("REACHER_HEX_DIR", "")
        if env_dir and os.path.isdir(env_dir):
            logger.info("Hex dir from REACHER_HEX_DIR: %s", env_dir)
            return env_dir

        # Package data: hex files bundled inside the reacher package.
        # This is the CANONICAL source — checked BEFORE CWD-relative paths
        # to prevent stale submodules or old checkouts from shadowing fixes.
        pkg_hex = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hex")

        candidates = [
            # Package data (pip-installed with hex files as package data) — CANONICAL
            ("package-data", pkg_hex),
            # CWD-relative (legacy layout: running from a checkout that keeps
            # hex under firmware/hex)
            ("cwd/firmware/hex", os.path.join(os.getcwd(), "firmware", "hex")),
            # Home directory fallback
            ("~/REACHER/hex", os.path.expanduser("~/REACHER/hex")),
            # GitHub-fetched cache (populated by _fetch_hex_from_github)
            ("~/.reacher/hex", _HEX_CACHE_DIR),
        ]

        chosen = None
        for label, c in candidates:
            norm = os.path.normpath(c)
            exists = os.path.isdir(norm)
            has_hex = _dir_has_hex(norm) if exists else False
            logger.debug("Hex candidate %-30s exists=%-5s has_hex=%-5s path=%s", label, exists, has_hex, norm)
            if chosen is None and exists and has_hex:
                chosen = (label, norm)

        if chosen:
            logger.info("Resolved hex dir: [%s] %s", chosen[0], chosen[1])
            return chosen[1]

        # No local hex directory found — try fetching from GitHub once.
        # This handles remote Pis that only have `pip install reacher` with no
        # firmware submodule or bundled hex files.
        fetched = _fetch_hex_from_github()
        if fetched:
            logger.info("Using GitHub-fetched hex files from %s", fetched)
            return fetched

        fallback = os.path.normpath(candidates[0][1])
        logger.warning("No hex directory found; returning default: %s", fallback)
        return fallback

    @staticmethod
    def _resolve_avrdude() -> str:
        base = _frozen_base()
        if base:
            name = "avrdude.exe" if sys.platform == "win32" else "avrdude"
            bundled = os.path.join(base, "avrdude", name)
            if os.path.isfile(bundled):
                return bundled
        # Fall back to system PATH
        found = shutil.which("avrdude")
        if found:
            return found
        return "avrdude"

    @staticmethod
    def _resolve_avrdude_conf() -> Optional[str]:
        """Return the path to avrdude.conf when running frozen, or None.

        In a PyInstaller bundle the config file is placed alongside the
        avrdude binary at ``_MEIPASS/avrdude/avrdude.conf``.  System-installed
        avrdude uses a compiled-in config path, so no override is needed in
        development mode.
        """
        base = _frozen_base()
        if base:
            bundled = os.path.join(base, "avrdude", "avrdude.conf")
            if os.path.isfile(bundled):
                return bundled
        return None

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

    @staticmethod
    def cache_hex(paradigm: str, board: str, data: bytes) -> str:
        """Write hex data to the local cache and return the file path.

        Stores at ``~/.reacher/hex/{board}/{paradigm}.hex`` so subsequent
        uploads can resolve the file through the normal fallback chain.
        """
        paradigm = paradigm.lower()
        board = board.lower()
        dest_dir = os.path.join(_HEX_CACHE_DIR, board)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"{paradigm}.hex")
        with open(dest, "wb") as f:
            f.write(data)
        logger.info("Cached firmware hex: %s/%s.hex (%d bytes)", board, paradigm, len(data))
        return dest

    async def upload(
        self,
        paradigm: str,
        port: str,
        board: str = DEFAULT_BOARD,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        hex_path: Optional[str] = None,
    ) -> bool:
        """Upload firmware asynchronously.

        Args:
            paradigm: One of PARADIGMS.
            port: Serial port (e.g. ``/dev/ttyUSB0`` or ``COM3``).
            board: Board identifier (e.g. ``"uno"``, ``"mega"``).
            progress_callback: Optional ``(percent, stage_message)`` callable.
            hex_path: If provided, use this path directly instead of resolving
                via :meth:`get_hex_path`.  Used when the client supplied inline
                hex data that was cached to disk.

        Returns:
            True on success, False on failure.
        """
        if hex_path is None:
            hex_path = self.get_hex_path(paradigm, board)
        profile = get_board_profile(board)

        # Log hex file identity for deployment auditing
        try:
            hex_size = os.path.getsize(hex_path)
            with open(hex_path, "rb") as hf:
                hex_hash = hashlib.sha256(hf.read()).hexdigest()[:16]
            logger.info(
                "Firmware hex: %s  board=%s  paradigm=%s  size=%d  sha256=%s",
                hex_path, board, paradigm, hex_size, hex_hash,
            )
        except OSError as exc:
            logger.warning("Could not read hex file metadata: %s", exc)

        # Pre-flight: verify avrdude is reachable before spawning subprocess
        if os.path.isabs(self.avrdude_path):
            if not os.path.isfile(self.avrdude_path):
                raise RuntimeError(
                    f"avrdude not found at configured path: {self.avrdude_path}"
                )
        else:
            if not shutil.which(self.avrdude_path):
                raise RuntimeError(
                    "avrdude not found in PATH. Install it with: "
                    "sudo apt-get install avrdude"
                )

        cmd = [self.avrdude_path]
        if self.avrdude_conf:
            cmd.extend(["-C", self.avrdude_conf])
        cmd.extend([
            *profile.avrdude_args,
            "-P", port,
            "-U", f"flash:w:{hex_path}:i",
        ])

        logger.info("Running: %s", " ".join(cmd))
        if progress_callback:
            progress_callback(0, "Starting upload")

        # In frozen mode, ensure avrdude can locate companion DLLs that
        # PyInstaller may have placed in the bundle root or alongside the
        # binary.  Add both directories to PATH for the subprocess.
        sub_env = None
        base = _frozen_base()
        if base and sys.platform == "win32":
            sub_env = os.environ.copy()
            avrdude_dir = os.path.dirname(self.avrdude_path)
            extra = os.pathsep.join([avrdude_dir, base])
            sub_env["PATH"] = extra + os.pathsep + sub_env.get("PATH", "")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sub_env,
        )

        # avrdude prints progress to stderr — collect all lines so we can
        # log the full output at ERROR level if the upload fails.
        percent = 0
        stderr_lines: list[str] = []
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if text:
                stderr_lines.append(text)
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
            stderr_full = "\n".join(stderr_lines)
            self.last_error = (
                f"avrdude exited {proc.returncode}.\n"
                f"stderr: {stderr_full or '(empty)'}\n"
                f"stdout: {stdout or '(empty)'}"
            )
            logger.error(self.last_error)
            if progress_callback:
                progress_callback(percent, f"Failed (exit {proc.returncode})")
            return False
