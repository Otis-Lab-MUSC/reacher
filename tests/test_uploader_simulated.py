"""Firmware upload against the SIMULATOR port.

Before this, ``POST /api/firmware/upload/{id}`` on a SIMULATOR session spawned
the real ``avrdude`` against the literal port name ``SIMULATOR`` and answered
500 ``unable to open port SIMULATOR`` (409 with no avrdude installed), leaving
the session ``idle`` with its paradigm unchanged. The uploader now stands in for
the programmer only: hex resolution, board lookup, hash logging, progress
reporting and the router's state machine (``uploading`` -> ``connected``, the
post-flash reconnect that boots the simulated board into the new sketch) all run
for real.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.uploader import boards
from reacher.uploader.uploader import (
    PARADIGMS,
    SIM_UPLOAD_FAIL_ENV,
    SIM_UPLOAD_SECONDS_ENV,
    AvrdudeNotFoundError,
    FirmwareUploader,
    _check_intel_hex,
)

AUTH = {"Authorization": f"Bearer {API_KEY}"}

# One data record (4 bytes) + EOF, checksums valid.
_GOOD_HEX = b":0400000001020304F2\n:00000001FF\n"


@pytest.fixture(autouse=True)
def _instant_flash(monkeypatch):
    monkeypatch.setenv(SIM_UPLOAD_SECONDS_ENV, "0")
    monkeypatch.delenv(SIM_UPLOAD_FAIL_ENV, raising=False)


def _hex_dir(tmp_path, content=_GOOD_HEX, board="mega", paradigms=("fr", "pr")):
    d = tmp_path / board
    d.mkdir(parents=True, exist_ok=True)
    for p in paradigms:
        (d / f"{p}.hex").write_bytes(content)
    return str(tmp_path)


def _upload(uploader, paradigm="pr", board="mega", port=boards.SIMULATOR_PORT):
    ticks = []
    ok = asyncio.run(uploader.upload(paradigm, port, board, lambda pct, stage: ticks.append((pct, stage))))
    return ok, ticks


# ---------------------------------------------------------------------------
# FirmwareUploader — the simulated programmer
# ---------------------------------------------------------------------------


class TestSimulatedUpload:
    def test_succeeds_without_avrdude_and_never_spawns_it(self, tmp_path):
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path), avrdude_path="/nonexistent/avrdude")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=AssertionError("spawned"))):
            ok, _ = _upload(uploader)
        assert ok is True

    def test_progress_has_avrdudes_shape(self, tmp_path):
        """Starting upload -> Writing (monotonic, ends 100) -> Complete."""
        _, ticks = _upload(FirmwareUploader(hex_dir=_hex_dir(tmp_path)))
        assert ticks[0] == (0, "Starting upload")
        assert ticks[-1] == (100, "Complete")
        writing = [pct for pct, stage in ticks if stage == "Writing"]
        assert writing == sorted(writing) and writing[-1] == 100 and len(writing) > 1

    def test_takes_the_configured_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv(SIM_UPLOAD_SECONDS_ENV, "0.4")
        start = time.monotonic()
        ok, _ = _upload(FirmwareUploader(hex_dir=_hex_dir(tmp_path)))
        assert ok and time.monotonic() - start >= 0.35

    def test_missing_hex_raises_like_a_real_upload(self, tmp_path):
        """Same exception the router maps to 404 — resolved before the port is considered."""
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path, paradigms=("fr",)))
        with pytest.raises(FileNotFoundError):
            _upload(uploader, paradigm="vi")
        with pytest.raises(FileNotFoundError):
            asyncio.run(uploader.upload("vi", "/dev/ttyUSB0", "mega"))

    def test_unknown_board_raises_like_a_real_upload(self, tmp_path):
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path))
        with pytest.raises(ValueError):
            _upload(uploader, board="due")

    def test_corrupt_hex_fails_like_a_nonzero_avrdude_exit(self, tmp_path):
        bad = _GOOD_HEX.replace(b"F2", b"F3")
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path, content=bad))
        ok, ticks = _upload(uploader)
        assert ok is False
        assert "checksum" in uploader.last_error
        assert ticks[-1][1] == "Failed (exit 1)"

    def test_hex_without_eof_record_fails(self, tmp_path):
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path, content=b":0400000001020304F2\n"))
        ok, _ = _upload(uploader)
        assert ok is False and "end-of-file" in uploader.last_error

    def test_forced_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv(SIM_UPLOAD_FAIL_ENV, "bootloader not responding")
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path))
        ok, ticks = _upload(uploader)
        assert ok is False
        assert "bootloader not responding" in uploader.last_error
        assert ticks[-1][1] == "Failed (exit 1)"

    def test_real_ports_still_require_avrdude(self, tmp_path):
        """The simulated branch is keyed on the port, not on the uploader."""
        uploader = FirmwareUploader(hex_dir=_hex_dir(tmp_path), avrdude_path="/nonexistent/avrdude")
        with pytest.raises(AvrdudeNotFoundError):
            asyncio.run(uploader.upload("pr", "/dev/ttyUSB0", "mega"))

    def test_every_shipped_hex_passes_the_checker(self):
        """A checker that rejects the real artifacts would make every simulated flash fail."""
        uploader = FirmwareUploader()
        checked = 0
        for board in boards.SUPPORTED_BOARDS:
            for paradigm in PARADIGMS:
                try:
                    path = uploader.get_hex_path(paradigm, board)
                except FileNotFoundError:
                    continue
                assert _check_intel_hex(path) > 1000, path
                checked += 1
        assert checked >= 9


class TestIntelHexChecker:
    @pytest.mark.parametrize(
        "content,needle",
        [
            (b"0400000001020304F2\n:00000001FF\n", "start with"),
            (b":04000000010203ZZF2\n:00000001FF\n", "hexadecimal"),
            (b":0400000001020304F3\n:00000001FF\n", "checksum"),
            (b":0500000001020304F1\n:00000001FF\n", "length"),
            (b":00000001FF\n:0400000001020304F2\n", "after the end-of-file"),
            (b"", "no end-of-file"),
        ],
    )
    def test_golden_negatives(self, tmp_path, content, needle):
        f = tmp_path / "x.hex"
        f.write_bytes(content)
        with pytest.raises(ValueError, match=needle):
            _check_intel_hex(str(f))

    def test_counts_data_bytes_and_tolerates_crlf_and_bom(self, tmp_path):
        f = tmp_path / "x.hex"
        f.write_bytes(b"\xef\xbb\xbf" + _GOOD_HEX.replace(b"\n", b"\r\n"))
        assert _check_intel_hex(str(f)) == 4


# ---------------------------------------------------------------------------
# Through the router — real SessionManager, real REACHER, real simulator
# ---------------------------------------------------------------------------


def _reset_ws_loop():
    """The WS module caches the first TestClient's event loop; once that portal
    closes, every later enqueue_event() raises "Event loop is closed"."""
    from reacher.api.routers import websocket as ws

    ws._loop = ws._notify = ws._broadcast_task = None
    ws._watchdog_task = ws._orphan_task = None


@pytest.fixture
def client():
    _reset_ws_loop()
    with TestClient(create_app()) as c:
        yield c
    _reset_ws_loop()


def _sim_session(client, paradigm="fr"):
    resp = client.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": paradigm}, headers=AUTH)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    assert client.post(f"/api/serial/{sid}/connect", json={}, headers=AUTH).status_code == 200
    return sid


def _state(client, sid):
    return client.get(f"/api/sessions/{sid}", headers=AUTH).json()["state"]


def _drain_ws(ws, until_state, limit=80):
    """Collect frames until *until_state* is announced after the upload began.

    A session briefly drops to idle when its port is closed for flashing (on
    hardware too), so stopping at the first matching state would end the
    drain before the upload even starts.
    """
    msgs, started = [], False
    for _ in range(limit):
        m = ws.receive_json()
        msgs.append(m)
        if m["type"] == "upload_progress":
            started = True
        if started and m["type"] == "session_state" and m["data"]["state"] == until_state:
            break
    return msgs


class TestUploadOverHttp:
    def test_upload_reflashes_the_simulated_board_and_streams_progress(self, client):
        sid = _sim_session(client, "fr")
        with client.websocket_connect(f"/ws/{sid}?token={API_KEY}") as ws:
            assert ws.receive_json() == {"type": "session_state", "session_id": sid, "data": {"state": "connected"}}
            resp = client.post(f"/api/firmware/upload/{sid}", json={"paradigm": "pr", "board": "mega"}, headers=AUTH)
            assert resp.status_code == 200, resp.text
            msgs = _drain_ws(ws, "connected")

        body = resp.json()
        assert body["status"] == "uploaded" and body["paradigm"] == "pr"
        # freshly booted pr sketch, read back from IDENTIFY
        assert body["firmware_info"]["sketch"] == "pr.ino"
        assert body["firmware_info"]["schedule"] == "PROGRESSIVE_RATIO"

        # close_serial() drops the session to idle first on hardware too; the
        # contract is uploading -> ... -> connected, progress inside it.
        states = [m["data"]["state"] for m in msgs if m["type"] == "session_state"]
        assert states[-2:] == ["uploading", "connected"]
        progress = [m["data"] for m in msgs if m["type"] == "upload_progress"]
        assert progress[0] == {"percent": 0, "stage": "Starting upload"}
        assert progress[-1] == {"percent": 100, "stage": "Complete"}
        assert [p["percent"] for p in progress] == sorted(p["percent"] for p in progress)
        # uploading is announced before the first progress tick
        assert msgs.index(next(m for m in msgs if m["type"] == "upload_progress")) > states.index("uploading")

        info = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert info["state"] == "connected" and info["paradigm"] == "pr"
        # the hardware gate now speaks pr: 205 (PR step) is accepted, VI's 204 is not
        assert client.post(f"/api/hardware/{sid}/command", json={"code": 205, "value": 3}, headers=AUTH).status_code == 200
        assert client.post(f"/api/hardware/{sid}/command", json={"code": 204, "value": 3000}, headers=AUTH).status_code == 400
        client.delete(f"/api/sessions/{sid}", headers=AUTH)

    def test_reflash_resets_the_board_to_sketch_defaults(self, client):
        """A real reboot drops RAM config — the new sketch must not inherit the old one's."""
        sid = _sim_session(client, "fr")
        inst = client.app.state.session_manager.get_session(sid).instance
        inst.send_command(472, 1234)  # PUMP_SET_DURATION on the running FR board
        assert inst.ser._simulator.pump_duration == 1234
        r = client.post(f"/api/firmware/upload/{sid}", json={"paradigm": "vi", "board": "mega"}, headers=AUTH)
        assert r.status_code == 200, r.text
        assert inst.ser._simulator.paradigm == "vi"
        assert inst.ser._simulator.pump_duration != 1234
        client.delete(f"/api/sessions/{sid}", headers=AUTH)

    def test_uno_lite_upload_boots_a_lite_board(self, client):
        """The UI defaults the board to "uno" (the SIMULATOR has no USB id to detect one from)."""
        sid = _sim_session(client)
        resp = client.post(f"/api/firmware/upload/{sid}", json={"paradigm": "fr_lite", "board": "uno"}, headers=AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json()["firmware_info"]["sketch"] == "fr_lite.ino"
        info = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert (info["state"], info["paradigm"], info["board"]) == ("connected", "fr_lite", "uno")
        client.delete(f"/api/sessions/{sid}", headers=AUTH)

    def test_missing_hex_is_404_and_session_returns_to_idle(self, client, tmp_path):
        sid = _sim_session(client)
        from reacher.api.routers import firmware as fw

        with patch.object(fw._uploader, "hex_dir", str(tmp_path)):
            resp = client.post(f"/api/firmware/upload/{sid}", json={"paradigm": "pr", "board": "mega"}, headers=AUTH)
        assert resp.status_code == 404
        assert _state(client, sid) == "idle"
        client.delete(f"/api/sessions/{sid}", headers=AUTH)

    def test_flash_failure_is_500_with_the_programmer_error_and_idle(self, client, monkeypatch):
        sid = _sim_session(client)
        monkeypatch.setenv(SIM_UPLOAD_FAIL_ENV, "stk500: not in sync")
        with client.websocket_connect(f"/ws/{sid}?token={API_KEY}") as ws:
            ws.receive_json()
            resp = client.post(f"/api/firmware/upload/{sid}", json={"paradigm": "pr", "board": "mega"}, headers=AUTH)
            msgs = _drain_ws(ws, "idle")
        assert resp.status_code == 500
        assert "stk500: not in sync" in resp.json()["detail"]
        assert _state(client, sid) == "idle"
        progress = [m["data"] for m in msgs if m["type"] == "upload_progress"]
        assert progress[-1]["stage"].startswith("Failed")
        # a failed flash leaves the old sketch's session paradigm alone
        assert client.get(f"/api/sessions/{sid}", headers=AUTH).json()["paradigm"] == "fr"
        client.delete(f"/api/sessions/{sid}", headers=AUTH)

    def test_unknown_paradigm_is_a_400_before_anything_is_touched(self, client):
        """Unchanged contract: the paradigm gate runs before the port is closed."""
        sid = _sim_session(client)
        resp = client.post(f"/api/firmware/upload/{sid}", json={"paradigm": "nope", "board": "mega"}, headers=AUTH)
        assert resp.status_code == 400
        assert _state(client, sid) == "connected"
        client.delete(f"/api/sessions/{sid}", headers=AUTH)
