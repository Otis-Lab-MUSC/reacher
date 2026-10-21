"""Tracked paths must be checkout-safe on Windows.

Windows refuses to create files whose base name is a reserved device name
(CON, PRN, AUX, NUL, COM1-9, LPT1-9, with or without an extension), and
labrynth's Windows release job clones this repo at the release tag. A single
such path fails that clone with ``invalid path`` after the tag is already public.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)
BAD_CHARS = re.compile(r'[<>:"|?*]')


def _tracked_paths() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.splitlines() if p]


def _problem(path: str) -> str | None:
    if BAD_CHARS.search(path):
        return "character not allowed in Windows paths"
    for part in path.split("/"):
        if RESERVED.match(part.split(".")[0]):
            return f"reserved Windows device name: {part}"
        if part.endswith((".", " ")):
            return f"trailing dot or space: {part!r}"
    return None


def test_tracked_paths_are_windows_safe():
    bad = {p: why for p in _tracked_paths() if (why := _problem(p))}
    assert not bad, f"paths that cannot be checked out on Windows: {bad}"


@pytest.mark.parametrize(
    "path",
    ["firmware/x/Aux.h", "a/AUX.cpp", "a/nul", "a/com1.txt", "web/src/lib/aux.ts", "a/b.", "a/b:c"],
)
def test_detector_flags_known_bad_paths(path):
    assert _problem(path) is not None


@pytest.mark.parametrize("path", ["firmware/x/AuxOutput.h", "web/src/lib/auxOutput.ts", "a/console.py"])
def test_detector_allows_safe_paths(path):
    assert _problem(path) is None
