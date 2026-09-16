"""Tests for the avrdude argv FirmwareUploader builds, and for ``-C``.

The Linux bundle shipped an avrdude binary with no ``avrdude.conf`` for
several releases and nothing noticed, because ``tests/test_uploader.py``
covers hex-path resolution only — argv construction, ``-C`` and frozen mode
had zero coverage.  These tests close that gap.

Frozen mode is simulated by setting ``sys._MEIPASS``; argv is captured by
replacing ``asyncio.create_subprocess_exec``, so nothing here ever spawns a
process or touches a serial port.
"""

import asyncio
import logging
import os
import sys

import pytest

from reacher.uploader.uploader import FirmwareUploader

BIN_NAME = "avrdude.exe" if sys.platform == "win32" else "avrdude"

# ``-p atmega2560 -c wiring -D -b 115200`` — boards.BOARD_PROFILES["mega"]
MEGA_ARGS = ["-p", "atmega2560", "-c", "wiring", "-D", "-b", "115200"]


class _FakeStream:
    """Minimal stand-in for ``proc.stderr`` / ``proc.stdout``."""

    def __init__(self, lines: list[bytes] | None = None) -> None:
        self._lines = list(lines or [])

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self) -> bytes:
        return b""


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stderr = _FakeStream()
        self.stdout = _FakeStream()

    async def wait(self) -> int:
        return self.returncode


def _capture_argv(monkeypatch, returncode: int = 0) -> list:
    """Replace subprocess spawning; return a list that receives the argv."""
    captured: list = []

    async def fake_exec(*cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeProc(returncode)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


def _fake_binary(path) -> str:
    """Create a stand-in avrdude executable at *path* and return its str path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"#!/bin/false\n")
    path.chmod(0o755)
    return str(path)


def _hex_file(tmp_path) -> str:
    hex_path = tmp_path / "fr.hex"
    hex_path.write_bytes(b":00000001FF\n")
    return str(hex_path)


def _unfreeze(monkeypatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def _freeze(monkeypatch, base) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(base), raising=False)


async def _run_upload(uploader, tmp_path, monkeypatch, returncode: int = 0):
    captured = _capture_argv(monkeypatch, returncode)
    ok = await uploader.upload("fr", "/dev/ttyUSB0", "mega", hex_path=_hex_file(tmp_path))
    assert captured, "create_subprocess_exec was never called"
    return captured[0], ok


# ---------------------------------------------------------------------------
# (a) Development mode — the argv must be byte-identical to pre-fix behaviour
# ---------------------------------------------------------------------------


async def test_dev_mode_passes_no_conf_and_no_C(tmp_path, monkeypatch):
    """No ``_MEIPASS`` → no conf, no ``-C``: system avrdude reads its own
    compiled-in config, which matches the binary the host installed."""
    _unfreeze(monkeypatch)
    host_bin = _fake_binary(tmp_path / "host" / BIN_NAME)
    uploader = FirmwareUploader(hex_dir=str(tmp_path), avrdude_path=host_bin)

    assert uploader.avrdude_conf is None
    assert uploader.avrdude_conf_provenance["status"] == "not-frozen"

    argv, ok = await _run_upload(uploader, tmp_path, monkeypatch)
    assert ok is True
    assert "-C" not in argv
    # Spelled out in full rather than asserted piecewise: this exact list is
    # what dev installs ran before the conf-coupling work, and keeping it so
    # is the whole blast-radius claim for pip/editable users.
    assert argv == [
        host_bin,
        *MEGA_ARGS,
        "-P", "/dev/ttyUSB0",
        "-U", f"flash:w:{_hex_file(tmp_path)}:i",
    ]


# ---------------------------------------------------------------------------
# (b) Frozen with a bundled conf — the hermetic case
# ---------------------------------------------------------------------------


async def test_frozen_with_bundled_conf_passes_C_once_right_after_binary(tmp_path, monkeypatch):
    bundled_bin = _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    conf = tmp_path / "avrdude" / "avrdude.conf"
    conf.write_text("# bundled conf\n")
    _freeze(monkeypatch, tmp_path)

    uploader = FirmwareUploader(hex_dir=str(tmp_path))
    assert uploader.avrdude_path == bundled_bin
    assert uploader.avrdude_conf == str(conf)
    assert uploader.avrdude_conf_provenance["status"] == "bundled"

    argv, _ = await _run_upload(uploader, tmp_path, monkeypatch)
    # Exactly once, and immediately after the binary: avrdude takes the last
    # -C given, so a second one silently wins and placement is not cosmetic.
    assert argv.count("-C") == 1
    assert argv[:3] == [bundled_bin, "-C", str(conf)]


@pytest.mark.parametrize("subpath", [
    ("avrdude", "avrdude.conf"),
    ("avrdude", "etc", "avrdude.conf"),
    ("etc", "avrdude.conf"),
])
async def test_frozen_conf_found_in_each_supported_layout(tmp_path, monkeypatch, subpath):
    """The search covers every layout the packaging spec has used, so moving
    the conf cannot quietly drop the bundle back onto the host's config."""
    _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    conf = tmp_path.joinpath(*subpath)
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("# bundled conf\n")
    _freeze(monkeypatch, tmp_path)

    uploader = FirmwareUploader(hex_dir=str(tmp_path))
    assert uploader.avrdude_conf == str(conf)


# ---------------------------------------------------------------------------
# (c) Frozen without a bundled conf — the shipped Linux defect
# ---------------------------------------------------------------------------


async def test_frozen_without_bundled_conf_logs_and_omits_C(tmp_path, monkeypatch, caplog):
    """This is what v3.0.1-alpha.24's linux-x64 bundle actually contained:
    ``_internal/avrdude/{avrdude,ld.so}`` and no conf."""
    bundled_bin = _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    _freeze(monkeypatch, tmp_path)

    uploader = FirmwareUploader(hex_dir=str(tmp_path))
    assert uploader.avrdude_path == bundled_bin
    assert uploader.avrdude_conf is None
    assert uploader.avrdude_conf_provenance["status"] == "missing"

    with caplog.at_level(logging.ERROR, logger="reacher.uploader.uploader"):
        argv, _ = await _run_upload(uploader, tmp_path, monkeypatch)

    assert "-C" not in argv
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "no bundled avrdude.conf" in logged, f"expected the missing-conf error; got {logged!r}"
    assert "/etc/avrdude.conf" in logged


async def test_missing_conf_fact_is_folded_into_last_error(tmp_path, monkeypatch):
    """firmware.py:133 hands ``last_error`` straight to the UI as the 500
    detail, so the next bug report should explain its own cause."""
    _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    _freeze(monkeypatch, tmp_path)

    uploader = FirmwareUploader(hex_dir=str(tmp_path))
    _, ok = await _run_upload(uploader, tmp_path, monkeypatch, returncode=1)

    assert ok is False
    assert "no bundled avrdude.conf" in uploader.last_error
    assert "version-mismatched host conf" in uploader.last_error


async def test_missing_conf_does_not_raise(tmp_path, monkeypatch):
    """Logging, not raising: a deliberate host-avrdude setup is legitimate,
    and raising would move the endpoint from 500 to the 409 tool-missing
    branch (tests/test_api.py::TestFirmwareUploadEndpoint)."""
    _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    _freeze(monkeypatch, tmp_path)
    uploader = FirmwareUploader(hex_dir=str(tmp_path))
    _, ok = await _run_upload(uploader, tmp_path, monkeypatch)
    assert ok is True


# ---------------------------------------------------------------------------
# (d) Conf/binary coupling — never a mixed pair
# ---------------------------------------------------------------------------


async def test_bundled_conf_unused_when_bundled_binary_is_absent(tmp_path, monkeypatch):
    """A bundle whose conf survived but whose binary did not must fall back
    to the host's avrdude *and* the host's conf.  Handing a bundled 8.1 conf
    to a host 7.1 avrdude is the same failure inverted: avrdude errors with
    "unable to process system wide configuration file" and exits 1."""
    conf = tmp_path / "avrdude" / "avrdude.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("# bundled conf\n")
    host_bin = _fake_binary(tmp_path / "host" / BIN_NAME)
    _freeze(monkeypatch, tmp_path)

    uploader = FirmwareUploader(hex_dir=str(tmp_path), avrdude_path=host_bin)
    assert uploader.avrdude_conf is None
    assert uploader.avrdude_conf_provenance["status"] == "host-binary"

    argv, _ = await _run_upload(uploader, tmp_path, monkeypatch)
    assert "-C" not in argv
    assert str(conf) not in argv


# ---------------------------------------------------------------------------
# (e) Golden negative — -N must never appear
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bundle_conf", [True, False])
async def test_N_noconfig_is_never_passed(tmp_path, monkeypatch, bundle_conf):
    """``-N`` is ``--noconfig``: it suppresses the *user* rc file
    (``~/.avrduderc``) and has never had any effect on ``/etc/avrdude.conf``
    (avrdude ``src/main.c:729, 839-840, 961``).  ``-C`` already replaces the
    system config outright (``main.c:942-944``), so ``-N`` buys nothing — and
    it does not exist before avrdude 7.3 (``git show v7.2:src/main.c`` has no
    ``case 'N'``), while the Linux bundle ships 7.1.  Passing it would turn
    every Linux upload into ``invalid option -N``.  Do not re-add it.
    """
    _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    if bundle_conf:
        (tmp_path / "avrdude" / "avrdude.conf").write_text("# bundled conf\n")
    _freeze(monkeypatch, tmp_path)

    uploader = FirmwareUploader(hex_dir=str(tmp_path))
    argv, _ = await _run_upload(uploader, tmp_path, monkeypatch)

    assert "-N" not in argv
    assert "--noconfig" not in argv


async def test_N_noconfig_is_never_passed_in_dev_mode(tmp_path, monkeypatch):
    _unfreeze(monkeypatch)
    uploader = FirmwareUploader(
        hex_dir=str(tmp_path), avrdude_path=_fake_binary(tmp_path / "host" / BIN_NAME)
    )
    argv, _ = await _run_upload(uploader, tmp_path, monkeypatch)
    assert "-N" not in argv and "--noconfig" not in argv


# ---------------------------------------------------------------------------
# Diagnostics provenance (B4's data source)
# ---------------------------------------------------------------------------


def test_provenance_lists_every_candidate_when_none_matched(tmp_path, monkeypatch):
    """A null ``avrdude_conf`` in /api/firmware/diagnostics must be
    self-explanatory — including which paths were tried."""
    _fake_binary(tmp_path / "avrdude" / BIN_NAME)
    _freeze(monkeypatch, tmp_path)

    prov = FirmwareUploader(hex_dir=str(tmp_path)).avrdude_conf_provenance
    tried = [c["path"] for c in prov["candidates"]]
    assert tried == [
        os.path.join(str(tmp_path), "avrdude", "avrdude.conf"),
        os.path.join(str(tmp_path), "avrdude", "etc", "avrdude.conf"),
        os.path.join(str(tmp_path), "etc", "avrdude.conf"),
    ]
    assert all(c["exists"] is False for c in prov["candidates"])
