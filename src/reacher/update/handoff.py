"""Find the install folder and hand the file swap to the launcher.

The backend cannot replace the folder it is running from, so it stages the new
copy and starts the launcher's ``--apply-update`` helper, which waits for this
process to exit, swaps, and relaunches.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from ..child_env import clean_child_env

STAGING_PREFIX = ".labrynth-update_"
MACOS_APP_SUFFIX = ".app"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def find_install_dir() -> Path | None:
    """Return the folder the frozen app runs from, or None outside a bundle."""
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if sys.platform != "darwin":
        return executable.parent
    app = next((parent for parent in executable.parents if parent.suffix == MACOS_APP_SUFFIX), None)
    return app


def create_staging_root(install_dir: Path) -> Path:
    """Create a staging folder beside install_dir so the swap is one rename on one filesystem."""
    return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=install_dir.parent))


def staged_launcher(executable: Path, install_dir: Path, staged_dir: Path) -> Path:
    """Return the staged copy of the running executable, which runs the swap.

    The helper is the new version's launcher, so it can move the install folder
    aside without being inside it. Raises ValueError if executable is not under
    install_dir.
    """
    return staged_dir / executable.relative_to(install_dir)


def build_command(launcher: Path, staged_dir: Path, install_dir: Path, pid: int) -> list[str]:
    """Return the argument list for the launcher's apply-update helper."""
    return [
        str(launcher),
        "--apply-update",
        "--staged",
        str(staged_dir),
        "--install",
        str(install_dir),
        "--pid",
        str(pid),
        "--relaunch",
    ]


def spawn_helper(command: list[str]) -> None:
    """Start command detached, so it outlives this process."""
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": clean_child_env(),
    }
    if sys.platform == "win32":
        options["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)  # type: ignore[call-overload]
