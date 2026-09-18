"""Regression tests for F2a: /start must not silently wipe a running/paused
session's buffered behavior data, and a failed start must not strand
``program_running=True`` on a session the state machine still calls idle.

Follow-up: "disconnected" — exactly where a serial blip mid-run leaves a
session holding unexported behavior data — is blocked the same way, and a
start that can never reach the firmware must fail before touching buffers
at all, not wipe them and then raise.
"""

import logging

import pytest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.reacher import REACHER

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    """TestClient with a mocked REACHER instance (mirrors tests/test_api.py)."""
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        mock_instance = Mock()
        mock_instance.program_running = False
        mock_instance.ser = Mock()
        mock_instance.ser.is_open = False
        mock_instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        mock_instance.get_behavior_data.return_value = []
        mock_instance.get_frame_data.return_value = []
        mock_instance.get_frame_timestamps_count.return_value = 0
        mock_instance.get_slm_data.return_value = []
        mock_instance.get_hardware_settings.return_value = []
        mock_instance.get_program_running.return_value = False
        mock_instance.get_filename.return_value = None
        mock_instance.get_data_destination.return_value = None
        mock_instance.get_detected_paradigm.return_value = None
        mock_instance.make_destination_folder.return_value = "/tmp/reacher_test"
        mock_instance.get_segment_exports.return_value = []
        mock_instance.get_segment_event_counts.return_value = []
        mock_instance.get_total_infusion_count.return_value = 0
        mock_instance.get_total_press_count.return_value = 0
        mock_instance.get_total_trial_count.return_value = 0
        mock_instance.get_event_log_path.return_value = "/tmp/reacher_test_missing_event_log.jsonl"
        mock_instance.flush_event_log.return_value = None
        mock_instance.emit_failure_count = 0
        MockReacher.return_value = mock_instance

        app = create_app()
        with TestClient(app) as c:
            yield c


def _create_session(client, port="/dev/ttyUSB0"):
    resp = client.post("/api/sessions", json={"port": port}, headers=AUTH_HEADER)
    assert resp.status_code == 201
    return resp.json()["session_id"]


class TestSecondStartGuard:
    def test_second_start_while_running_is_rejected_409(self, client):
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, "running")
        info = sm.get_session(sid)
        info.instance.get_behavior_data.return_value = [{"event": "a"}, {"event": "b"}]

        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert info.instance.start_program.call_count == 0

    def test_second_start_while_running_leaves_buffer_untouched(self, client):
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, "running")
        info = sm.get_session(sid)
        info.instance.get_behavior_data.return_value = [{"event": "a"}, {"event": "b"}]

        before = client.get(f"/api/data/{sid}/behavior", headers=AUTH_HEADER).json()
        client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)
        after = client.get(f"/api/data/{sid}/behavior", headers=AUTH_HEADER).json()

        assert before == {"data": [{"event": "a"}, {"event": "b"}], "total": 2}
        assert after == before

    def test_second_start_while_paused_is_rejected_409(self, client):
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, "paused")
        info = sm.get_session(sid)

        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert "paused" in resp.json()["detail"]
        assert info.instance.start_program.call_count == 0

    def test_start_now_override_from_armed_still_works(self, client):
        """The disarm-then-start manual override must survive the new guard:
        "armed" is not in the blocked set."""
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, "armed")
        info = sm.get_session(sid)

        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)

        assert resp.status_code == 200
        info.instance.disarm_external_trigger.assert_called_once()
        info.instance.start_program.assert_called_once()

    def test_start_from_connected_is_unaffected(self, client):
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, "connected")
        info = sm.get_session(sid)

        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)

        assert resp.status_code == 200
        info.instance.start_program.assert_called_once()

    def test_second_start_while_disconnected_is_rejected_409(self, client):
        """A serial blip mid-run leaves the session 'disconnected' with
        unexported behavior data still buffered — a /start there can never
        reach the firmware anyway and must not wipe it first."""
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, "disconnected")
        info = sm.get_session(sid)
        info.instance.get_behavior_data.return_value = [{"event": "a"}, {"event": "b"}]

        before = client.get(f"/api/data/{sid}/behavior", headers=AUTH_HEADER).json()
        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)
        after = client.get(f"/api/data/{sid}/behavior", headers=AUTH_HEADER).json()

        assert resp.status_code == 409
        assert "disconnected" in resp.json()["detail"]
        assert info.instance.start_program.call_count == 0
        assert after == before == {"data": [{"event": "a"}, {"event": "b"}], "total": 2}


# --- Kernel-level: a failed start must not strand program_running=True ---


@pytest.fixture
def mock_serial():
    with patch("serial.Serial") as mock_serial_class, patch("serial.tools.list_ports.comports") as mock_comports:
        mock_serial_instance = Mock()
        mock_serial_instance.baudrate = 115200
        mock_serial_class.return_value = mock_serial_instance
        mock_comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
        yield mock_serial_instance


@pytest.fixture
def reacher(mock_serial):
    with (
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        return REACHER()


class TestPartialStartReconcile:
    def test_failed_start_does_not_strand_program_running(self, reacher):
        """send_serial_command raises when the port isn't open (the
        unconnected/idle case) — program_running must not survive that."""
        reacher.ser.is_open = False

        with pytest.raises(Exception):
            reacher.start_program()

        assert reacher.program_running is False

    def test_failed_start_leaves_buffered_data_intact(self, reacher):
        """The port-closed check must fire before _reset_session_buffers():
        a start that can't succeed must not destroy what it can't replace."""
        reacher.ser.is_open = False
        reacher.behavior_data = [{"event": "a"}, {"event": "b"}, {"event": "c"}]

        with pytest.raises(Exception):
            reacher.start_program()

        assert reacher.behavior_data == [{"event": "a"}, {"event": "b"}, {"event": "c"}]
        assert reacher.program_running is False

    def test_successful_start_still_sets_program_running(self, reacher, mock_serial):
        reacher.ser.is_open = True

        reacher.start_program()

        assert reacher.program_running is True
