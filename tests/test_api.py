"""FastAPI integration tests using TestClient."""

import json
import os
import zipfile

import pytest
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from reacher.api.app import create_app


@pytest.fixture
def client():
    """Create a TestClient with mocked REACHER instances."""
    with patch("reacher.session_manager.REACHER") as MockReacher, \
         patch("os.makedirs"):
        mock_instance = Mock()
        mock_instance.program_running = False
        mock_instance.ser = Mock()
        mock_instance.ser.is_open = False
        mock_instance.get_firmware_information.return_value = {"sketch": "fr", "version": "1.0"}
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


class TestSessionEndpoints:
    def test_list_sessions_empty(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_create_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"})
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data

    def test_create_duplicate_port_conflict(self, client):
        client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        assert resp.status_code == 409

    def test_get_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["port"] == "/dev/ttyUSB0"

    def test_get_nonexistent_session(self, client):
        resp = client.get("/api/sessions/nonexistent")
        assert resp.status_code == 404

    def test_delete_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        # Session should be gone
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 404


class TestSerialEndpoints:
    def test_list_ports(self, client):
        with patch("reacher.api.routers.serial.list_ports") as mock_lp:
            mock_lp.comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
            resp = client.get("/api/serial/ports")
            assert resp.status_code == 200
            assert "COM1" in resp.json()["ports"]


class TestHardwareEndpoints:
    def test_get_commands_for_session(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"})
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/hardware/{sid}/commands")
        assert resp.status_code == 200
        assert resp.json()["paradigm"] == "fr"
        assert len(resp.json()["commands"]) > 0

    def test_send_unknown_command(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/hardware/{sid}/command", json={"code": 99999})
        assert resp.status_code == 400

    def test_get_config(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/hardware/{sid}/config")
        assert resp.status_code == 200
        assert "firmware_info" in resp.json()


class TestProgramEndpoints:
    def test_set_limits(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/program/{sid}/limit", json={
            "type": "Time",
            "time_limit": 3600,
        })
        assert resp.status_code == 200
        assert resp.json()["type"] == "Time"

    def test_invalid_limit_type(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/program/{sid}/limit", json={"type": "Invalid"})
        assert resp.status_code == 400


class TestDataEndpoints:
    def test_get_behavior_empty(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/data/{sid}/behavior")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_get_frames(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/data/{sid}/frames")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestFileEndpoints:
    def test_export_zip_no_config(self, client):
        """Export should fail 400 when filename/destination not configured."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/file/{sid}/export/zip", json={})
        assert resp.status_code == 400

    def test_export_zip_success(self, client, tmp_path):
        """Export should write a ZIP to the configured destination."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"})
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
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "1.0"}
        instance.get_hardware_settings.return_value = [{"baud_rate": 9600}]
        instance.get_frame_timestamps_count.return_value = 5

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

            meta = json.loads(zf.read("metadata.json"))
            assert meta["session_name"] == "my_session"
            assert meta["infusion_count"] == 3
