"""counted_pumps on the limit route, session status and export metadata."""

import json
import os
import zipfile

import pytest
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
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
        mock_instance.get_counted_pumps.return_value = ["PUMP_1"]
        mock_instance.get_infusions_by_pump.return_value = {"PUMP_1": 0, "PUMP_2": 0}
        mock_instance._counted_pumps = None
        mock_instance.get_event_log_path.return_value = "/tmp/reacher_test_missing_event_log.jsonl"
        mock_instance.flush_event_log.return_value = None
        mock_instance.emit_failure_count = 0
        MockReacher.return_value = mock_instance

        app = create_app()
        with TestClient(app) as c:
            yield c


def _session(client):
    resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
    return resp.json()["session_id"]


def _instance(client, sid):
    return client.app.state.session_manager.get_instance(sid)


class TestLimitCountedPumps:
    def test_explicit_list_is_passed_to_kernel(self, client):
        sid = _session(client)
        instance = _instance(client, sid)
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Infusion", "infusion_limit": 5, "counted_pumps": ["PUMP_1", "PUMP_2"]},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        instance.set_counted_pumps.assert_called_once_with(["PUMP_1", "PUMP_2"])

    def test_omitted_leaves_setting_untouched(self, client):
        sid = _session(client)
        instance = _instance(client, sid)
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Infusion", "infusion_limit": 5},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        instance.set_counted_pumps.assert_not_called()

    def test_explicit_null_resets_to_reward_pump(self, client):
        sid = _session(client)
        instance = _instance(client, sid)
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Infusion", "infusion_limit": 5, "counted_pumps": None},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        instance.set_counted_pumps.assert_called_once_with(None)

    @pytest.mark.parametrize("limit_type", ["Infusion", "Both"])
    def test_empty_list_rejected_for_infusion_limits(self, client, limit_type):
        sid = _session(client)
        instance = _instance(client, sid)
        body = {"type": limit_type, "infusion_limit": 5, "time_limit": 60, "counted_pumps": []}
        resp = client.post(f"/api/program/{sid}/limit", json=body, headers=AUTH_HEADER)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "counted_pumps cannot be empty for an Infusion or Both limit"
        instance.set_counted_pumps.assert_not_called()
        instance.set_limit_type.assert_not_called()
        instance.set_infusion_limit.assert_not_called()

    def test_empty_list_allowed_for_time_limit(self, client):
        sid = _session(client)
        instance = _instance(client, sid)
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Time", "time_limit": 60, "counted_pumps": []},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        instance.set_counted_pumps.assert_called_once_with([])

    def test_unknown_pump_key_is_422(self, client):
        sid = _session(client)
        instance = _instance(client, sid)
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Infusion", "infusion_limit": 5, "counted_pumps": ["PUMP_3"]},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        instance.set_counted_pumps.assert_not_called()

    def test_kernel_value_error_is_400(self, client):
        sid = _session(client)
        instance = _instance(client, sid)
        instance.set_counted_pumps.side_effect = ValueError("unknown pump")
        instance.set_limit_type.reset_mock()
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={"type": "Infusion", "infusion_limit": 5, "counted_pumps": ["PUMP_2"]},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "unknown pump"
        instance.set_limit_type.assert_not_called()


class TestSessionCountedPumps:
    def test_get_session_reports_per_pump_status(self, client):
        sid = _session(client)
        resp = client.get(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["counted_pumps"] == ["PUMP_1"]
        assert data["follows_reward_pump"] is True
        assert data["infusions_by_pump"] == {"PUMP_1": 0, "PUMP_2": 0}
        assert data["infusion_count"] == 0


class TestExportCountedPumps:
    def test_export_metadata_has_per_pump_fields(self, client, tmp_path):
        sid = _session(client)
        instance = _instance(client, sid)
        instance.get_filename.return_value = "test_file"
        instance.get_data_destination.return_value = str(tmp_path)
        folder = tmp_path / "test_file"
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)
        instance.get_total_infusion_count.return_value = 7
        instance.get_infusions_by_pump.return_value = {"PUMP_1": 4, "PUMP_2": 3}
        instance.get_counted_pumps.return_value = ["PUMP_1", "PUMP_2"]
        event_log_path = tmp_path / "event_log.jsonl"
        event_log_path.write_text('{"type": "SESSION_START", "timestamp": 1700000000.0}\n')
        instance.get_event_log_path.return_value = str(event_log_path)

        resp = client.post(
            f"/api/file/{sid}/export/zip",
            json={"session_name": "my_session", "program_start_time": 1700000000.0},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        zip_path = resp.json()["file_path"]
        assert os.path.exists(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            meta = json.loads(zf.read("metadata.json"))
        assert meta["infusions_by_pump"] == {"PUMP_1": 4, "PUMP_2": 3}
        assert meta["counted_pumps"] == ["PUMP_1", "PUMP_2"]
        assert meta["infusion_count"] == 7
