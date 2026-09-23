"""The simulated board identifies as the session's paradigm — P1-BUG-1 (2026-09-23).

A real board's paradigm is decided by the hex flashed onto it, and
``POST /api/serial/{id}/connect`` reads it back from IDENTIFY and writes it onto
the session (``api/routers/serial.py``). That is correct: hardware is truth.

The simulator has no hex. It used to default to ``fr`` with no way to say
otherwise — ``cmd 202 SET_PARADIGM`` is dead (no GUI, no backend, no firmware
handler; see ``schema.INTENTIONALLY_UNHANDLED``) and declares ``payload_type=
"int"`` while the simulator only accepts a string. So connecting *overwrote*
every session's paradigm with "fr", and the hardware router's paradigm gate then
400'd each paradigm's own commands while wrongly admitting FR's. Any non-FR
result taken from a connected simulator session was silently an FR result.

The fix threads the session's paradigm into the simulator at construction, so
its IDENTIFY tells the truth and the existing overwrite becomes a no-op rather
than a corruption. Two properties are load-bearing and tested here:

* the overwrite itself is untouched — a real board still wins, and a session
  that genuinely is ``fr`` must still reject 203/204/205;
* the paradigm gate is not weakened — non-FR commands are accepted only on a
  genuinely non-FR session.
"""

import json
import logging
import queue
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.reacher import REACHER
from reacher.kernel.simulator import PARADIGM_TO_SCHEDULE, FirmwareSimulator, SimulatedSerial

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}

#: (code, the paradigms CommandSpec declares it for). The gate must admit a code
#: on exactly these and reject it everywhere else.
PARADIGM_OWNED_COMMANDS = [
    (201, 9, {"fr", "pr"}),
    (205, 7, {"pr"}),
    (204, 45000, {"vi"}),
    (203, 33000, {"omission"}),
]

ALL_SIM_PARADIGMS = ["fr", "pr", "vi", "omission", "pavlovian"]


def _identify_as(paradigm):
    """Return the IDENTIFY record a simulator built for *paradigm* emits."""
    q = queue.Queue()
    FirmwareSimulator(q, paradigm=paradigm)._send_identification()
    # _send writes newline-delimited JSON bytes; IDENTIFY is the first line,
    # followed by the per-device config dump.
    return json.loads(q.get_nowait().decode())


class TestFirmwareSimulatorParadigm:
    """The unit layer: the simulator reports what it was told to impersonate."""

    def test_bare_construction_still_defaults_to_fr(self):
        """Every pre-existing ``FirmwareSimulator(q)`` call site depends on this."""
        assert FirmwareSimulator(queue.Queue()).paradigm == "fr"
        assert FirmwareSimulator(queue.Queue()).schedule == "FIXED_RATIO"

    @pytest.mark.parametrize("paradigm", sorted(PARADIGM_TO_SCHEDULE))
    def test_paradigm_selects_its_own_schedule(self, paradigm):
        sim = FirmwareSimulator(queue.Queue(), paradigm=paradigm)
        assert sim.paradigm == paradigm
        assert sim.schedule == PARADIGM_TO_SCHEDULE[paradigm]

    @pytest.mark.parametrize("bad", ["bogus", "", "FR", None])
    def test_unknown_paradigm_falls_back_rather_than_raising(self, bad):
        """A test double must not be able to break a connect with a bad name."""
        sim = FirmwareSimulator(queue.Queue(), paradigm=bad)
        assert sim.paradigm == "fr"
        assert sim.schedule == "FIXED_RATIO"

    def test_identification_reports_the_chosen_sketch(self):
        idn = _identify_as("vi")
        assert idn["sketch"] == "vi.ino"
        assert idn["schedule"] == "VARIABLE_INTERVAL"

    def test_lite_identification_keeps_the_lite_sketch_name(self):
        """``get_detected_paradigm`` distinguishes lite only by the sketch name —
        the schedule is shared with the full sketch."""
        idn = _identify_as("pr_lite")
        assert idn["sketch"] == "pr_lite.ino"
        assert idn["schedule"] == "PROGRESSIVE_RATIO"


class TestSimulatedSerialThreading:
    def test_simulated_serial_passes_the_paradigm_through(self):
        ser = SimulatedSerial(paradigm="omission")
        assert ser.paradigm == "omission"
        assert ser._simulator.paradigm == "omission"

    def test_default_is_unchanged(self):
        assert SimulatedSerial()._simulator.paradigm == "fr"


@pytest.fixture
def reacher():
    """A bare kernel with threads and disk I/O stubbed (mirrors core/test_reacher.py)."""
    with (
        patch("serial.Serial"),
        patch("serial.tools.list_ports.comports", return_value=[Mock(device="COM1", vid=1, pid=1)]),
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        yield REACHER()


class TestSetComPortKeepsTheTwoPathsDistinct:
    """The simulator learns the paradigm; a real port must not."""

    def test_simulator_branch_records_and_applies_it(self, reacher):
        reacher.set_COM_port("SIMULATOR", "vi")
        assert reacher._is_simulated is True
        assert reacher._simulated_paradigm == "vi"
        assert reacher.ser._simulator.paradigm == "vi"

    def test_real_port_branch_ignores_it(self, reacher):
        """A real board's paradigm comes from its hex, never from the caller."""
        reacher.set_COM_port("COM1", "vi")
        assert reacher._is_simulated is False
        assert reacher._simulated_paradigm is None
        # Nothing was substituted for the real port.
        assert not isinstance(reacher.ser, SimulatedSerial)

    def test_switching_from_simulator_to_real_port_clears_it(self, reacher):
        reacher.set_COM_port("SIMULATOR", "vi")
        reacher.set_COM_port("COM1", None)
        assert reacher._simulated_paradigm is None

    def test_omitting_the_paradigm_is_still_valid(self, reacher):
        """Existing callers pass one argument."""
        reacher.set_COM_port("SIMULATOR")
        assert reacher.ser._simulator.paradigm == "fr"


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """TestClient over a real SessionManager and real simulated serial.

    Deliberately *not* ``test_api.py``'s ``client`` fixture: that one mocks
    ``REACHER`` wholesale, so it cannot exercise the simulator at all.
    """
    monkeypatch.setenv("REACHER_NO_BROWSER", "1")
    monkeypatch.setenv("REACHER_STATIC_DIR", str(tmp_path / "no-static"))
    with TestClient(create_app()) as client:
        yield client


def _connect_session(client, paradigm):
    sid = client.post(
        "/api/sessions", json={"port": "SIMULATOR", "paradigm": paradigm}, headers=AUTH_HEADER
    ).json()["session_id"]
    client.post(f"/api/serial/{sid}/connect", headers=AUTH_HEADER)
    # connect() already waits on the IDENTIFY readiness gate; this only covers
    # the queue hop between the simulator thread and the kernel's reader.
    time.sleep(0.2)
    return sid


async def _no_sleep(_seconds):
    """The upload route waits 2s for a real board to reboot; the simulator does not."""
    return None


def _destroy(client, sid):
    client.post(f"/api/serial/{sid}/disconnect", headers=AUTH_HEADER)
    client.delete(f"/api/sessions/{sid}", headers=AUTH_HEADER)


class TestConnectedSessionReportsItsOwnParadigm:
    @pytest.mark.parametrize("paradigm", ALL_SIM_PARADIGMS)
    def test_connect_no_longer_overwrites_the_paradigm(self, api_client, paradigm):
        sid = _connect_session(api_client, paradigm)
        try:
            got = api_client.get(f"/api/sessions/{sid}", headers=AUTH_HEADER).json()["paradigm"]
            assert got == paradigm, (
                f"session created as {paradigm!r} reports {got!r} after connect — "
                "the simulator is identifying as the wrong sketch again (P1-BUG-1)"
            )
        finally:
            _destroy(api_client, sid)

    def test_lite_paradigm_survives_the_identify_round_trip(self, api_client):
        sid = _connect_session(api_client, "pr_lite")
        try:
            got = api_client.get(f"/api/sessions/{sid}", headers=AUTH_HEADER).json()["paradigm"]
            assert got == "pr_lite"
        finally:
            _destroy(api_client, sid)


class TestParadigmGateIsNotWeakened:
    """Both directions. Accepting everything would pass a one-sided test."""

    @pytest.mark.parametrize("paradigm", ALL_SIM_PARADIGMS)
    def test_a_command_is_accepted_exactly_on_the_paradigms_that_declare_it(
        self, api_client, paradigm
    ):
        sid = _connect_session(api_client, paradigm)
        try:
            for code, value, owners in PARADIGM_OWNED_COMMANDS:
                resp = api_client.post(
                    f"/api/hardware/{sid}/command",
                    json={"code": code, "value": value},
                    headers=AUTH_HEADER,
                )
                if paradigm in owners:
                    assert resp.status_code == 200, (
                        f"cmd {code} is declared for {sorted(owners)} but was rejected on "
                        f"{paradigm}: {resp.text}"
                    )
                else:
                    assert resp.status_code == 400, (
                        f"cmd {code} is NOT declared for {paradigm} but was accepted — "
                        "the paradigm gate has been weakened"
                    )
        finally:
            _destroy(api_client, sid)

    def test_an_fr_session_still_rejects_the_other_paradigms_commands(self, api_client):
        """The regression that matters: the fix must not make everything pass.

        This is the P1 item-4 check, restated against a session whose paradigm
        genuinely is ``fr`` rather than one forced there by the bug.
        """
        sid = _connect_session(api_client, "fr")
        try:
            for code in (203, 204, 205):
                resp = api_client.post(
                    f"/api/hardware/{sid}/command",
                    json={"code": code, "value": 1000},
                    headers=AUTH_HEADER,
                )
                assert resp.status_code == 400
                assert "not available for fr paradigm" in resp.json()["detail"]
        finally:
            _destroy(api_client, sid)


class TestPostUploadReconnectUsesTheFlashedHex:
    """After an upload the board runs the hex just flashed, not the old one."""

    def test_upload_reconnects_the_simulator_as_the_uploaded_paradigm(self, api_client, monkeypatch):
        """``info.paradigm`` is still the pre-upload value at reconnect time —
        ``sm.set_paradigm(body.paradigm)`` runs afterwards — so passing it would
        leave the simulated board identifying as the sketch it no longer runs.
        """
        sid = _connect_session(api_client, "fr")
        try:
            async def _fake_upload(paradigm, port, board, progress_cb, hex_path=None):
                return True

            monkeypatch.setattr(
                "reacher.api.routers.firmware._uploader.upload", _fake_upload
            )
            monkeypatch.setattr("reacher.api.routers.firmware.asyncio.sleep", _no_sleep)

            resp = api_client.post(
                f"/api/firmware/upload/{sid}",
                json={"paradigm": "vi", "board": "mega"},
                headers=AUTH_HEADER,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["firmware_info"]["sketch"] == "vi.ino", (
                "the simulated board still identifies as the pre-upload sketch"
            )
        finally:
            _destroy(api_client, sid)
