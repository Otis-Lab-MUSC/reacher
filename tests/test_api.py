"""FastAPI integration tests using TestClient."""

import csv
import io
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
    """Create a TestClient with mocked REACHER instances."""
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        mock_instance = Mock()
        mock_instance.program_running = False
        mock_instance.ser = Mock()
        mock_instance.ser.is_open = False
        mock_instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        mock_instance.get_behavior_data.return_value = []
        mock_instance.get_frame_data.return_value = []
        mock_instance.get_frame_timestamps_count.return_value = 0
        mock_instance.get_hardware_settings.return_value = []
        mock_instance.get_program_running.return_value = False
        mock_instance.get_filename.return_value = None
        mock_instance.get_data_destination.return_value = None
        mock_instance.get_detected_paradigm.return_value = None
        mock_instance.make_destination_folder.return_value = "/tmp/reacher_test"
        MockReacher.return_value = mock_instance

        app = create_app()
        with TestClient(app) as c:
            yield c


class TestAuthEndpoints:
    def test_auth_required_401(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 401

    def test_auth_valid_200(self, client):
        resp = client.get("/api/sessions", headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_health_no_auth_required(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200


class TestSessionEndpoints:
    def test_list_sessions_empty(self, client):
        resp = client.get("/api/sessions", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_create_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"}, headers=AUTH_HEADER)
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data

    def test_create_duplicate_port_conflict(self, client):
        client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        assert resp.status_code == 409

    def test_get_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["port"] == "/dev/ttyUSB0"

    def test_get_nonexistent_session(self, client):
        resp = client.get("/api/sessions/nonexistent", headers=AUTH_HEADER)
        assert resp.status_code == 404

    def test_delete_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.delete(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        # Session should be gone
        resp = client.get(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 404


class TestSerialEndpoints:
    def test_list_ports(self, client):
        with patch("reacher.api.routers.serial.list_ports") as mock_lp:
            mock_lp.comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
            resp = client.get("/api/serial/ports", headers=AUTH_HEADER)
            assert resp.status_code == 200
            assert "COM1" in resp.json()["ports"]


class TestHardwareEndpoints:
    def test_get_commands_for_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/hardware/{sid}/commands", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["paradigm"] == "fr"
        assert len(resp.json()["commands"]) > 0

    def test_send_unknown_command(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/hardware/{sid}/command", json={"code": 99999}, headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_get_config(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/hardware/{sid}/config", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert "firmware_info" in resp.json()

    def test_rate_limit_429(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        # Send 21 commands rapidly — the 21st should be rate-limited
        for _ in range(20):
            client.post(f"/api/hardware/{sid}/command", json={"code": 99999}, headers=AUTH_HEADER)
        resp = client.post(f"/api/hardware/{sid}/command", json={"code": 99999}, headers=AUTH_HEADER)
        assert resp.status_code == 429


class TestProgramEndpoints:
    def test_set_limits(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.post(
            f"/api/program/{sid}/limit",
            json={
                "type": "Time",
                "time_limit": 3600,
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == "Time"

    def test_invalid_limit_type(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/program/{sid}/limit", json={"type": "Invalid"}, headers=AUTH_HEADER)
        assert resp.status_code == 400


class TestDataEndpoints:
    def test_get_behavior_empty(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/data/{sid}/behavior", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_get_frames(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/data/{sid}/frames", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_get_behavior_with_limit(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_behavior_data.return_value = [
            {"device": "lever", "event": "press", "start_timestamp": i, "end_timestamp": i} for i in range(10)
        ]
        resp = client.get(f"/api/data/{sid}/behavior?limit=5", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 5

    def test_get_behavior_since_and_limit(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_behavior_data.return_value = [
            {"device": "lever", "event": "press", "start_timestamp": i, "end_timestamp": i} for i in range(10)
        ]
        resp = client.get(f"/api/data/{sid}/behavior?since=3&limit=2", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2


class TestFileEndpoints:
    def test_export_zip_no_config(self, client, tmp_path):
        """Export should default to ~/Downloads when filename/destination not configured."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = None
        instance.get_data_destination.return_value = None
        folder = tmp_path / "default_export"
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)

        resp = client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert "file_path" in resp.json()
        instance.set_data_destination.assert_called_once()
        dest_arg = instance.set_data_destination.call_args[0][0]
        assert dest_arg.endswith("/Downloads")
        instance.set_filename.assert_called_once()
        assert len(instance.set_filename.call_args[0][0]) > 0

    def test_export_zip_success(self, client, tmp_path):
        """Export should write a ZIP to the configured destination."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        # Configure the mock instance for export
        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = "test_file"
        instance.get_data_destination.return_value = str(tmp_path)
        folder = tmp_path / "test_file"
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)
        instance.get_behavior_data.return_value = [
            {"device": "lever", "event": "press", "start_timestamp": 100, "end_timestamp": 200}
        ]
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_hardware_settings.return_value = [{"baud_rate": 9600}]
        instance.get_frame_data.return_value = [50, 150, 250, 350, 450]

        resp = client.post(
            f"/api/file/{sid}/export/zip",
            json={
                "session_name": "my_session",
                "notes": "test notes",
                "infusion_count": 3,
                "press_count": 10,
                "trial_count": 2,
                "program_start_time": 1700000000.0,
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_path"].endswith(".zip")
        assert data["folder_path"] == str(folder)

        # Verify the ZIP was written and contains the right files
        zip_path = data["file_path"]
        assert os.path.exists(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "behavior_events.csv" in names
            assert "arduino_config.json" in names
            assert "metadata.json" in names
            assert "notes.txt" in names
            assert "frame_timestamps.csv" in names

            meta = json.loads(zf.read("metadata.json"))
            assert meta["session_name"] == "my_session"
            assert meta["infusion_count"] == 3
            assert meta["firmware_sketch"] == "fr.ino"
            assert meta["firmware_version"] == "v2.0.0"
            assert meta["frame_count"] == 5

            # Verify behavior_events.csv has frame index columns
            behavior_csv = zf.read("behavior_events.csv").decode()
            reader = csv.DictReader(io.StringIO(behavior_csv))
            rows = list(reader)
            assert len(rows) == 1
            assert "start_frame_index" in reader.fieldnames
            assert "end_frame_index" in reader.fieldnames
            assert rows[0]["start_frame_index"] == "0"
            assert rows[0]["end_frame_index"] == "1"

            # Verify frame_timestamps.csv
            ft_csv = zf.read("frame_timestamps.csv").decode()
            ft_reader = csv.DictReader(io.StringIO(ft_csv))
            ft_rows = list(ft_reader)
            assert len(ft_rows) == 5
            assert ft_rows[0] == {"frame_index": "0", "timestamp_ms": "50"}
            assert ft_rows[4] == {"frame_index": "4", "timestamp_ms": "450"}
