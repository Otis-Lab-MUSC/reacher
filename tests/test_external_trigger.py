"""External TTL session-start trigger — pin policy, kernel path, and routes.

The feature spans firmware, kernel and API; these cover the backend half. The
firmware/Python command parity is covered by tests/test_command_parity.py and
the lite-build exclusion by tests/test_commands.py::TestLiteParadigms.
"""

import logging
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from reacher import pin_overrides
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.api.routers import websocket as ws_router
from reacher.kernel.commands import CommandCode, get_commands_for_paradigm
from reacher.kernel.reacher import REACHER

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}
SET_PIN = int(CommandCode.EXT_TRIGGER_SET_PIN)


# ---------------------------------------------------------------------------
# Pin policy
# ---------------------------------------------------------------------------


class TestPinPolicy:
    """requires_interrupt alone is not enough — MEGA_INT also holds 2 and 3."""

    @pytest.mark.parametrize("pin", [18, 19, 20, 21])
    def test_assignable_pins_accepted(self, pin):
        assert pin_overrides.validate_pin(SET_PIN, pin, "mega") is None

    def test_microscope_timestamp_pin_rejected(self):
        """Pin 2 is INT0, which carries Microscope::TimestampISR.

        attachInterrupt() replaces a pin's handler, so allowing pin 2 here would
        silently stop two-photon frame logging with no error raised anywhere.
        This is the case requires_interrupt=True would have let through.
        """
        err = pin_overrides.validate_pin(SET_PIN, 2, "mega")
        assert err is not None
        assert err["component"] == "ext_trigger"
        assert err["allowed"] == [18, 19, 20, 21]

    @pytest.mark.parametrize("pin", [3, 12, 17, 22, 53])
    def test_non_assignable_pins_rejected(self, pin):
        assert pin_overrides.validate_pin(SET_PIN, pin, "mega") is not None

    def test_pin_2_is_in_mega_int_so_the_role_flag_alone_would_admit_it(self):
        """Guards the reason allowed_pins exists, not just its effect."""
        assert 2 in pin_overrides.MEGA_INT
        assert 2 not in pin_overrides.EXT_TRIGGER_PINS

    def test_uno_has_no_assignable_pin(self):
        err = pin_overrides.validate_pin(SET_PIN, 18, "uno")
        assert err is not None
        assert err["allowed"] == []

    def test_unidentified_board_still_offers_the_mega_pins(self):
        """USB-ID detection returns None for Mega clones on real hardware, and
        board_sets falls back to UNO. Intersecting a Mega-specific allow-list
        with that guess would leave a clone able to use the trigger on its
        default pin but never move it, with a bewildering `allowed: []`."""
        for pin in (18, 19, 20, 21):
            assert pin_overrides.validate_pin(SET_PIN, pin, None) is None
        err = pin_overrides.validate_pin(SET_PIN, 12, None)
        assert err["allowed"] == [18, 19, 20, 21]
        assert err["board_known"] is False

    def test_a_known_uno_still_rejects_every_trigger_pin(self):
        """Relaxing the unknown-board case must not relax the known one."""
        err = pin_overrides.validate_pin(SET_PIN, 18, "uno")
        assert err["allowed"] == []
        assert err["board_known"] is True

    def test_pin_2_rejected_regardless_of_board(self):
        """The allow-list, not the board, is what protects the microscope ISR."""
        for board in ("mega", "uno", None, "some-clone"):
            assert pin_overrides.validate_pin(SET_PIN, 2, board) is not None

    def test_other_components_unaffected(self):
        assert pin_overrides.validate_pin(int(CommandCode.MICROSCOPE_SET_TRIG_PIN), 2, "mega") is None
        assert pin_overrides.validate_pin(int(CommandCode.SLM_SET_PIN), 11, "mega") is None


class TestParadigmGating:
    """The frontend sniffs code 1201 to decide whether to show the UI at all."""

    @pytest.mark.parametrize("paradigm", ["fr", "pr", "vi", "omission", "pavlovian"])
    def test_offered_on_mega_paradigms(self, paradigm):
        assert int(CommandCode.EXT_TRIGGER_ARM) in get_commands_for_paradigm(paradigm)

    @pytest.mark.parametrize("paradigm", ["fr_lite", "pr_lite", "vi_lite", "omission_lite"])
    def test_withheld_from_lite_paradigms(self, paradigm):
        cmds = get_commands_for_paradigm(paradigm)
        for code in (1200, 1201, 1276):
            assert code not in cmds


# ---------------------------------------------------------------------------
# Kernel
# ---------------------------------------------------------------------------


@pytest.fixture
def kernel():
    with (
        patch("serial.Serial"),
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        instance = REACHER()
        instance.send_serial_command = Mock()
        instance._write_event_log = Mock()
        yield instance


def _start_line(source="external"):
    return {
        "level": "007",
        "device": "CONTROLLER",
        "event": "START",
        "timestamp": 0,
        "source": source,
    }


class TestArming:
    def test_arm_sends_the_arm_command(self, kernel):
        kernel.arm_external_trigger()
        kernel.send_serial_command.assert_called_once_with({"cmd": 1201})

    def test_disarm_sends_the_disarm_command(self, kernel):
        kernel.disarm_external_trigger()
        kernel.send_serial_command.assert_called_once_with({"cmd": 1200})

    def test_arm_resets_buffers_so_the_trigger_event_survives(self, kernel):
        """Buffers reset at arm time, not at fire time.

        The START event is appended by update_behavioral_events on the queue
        thread in the same call that begins the session, so resetting when the
        trigger fires would wipe the very event that caused the start.
        """
        kernel.behavior_data = [{"stale": True}]
        kernel.frame_data = [1, 2, 3]
        kernel._infusion_count = 7

        kernel.arm_external_trigger()
        assert kernel.behavior_data == []
        assert kernel.frame_data == []
        assert kernel._infusion_count == 0

        kernel.update_behavioral_events(_start_line())
        assert any(e.get("event") == "START" for e in kernel.behavior_data)

    def test_arm_clears_a_stale_anchor(self, kernel):
        """A session armed after an earlier run must not carry the old t0."""
        kernel.program_start_time = 1000.0
        kernel.arm_external_trigger()
        assert kernel.program_start_time is None


class TestExternalStart:
    def test_external_start_does_not_resend_session_start(self, kernel):
        """Re-sending cmd 101 would re-fire microscope.Trigger(), a toggle that
        would stop the scope scanning mid-session."""
        kernel.update_behavioral_events(_start_line())
        for call in kernel.send_serial_command.call_args_list:
            assert call.args[0].get("cmd") != 101

    def test_external_start_marks_the_program_running(self, kernel):
        assert kernel.program_running is False
        kernel.update_behavioral_events(_start_line())
        assert kernel.program_running is True
        assert kernel.program_start_time is not None

    def test_software_start_line_does_not_trigger_the_external_path(self, kernel):
        kernel.update_behavioral_events(_start_line(source="software"))
        assert kernel.program_running is False

    def test_start_line_without_source_is_not_external(self, kernel):
        """Older firmware omits the field entirely."""
        event = _start_line()
        del event["source"]
        kernel.update_behavioral_events(event)
        assert kernel.program_running is False

    def test_t0_is_backdated_by_the_receipt_lag(self, kernel):
        """Firmware stamped t0 at the TTL edge; we hear about it a queue hop
        later. check_limit_met measures elapsed against program_start_time, so
        without back-dating a Time limit over-runs by the serial latency."""
        rx = time.monotonic() - 0.4
        before = time.time()
        kernel.update_behavioral_events(_start_line(), rx_monotonic=rx)
        assert kernel.program_start_time < before
        assert 0.3 < (before - kernel.program_start_time) < 0.6

    def test_t0_is_not_backdated_without_a_receipt_time(self, kernel):
        before = time.time()
        kernel.update_behavioral_events(_start_line())
        assert kernel.program_start_time >= before

    def test_on_start_fires_before_the_behavioral_event_is_emitted(self, kernel):
        """Ordering guarantee the frontend depends on: clients must see
        session_state -> running before the event that caused it."""
        order = []
        kernel._on_start = lambda: order.append("state")
        kernel.event_callback = lambda sid, kind, data: order.append(kind)
        kernel.session_id = "abc"

        kernel.update_behavioral_events(_start_line())
        assert order.index("state") < order.index("event")

    def test_controller_end_still_works(self, kernel):
        kernel.update_behavioral_events({
            "level": "007", "device": "CONTROLLER", "event": "END", "timestamp": 42,
        })
        assert kernel._controller_end_received.is_set()


class TestStateEvents:
    """The firmware disarms itself when the trigger fires, so unlike every other
    device the backend cannot infer this state from the command it sent."""

    def test_ext_trigger_state_is_mirrored_into_hardware_settings(self, kernel):
        kernel.handle_state_event({
            "level": "001", "device": "EXT_TRIGGER", "event": "ARMED", "pin": 18,
        })
        entry = next(e for e in kernel.get_hardware_settings() if e["device"] == "EXT_TRIGGER")
        assert entry["armed"] is True
        assert entry["pin"] == 18

        kernel.handle_state_event({
            "level": "001", "device": "EXT_TRIGGER", "event": "DISARMED", "pin": 18,
        })
        entry = next(e for e in kernel.get_hardware_settings() if e["device"] == "EXT_TRIGGER")
        assert entry["armed"] is False

    def test_ext_trigger_state_is_emitted_as_config(self, kernel):
        emitted = []
        kernel.event_callback = lambda sid, kind, data: emitted.append((kind, data))
        kernel.session_id = "abc"
        kernel.handle_state_event({
            "level": "001", "device": "EXT_TRIGGER", "event": "ARMED", "pin": 20,
        })
        assert ("config", {"device": "EXT_TRIGGER", "armed": True, "pin": 20}) in emitted

    def test_other_devices_stay_log_only(self, kernel):
        """Level 001 has always been log-only; only EXT_TRIGGER is special."""
        emitted = []
        kernel.event_callback = lambda sid, kind, data: emitted.append(kind)
        kernel.session_id = "abc"
        kernel.handle_state_event({"level": "001", "device": "CUE", "desc": "ARMED"})
        assert emitted == []
        assert kernel.get_hardware_settings() == []


def test_receipt_time_rides_the_queue(kernel):
    """read_serial stamps monotonic time so handle_data can back-date t0."""
    kernel.ser.is_open = True
    type(kernel.ser).in_waiting = 1
    kernel.ser.readline.return_value = b'{"level":"001","device":"CUE"}\n'

    seen = []
    kernel.handle_data = lambda line, rx=None: seen.append((line, rx))

    def stop_after_one(*_a, **_k):
        kernel.serial_flag.set()
    kernel.queue.put_nowait = Mock(side_effect=lambda item: (seen.append(item), stop_after_one()))

    kernel.serial_flag.clear()
    kernel.read_serial()

    line, rx = seen[0]
    assert line.startswith("{")
    assert isinstance(rx, float)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        instance = Mock()
        instance.program_running = False
        instance.ser = Mock()
        instance.ser.is_open = False
        instance.get_hardware_settings.return_value = []
        instance.get_program_running.return_value = False
        instance.release_external_trigger.return_value = True
        instance.emit_failure_count = 0
        MockReacher.return_value = instance
        app = create_app()
        with TestClient(app) as c:
            c.mock_instance = instance
            yield c


def _session(client, paradigm="fr", state="connected"):
    resp = client.post("/api/sessions", json={"port": "COM1"}, headers=AUTH_HEADER)
    sid = resp.json()["session_id"]
    sm = client.app.state.session_manager
    sm.set_paradigm(sid, paradigm)
    sm.set_state(sid, state)
    return sid


class TestRoutes:
    def test_arm_from_connected(self, client):
        sid = _session(client)
        resp = client.post(f"/api/program/{sid}/arm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json() == {"status": "armed"}
        assert client.app.state.session_manager.get_session(sid).state == "armed"
        client.mock_instance.arm_external_trigger.assert_called_once()

    @pytest.mark.parametrize("state", ["idle", "running", "paused", "uploading", "armed"])
    def test_arm_rejected_outside_connected(self, client, state):
        sid = _session(client, state=state)
        resp = client.post(f"/api/program/{sid}/arm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_arm_rejected_on_lite_paradigm(self, client):
        sid = _session(client, paradigm="fr_lite")
        resp = client.post(f"/api/program/{sid}/arm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 400
        assert "external trigger" in resp.json()["detail"]

    def test_disarm_returns_to_connected(self, client):
        sid = _session(client, state="armed")
        resp = client.post(f"/api/program/{sid}/disarm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert client.app.state.session_manager.get_session(sid).state == "connected"
        client.mock_instance.release_external_trigger.assert_called_once()

    def test_disarm_rejected_when_not_armed(self, client):
        sid = _session(client, state="connected")
        resp = client.post(f"/api/program/{sid}/disarm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_arm_unknown_session_404(self, client):
        resp = client.post("/api/program/deadbeef/arm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 404

    def test_start_now_disarms_before_starting(self, client):
        """Otherwise the board keeps watching the pin and a later stray edge
        re-enters StartSession() mid-run, re-pulsing the scope trigger."""
        sid = _session(client, state="armed")
        calls = []
        client.mock_instance.disarm_external_trigger.side_effect = lambda: calls.append("disarm")
        client.mock_instance.start_program.side_effect = lambda: calls.append("start")

        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert calls == ["disarm", "start"]
        assert client.app.state.session_manager.get_session(sid).state == "running"

    def test_software_start_does_not_disarm(self, client):
        sid = _session(client, state="connected")
        client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)
        client.mock_instance.disarm_external_trigger.assert_not_called()

    def test_config_is_frozen_while_armed(self, client):
        """Config is applied one serial command per request — there is no
        transactional apply, so a trigger landing mid-edit would start the
        session on a half-applied configuration."""
        sid = _session(client, state="armed")
        resp = client.post(
            f"/api/hardware/{sid}/command", json={"code": 301}, headers=AUTH_HEADER,
        )
        assert resp.status_code == 409

    def test_config_is_open_again_after_disarming(self, client):
        sid = _session(client, state="armed")
        client.post(f"/api/program/{sid}/disarm-trigger", headers=AUTH_HEADER)
        resp = client.post(
            f"/api/hardware/{sid}/command", json={"code": 301}, headers=AUTH_HEADER,
        )
        assert resp.status_code == 200

    def test_pause_rejected_while_armed(self, client):
        sid = _session(client, state="armed")
        resp = client.post(f"/api/program/{sid}/pause", headers=AUTH_HEADER)
        assert resp.status_code == 400


class TestArmedLifecycleExits:
    """Arming attaches an interrupt on the board that only the host knows to
    detach. Every path that ends or abandons a session has to release it, or a
    later TTL edge starts a run on a session the host thinks is gone.

    None of these endpoints were exercised against an armed session before,
    which is why a green suite hid five separate leaks.
    """

    def test_stop_releases_the_trigger_and_reaches_a_terminal_state(self, client):
        """stop_program() returns at its re-entrance guard for an armed session
        (program_running is False), so on_stop never fires. The route used to
        report "stopped" while the board kept watching the pin."""
        sid = _session(client, state="armed")
        resp = client.post(f"/api/program/{sid}/stop", headers=AUTH_HEADER)
        assert resp.status_code == 200
        client.mock_instance.stop_program.assert_called_once()
        assert client.app.state.session_manager.get_session(sid).state == "stopped"

    def test_disconnect_releases_before_closing_serial(self, client):
        """Closing the port first makes the disarm unsendable."""
        sid = _session(client, state="armed")
        calls = []
        client.mock_instance.release_external_trigger.side_effect = lambda: (
            calls.append("release") or True
        )
        client.mock_instance.close_serial.side_effect = lambda: calls.append("close")

        resp = client.post(f"/api/serial/{sid}/disconnect", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert calls == ["release", "close"]

    def test_disconnect_reports_an_unreleased_board(self, client):
        sid = _session(client, state="armed")
        client.mock_instance.release_external_trigger.return_value = False
        resp = client.post(f"/api/serial/{sid}/disconnect", headers=AUTH_HEADER)
        assert resp.json()["trigger_released"] is False

    def test_reset_releases_the_trigger(self, client):
        sid = _session(client, state="armed")
        client.post(f"/api/sessions/{sid}/reset", headers=AUTH_HEADER)
        # reset() releases internally, before it closes serial.
        client.mock_instance.reset.assert_called_once()

    def test_destroy_releases_before_closing_serial(self, client):
        """An armed session has program_running False, so destroy_session takes
        the `elif ser.is_open: close_serial()` branch. The release has to run
        before that branch, not inside it."""
        sid = _session(client, state="armed")
        calls = []
        client.mock_instance.program_running = False
        client.mock_instance.ser.is_open = True
        client.mock_instance.release_external_trigger.side_effect = lambda: (
            calls.append("release") or True
        )
        client.mock_instance.close_serial.side_effect = lambda: calls.append("close")

        resp = client.delete(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert calls == ["release", "close"]

    def test_disarm_still_exits_armed_when_the_port_is_gone(self, client):
        """Serial closed while armed used to 500 and strand the session in
        "armed" with no way out."""
        sid = _session(client, state="armed")
        client.mock_instance.release_external_trigger.return_value = False
        resp = client.post(f"/api/program/{sid}/disarm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["firmware_notified"] is False
        assert client.app.state.session_manager.get_session(sid).state == "connected"

    def test_limit_rejected_while_armed(self, client):
        sid = _session(client, state="armed")
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Time", "time_limit": 60},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 409


class TestReleaseSemantics:
    def test_release_is_a_noop_when_never_armed(self, kernel):
        assert kernel.release_external_trigger() is True
        kernel.send_serial_command.assert_not_called()

    def test_release_sends_the_disarm_when_armed(self, kernel):
        kernel.arm_external_trigger()
        kernel.send_serial_command.reset_mock()
        assert kernel.release_external_trigger() is True
        kernel.send_serial_command.assert_called_once_with({"cmd": 1200})

    def test_release_is_idempotent(self, kernel):
        kernel.arm_external_trigger()
        kernel.release_external_trigger()
        kernel.send_serial_command.reset_mock()
        assert kernel.release_external_trigger() is True
        kernel.send_serial_command.assert_not_called()

    def test_release_never_raises_on_a_closed_port(self, kernel):
        """Teardown closes serial; a failure here must not block the teardown."""
        kernel.arm_external_trigger()
        kernel.send_serial_command.side_effect = Exception("Serial port is not open.")
        assert kernel.release_external_trigger() is False

    def test_firmware_self_disarm_clears_the_flag(self, kernel):
        """The trigger disarms itself on the edge; the host must not then try
        to disarm an already-disarmed board during teardown."""
        kernel.arm_external_trigger()
        kernel.handle_state_event({
            "level": "001", "device": "EXT_TRIGGER", "event": "DISARMED", "pin": 18,
        })
        kernel.send_serial_command.reset_mock()
        kernel.release_external_trigger()
        kernel.send_serial_command.assert_not_called()

    def test_external_start_clears_the_flag(self, kernel):
        kernel.arm_external_trigger()
        kernel.update_behavioral_events(_start_line())
        kernel.send_serial_command.reset_mock()
        kernel.release_external_trigger()
        kernel.send_serial_command.assert_not_called()

    def test_stop_program_releases_before_its_reentrance_guard(self, kernel):
        """An armed session has program_running False, so anything behind the
        guard would never run."""
        kernel.arm_external_trigger()
        kernel.program_running = False
        kernel.send_serial_command.reset_mock()
        kernel.stop_program()
        kernel.send_serial_command.assert_called_once_with({"cmd": 1200})


def test_armed_session_blocks_the_shutdown_watchdog(client, monkeypatch):
    """The session is waiting on a TTL edge that may be minutes away; shutting
    down would drop it silently and the experiment would never start."""
    # _app_ref is normally set on the first WebSocket connect, which this test
    # does not make.
    monkeypatch.setattr(ws_router, "_app_ref", client.app)
    sid = _session(client, state="armed")
    assert ws_router._any_session_active() is True
    client.app.state.session_manager.set_state(sid, "connected")
    assert ws_router._any_session_active() is False
