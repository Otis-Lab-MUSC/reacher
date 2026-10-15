"""Committed firmware hex under src/reacher/hex/ must be current and fit its board.

Needs no toolchain: the version stamp, size and file set are read straight from
the Intel HEX. Byte-for-byte equality with the firmware source is checked by the
``firmware`` CI job (compile.sh + git diff).
"""

from pathlib import Path

import pytest

import reacher

HEX_ROOT = Path(__file__).resolve().parent.parent / "src" / "reacher" / "hex"
FIX = "run `bash firmware/compile.sh` and commit the refreshed hex under src/reacher/hex/"

FLASH_BYTES = {"uno": 32256, "mega": 253952}
EXPECTED = {
    "mega": {"fr", "pr", "vi", "omission", "pavlovian"},
    "uno": {"fr_lite", "pr_lite", "vi_lite", "omission_lite"},
}
HEX_FILES = sorted(HEX_ROOT.glob("*/*.hex"))


def decode_hex(path: Path) -> bytes:
    """Return the program bytes of an Intel HEX file (data records only), checksums verified."""
    out = bytearray()
    saw_eof = False
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        assert line.startswith(":"), f"{path}:{lineno}: record does not start with ':'"
        rec = bytes.fromhex(line[1:])
        assert len(rec) == rec[0] + 5, f"{path}:{lineno}: length field disagrees with record size"
        assert sum(rec) & 0xFF == 0, f"{path}:{lineno}: bad checksum"
        if rec[3] == 0x00:
            out += rec[4:-1]
        elif rec[3] == 0x01:
            saw_eof = True
    assert saw_eof, f"{path}: no end-of-file record"
    return bytes(out)


def _id(p: Path) -> str:
    return f"{p.parent.name}/{p.name}"


def test_hex_files_found():
    assert HEX_FILES, f"no hex files under {HEX_ROOT}"


@pytest.mark.parametrize("path", HEX_FILES, ids=_id)
def test_decodes_with_valid_checksums(path):
    assert decode_hex(path), f"{_id(path)} has no data records"


@pytest.mark.parametrize("path", HEX_FILES, ids=_id)
def test_version_stamp_matches_package(path):
    data = decode_hex(path)
    marker = b'"version":"v'
    count = data.count(marker)
    assert count == 1, f"{_id(path)} has {count} version stamps, expected 1"
    start = data.index(marker) + len(b'"version":"')
    stamp = data[start : data.index(b'"', start)].decode()
    assert stamp == f"v{reacher.__version__}", (
        f"{_id(path)} is stamped {stamp}, package is v{reacher.__version__}: {FIX}"
    )


@pytest.mark.parametrize("path", HEX_FILES, ids=_id)
def test_fits_board_flash(path):
    board = path.parent.name
    assert board in FLASH_BYTES, f"unknown board directory {board!r}: no flash budget"
    size = len(decode_hex(path))
    assert size <= FLASH_BYTES[board], (
        f"{_id(path)} is {size} bytes, over the {board} budget of {FLASH_BYTES[board]}; "
        f"shrink the sketch, then {FIX}"
    )


def test_file_set_per_board():
    actual: dict[str, set[str]] = {}
    for p in HEX_FILES:
        actual.setdefault(p.parent.name, set()).add(p.stem)
    assert actual == EXPECTED, (
        f"hex files per board differ from compile.sh's output. extra: "
        f"{ {b: sorted(actual.get(b, set()) - EXPECTED.get(b, set())) for b in actual} }, missing: "
        f"{ {b: sorted(EXPECTED[b] - actual.get(b, set())) for b in EXPECTED} }. "
        f"Delete stale files with git rm, or {FIX}"
    )
