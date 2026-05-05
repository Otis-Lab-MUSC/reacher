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
        mock_instance.get_segment_exports.return_value = []
        mock_instance.get_segment_event_counts.return_value = []
        mock_instance.get_event_log_path.return_value = "/tmp/reacher_test_missing_event_log.jsonl"
        mock_instance.flush_event_log.return_value = None
        mock_instance.emit_failure_count = 0
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
        body = resp.json()
        assert body["port"] == "/dev/ttyUSB0"
        # Fix 7.4: surface cumulative event_callback failures
        assert body["callback_failures"] == 0

    def test_get_session_reports_callback_failures(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        sm = client.app.state.session_manager
        sm.get_session(sid).instance.emit_failure_count = 7
        resp = client.get(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["callback_failures"] == 7

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


class TestPinAssignments:
    """PUT /api/hardware/{id}/pins — bulk pin reassignment endpoint."""

    def _connected_session(self, client, port="/dev/ttyUSB0", board="uno"):
        resp = client.post("/api/sessions", json={"port": port, "paradigm": "fr"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        sm = client.app.state.session_manager
        sm.set_state(sid, "connected")
        sm.set_board(sid, board)
        return sid

    def test_happy_path_uno(self, client):
        sid = self._connected_session(client)
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"cue": 11, "pump": 4, "lever_rh": 12}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] == {"cue": 11, "pump": 4, "lever_rh": 12}
        assert body["errors"] == []

    def test_state_gate_rejects_non_connected(self, client):
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        # state defaults to "idle" — not connected
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"cue": 11}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 409

    def test_collision_rejection(self, client):
        sid = self._connected_session(client)
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"cue": 11, "laser": 11}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "pin_collision"

    def test_role_violation_pwm_required(self, client):
        sid = self._connected_session(client)
        # Pin 4 is digital but not PWM on UNO; cue requires PWM
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"cue": 4}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "pin_violations"
        assert any(v["required"] == "pwm" for v in detail["violations"])

    def test_out_of_range_rejected_on_uno(self, client):
        sid = self._connected_session(client, board="uno")
        # Pin 30 only exists on Mega; should fail UNO digital range
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"pump": 30}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert any(v["error"] == "pin_out_of_range" for v in detail["violations"])

    def test_mega_allows_pin_30(self, client):
        sid = self._connected_session(client, board="mega")
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"pump": 30}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] == {"pump": 30}

    def test_mega_pwm_44_allowed_for_cue(self, client):
        sid = self._connected_session(client, board="mega")
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"cue": 44}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200

    def test_unknown_component_rejected(self, client):
        sid = self._connected_session(client)
        resp = client.put(
            f"/api/hardware/{sid}/pins",
            json={"assignments": {"invalid_component": 11}},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400

    def test_single_command_pin_state_gate(self, client):
        # Sending a SET_PIN via single POST /command also requires connected state
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]
        resp = client.post(
            f"/api/hardware/{sid}/command",
            json={"code": 376, "value": 11},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 409

    def test_single_command_pin_role_validation(self, client):
        sid = self._connected_session(client)
        resp = client.post(
            f"/api/hardware/{sid}/command",
            json={"code": 376, "value": 4},  # cue requires PWM, pin 4 is non-PWM
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


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

        # Non-segmented session — no prior segment CSVs
        instance.get_segment_exports.return_value = []
        instance.get_segment_event_counts.return_value = []

        # Real event_log.jsonl on disk so it gets included
        event_log_path = tmp_path / "event_log.jsonl"
        event_log_path.write_text(
            '{"type": "SESSION_START", "timestamp": 1700000000.0}\n'
            '{"type": "behavior", "device": "lever", "event": "press"}\n'
            '{"type": "SESSION_END", "timestamp": 1700000010.0}\n'
        )
        instance.get_event_log_path.return_value = str(event_log_path)

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
            assert "event_log.jsonl" in names

            meta = json.loads(zf.read("metadata.json"))
            assert meta["session_name"] == "my_session"
            assert meta["infusion_count"] == 3
            assert meta["firmware_sketch"] == "fr"
            assert meta["firmware_version"] == "v2.0.0"
            assert meta["frame_count"] == 5
            assert meta["segment_count"] == 1
            assert meta["per_segment_event_counts"] == [1]
            assert meta["behavior_event_count"] == 1

            # event_log.jsonl should round-trip exactly
            event_log_roundtrip = zf.read("event_log.jsonl").decode()
            assert event_log_roundtrip == event_log_path.read_text()

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

    def test_export_zip_includes_pavlov_rows(self, client, tmp_path):
        """behavior_events.csv in the export must carry device=PAVLOV rows."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = "pavlov_file"
        instance.get_data_destination.return_value = str(tmp_path)
        folder = tmp_path / "pavlov_file"
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)
        instance.get_behavior_data.return_value = [
            {
                "device": "PAVLOV",
                "event": "TRIAL_START",
                "start_timestamp": 1000,
                "end_timestamp": 1000,
            },
            {
                "device": "PAVLOV",
                "event": "REWARD_DELIVERED",
                "start_timestamp": 2000,
                "end_timestamp": 2000,
            },
            {
                "device": "PUMP",
                "event": "INFUSION",
                "start_timestamp": 2100,
                "end_timestamp": 2200,
            },
        ]
        instance.get_firmware_information.return_value = {"sketch": "pavlovian", "version": "v2.0.0"}
        instance.get_hardware_settings.return_value = []
        instance.get_frame_data.return_value = []
        instance.get_segment_exports.return_value = []
        instance.get_segment_event_counts.return_value = []

        event_log_path = tmp_path / "pavlov_event_log.jsonl"
        event_log_path.write_text('{"type": "SESSION_START", "timestamp": 1700000000.0}\n')
        instance.get_event_log_path.return_value = str(event_log_path)

        resp = client.post(
            f"/api/file/{sid}/export/zip",
            json={
                "session_name": "pavlov_session",
                "notes": "",
                "infusion_count": 1,
                "press_count": 0,
                "trial_count": 1,
                "program_start_time": 1700000000.0,
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        zip_path = resp.json()["file_path"]
        with zipfile.ZipFile(zip_path) as zf:
            behavior_csv = zf.read("behavior_events.csv").decode()
            reader = csv.DictReader(io.StringIO(behavior_csv))
            rows = list(reader)
        devices = [r["device"] for r in rows]
        assert devices.count("PAVLOV") == 2
        events = {r["event"] for r in rows if r["device"] == "PAVLOV"}
        assert events == {"TRIAL_START", "REWARD_DELIVERED"}

    def test_export_zip_segmented_session(self, client, tmp_path):
        """Segmented sessions should include all prior segment CSVs, event_log.jsonl, and segment metadata."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = "seg_session"
        instance.get_data_destination.return_value = str(tmp_path)
        folder = tmp_path / "seg_session"
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)

        # Two prior segment CSVs already on disk (written by split_segment)
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        seg1_path = log_dir / "behavior_events_001.csv"
        seg2_path = log_dir / "behavior_events_002.csv"
        seg1_content = (
            "device,event,start_timestamp,end_timestamp,start_frame_index,end_frame_index\n"
            "lever,press,10,20,,\n"
            "lever,press,30,40,,\n"
            "pump,infusion,35,45,,\n"
        )
        seg2_content = (
            "device,event,start_timestamp,end_timestamp,start_frame_index,end_frame_index\n"
            "lever,press,100,110,,\n"
            "lever,press,120,130,,\n"
            "lick,contact,125,135,,\n"
            "lick,contact,140,145,,\n"
            "pump,infusion,150,160,,\n"
        )
        seg1_path.write_text(seg1_content)
        seg2_path.write_text(seg2_content)
        instance.get_segment_exports.return_value = [str(seg1_path), str(seg2_path)]
        instance.get_segment_event_counts.return_value = [3, 5]

        # Final segment (still in memory, not yet auto-exported)
        instance.get_behavior_data.return_value = [
            {"device": "lever", "event": "press", "start_timestamp": 200, "end_timestamp": 210},
            {"device": "pump", "event": "infusion", "start_timestamp": 215, "end_timestamp": 220},
        ]
        instance.get_frame_data.return_value = []
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_hardware_settings.return_value = []

        # event_log.jsonl spanning all segments
        event_log_path = tmp_path / "event_log.jsonl"
        event_log_path.write_text(
            '{"type": "SESSION_START", "timestamp": 1700000000.0}\n'
            '{"type": "SEGMENT_SPLIT", "segment": 1, "events_exported": 3}\n'
            '{"type": "SEGMENT_SPLIT", "segment": 2, "events_exported": 5}\n'
            '{"type": "SESSION_END", "timestamp": 1700000500.0}\n'
        )
        instance.get_event_log_path.return_value = str(event_log_path)

        resp = client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH_HEADER)
        assert resp.status_code == 200
        zip_path = resp.json()["file_path"]

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())

            # All three segment CSVs plus event log
            assert "behavior_events_001.csv" in names
            assert "behavior_events_002.csv" in names
            assert "behavior_events_003.csv" in names
            assert "event_log.jsonl" in names
            # Suffix-less behavior_events.csv must NOT be present in a segmented export
            assert "behavior_events.csv" not in names

            # Prior segment CSVs should be byte-identical passthrough
            assert zf.read("behavior_events_001.csv").decode() == seg1_content
            assert zf.read("behavior_events_002.csv").decode() == seg2_content

            # Final segment from the in-memory buffer
            final_csv = zf.read("behavior_events_003.csv").decode()
            final_rows = list(csv.DictReader(io.StringIO(final_csv)))
            assert len(final_rows) == 2
            assert final_rows[0]["device"] == "lever"
            assert final_rows[1]["device"] == "pump"

            # Metadata reflects all three segments
            meta = json.loads(zf.read("metadata.json"))
            assert meta["segment_count"] == 3
            assert meta["per_segment_event_counts"] == [3, 5, 2]
            assert meta["behavior_event_count"] == 10

            # Event log round-trips
            assert zf.read("event_log.jsonl").decode() == event_log_path.read_text()

    @pytest.mark.parametrize("stored_filename,expected_stem", [
        ("run1.zip", "run1"),
        ("run2.ZIP", "run2"),
        ("run3.tar.gz", "run3"),
        ("run4.tgz", "run4"),
        ("run5", "run5"),  # negative control — unchanged
    ])
    def test_export_zip_strips_doubled_archive_suffix(self, client, tmp_path, stored_filename, expected_stem):
        """Filenames with archive suffixes must not produce `{name}.zip.zip` downloads."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = stored_filename
        instance.get_data_destination.return_value = str(tmp_path)
        folder = tmp_path / expected_stem
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)
        instance.get_behavior_data.return_value = []
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_hardware_settings.return_value = []
        instance.get_frame_data.return_value = []
        instance.get_segment_exports.return_value = []
        instance.get_segment_event_counts.return_value = []

        resp = client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH_HEADER)
        assert resp.status_code == 200
        file_path = resp.json()["file_path"]

        # Exactly one `.zip` at the tail — never doubled.
        assert file_path.endswith(f"{expected_stem}.zip")
        assert not file_path.endswith(".zip.zip")

        # Contents must remain the flat layout (no nested archives).
        with zipfile.ZipFile(file_path) as zf:
            names = zf.namelist()
            assert not any(n.lower().endswith((".zip", ".tar", ".gz", ".tgz")) for n in names)

        # The cleaned filename must be pushed back into the kernel so downstream
        # folder/segment naming uses the same stem.
        assert instance.set_filename.called
        assert instance.set_filename.call_args[0][0] == expected_stem

    def test_set_file_config_strips_archive_suffix(self, client):
        """POST /config must not persist a `.zip`-suffixed filename into the kernel."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = "experiment"
        instance.get_data_destination.return_value = "/tmp"

        resp = client.post(
            f"/api/file/{sid}/config",
            json={"filename": "experiment.zip"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        instance.set_filename.assert_called_with("experiment")

    def test_export_zip_missing_segment_file_logs_and_continues(self, client, tmp_path):
        """A missing on-disk segment CSV should not break the export."""
        resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        sid = resp.json()["session_id"]

        sm = client.app.state.session_manager
        instance = sm.get_instance(sid)
        instance.get_filename.return_value = "missing_seg"
        instance.get_data_destination.return_value = str(tmp_path)
        folder = tmp_path / "missing_seg"
        folder.mkdir()
        instance.make_destination_folder.return_value = str(folder)

        # One real segment on disk, one ghost path that doesn't exist
        real_seg = tmp_path / "behavior_events_001.csv"
        real_seg.write_text(
            "device,event,start_timestamp,end_timestamp,start_frame_index,end_frame_index\n"
            "lever,press,1,2,,\n"
        )
        ghost_seg = tmp_path / "behavior_events_002.csv"  # never written
        instance.get_segment_exports.return_value = [str(real_seg), str(ghost_seg)]
        instance.get_segment_event_counts.return_value = [1, 4]

        instance.get_behavior_data.return_value = [
            {"device": "lever", "event": "press", "start_timestamp": 50, "end_timestamp": 60}
        ]
        instance.get_frame_data.return_value = []
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_hardware_settings.return_value = []

        resp = client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH_HEADER)
        assert resp.status_code == 200

        with zipfile.ZipFile(resp.json()["file_path"]) as zf:
            names = set(zf.namelist())
            assert "behavior_events_001.csv" in names  # survivor
            assert "behavior_events_002.csv" not in names  # ghost — skipped
            assert "behavior_events_003.csv" in names  # final segment from buffer

            meta = json.loads(zf.read("metadata.json"))
            # segment_count reflects the intended layout even if one file was missing
            assert meta["segment_count"] == 3
            assert meta["per_segment_event_counts"] == [1, 4, 1]
