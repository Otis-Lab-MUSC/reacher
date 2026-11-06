"""Router handlers catch the failures their kernel calls can raise, and let a bug in them surface.

A realistic failure (serial, OS, rejected argument) keeps its mapped response. A TypeError stands in
for a programming error: it must reach the server's error handling instead of being reported as an
ordinary failed request or swallowed.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
import serial
from fastapi.testclient import TestClient

from reacher import pump_target
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH = {"Authorization": f"Bearer {API_KEY}"}
PORT = "/dev/ttyUSB0"
FREE_COMMAND = {"code": 100, "value": 1}
SET_ACTIVE_PUMP = {"code": 221, "value": 1}


@pytest.fixture
def api():
    """Return a TestClient over a mocked REACHER, with ``client.instance`` the mock."""
    with patch("reacher.session_manager.REACHER") as mock_class, patch("os.makedirs"):
        instance = Mock()
        instance.ser = Mock(is_open=False)
        instance.program_running = False
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_firmware_caps.return_value = set()
        instance._firmware_ready.wait.return_value = False
        instance.get_detected_paradigm.return_value = None
        mock_class.return_value = instance
        with TestClient(create_app()) as client:
            client.instance = instance
            yield client


def _session(client, state="connected"):
    resp = client.post("/api/sessions", json={"port": PORT, "paradigm": "fr"}, headers=AUTH)
    session_id = resp.json()["session_id"]
    client.app.state.session_manager.set_state(session_id, state)
    return session_id


class TestHardwareCommand:
    """POST /api/hardware/{id}/command and PUT /api/hardware/{id}/pins."""

    def test_kernel_failure_is_500(self, api):
        sid = _session(api)
        api.instance.send_command.side_effect = serial.SerialException("device disconnected")
        resp = api.post(f"/api/hardware/{sid}/command", json=FREE_COMMAND, headers=AUTH)
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Command failed"

    def test_programming_error_is_not_reported_as_a_failed_command(self, api):
        sid = _session(api)
        api.instance.send_command.side_effect = TypeError("bug in the kernel call")
        with pytest.raises(TypeError):
            api.post(f"/api/hardware/{sid}/command", json=FREE_COMMAND, headers=AUTH)

    def test_pump_target_write_failure_does_not_fail_the_command(self, api):
        sid = _session(api)
        with patch.object(pump_target, "save", side_effect=OSError("disk full")):
            resp = api.post(f"/api/hardware/{sid}/command", json=SET_ACTIVE_PUMP, headers=AUTH)
        assert resp.status_code == 200

    def test_pump_target_programming_error_is_not_swallowed(self, api):
        sid = _session(api)
        with patch.object(pump_target, "save", side_effect=TypeError("bug")):
            with pytest.raises(TypeError):
                api.post(f"/api/hardware/{sid}/command", json=SET_ACTIVE_PUMP, headers=AUTH)

    def test_pin_send_failure_is_reported_per_component(self, api):
        sid = _session(api)
        api.instance.send_command.side_effect = OSError("write failed")
        resp = api.put(f"/api/hardware/{sid}/pins", json={"assignments": {"pump": 4}}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["errors"] == [{"component": "pump", "error": "write failed"}]

    def test_pin_send_programming_error_is_not_listed_as_a_component_error(self, api):
        sid = _session(api)
        api.instance.send_command.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            api.put(f"/api/hardware/{sid}/pins", json={"assignments": {"pump": 4}}, headers=AUTH)


@pytest.mark.parametrize(
    ("verb", "kernel_method", "state"),
    [
        ("start", "start_program", "connected"),
        ("stop", "stop_program", "running"),
        ("split", "split_segment", "running"),
        ("restart", "restart_program", "running"),
    ],
)
class TestProgramControl:
    """The program endpoints map a kernel failure to 500 and let a bug through."""

    def _call(self, api, verb, kernel_method, state, error):
        sid = _session(api, state)
        api.instance.ser.is_open = True
        api.instance.program_running = True
        getattr(api.instance, kernel_method).side_effect = error
        return api.post(f"/api/program/{sid}/{verb}", headers=AUTH)

    def test_kernel_failure_is_500(self, api, verb, kernel_method, state):
        resp = self._call(api, verb, kernel_method, state, OSError("port gone"))
        assert resp.status_code == 500

    def test_programming_error_surfaces(self, api, verb, kernel_method, state):
        with pytest.raises(TypeError):
            self._call(api, verb, kernel_method, state, TypeError("bug"))


class TestSession:
    """POST /api/sessions/{id}/reset."""

    def test_reset_failure_is_500(self, api):
        sid = _session(api)
        api.instance.open_serial.side_effect = serial.SerialException("busy")
        assert api.post(f"/api/sessions/{sid}/reset", headers=AUTH).status_code == 500

    def test_reset_programming_error_surfaces(self, api):
        sid = _session(api)
        api.instance.reset.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            api.post(f"/api/sessions/{sid}/reset", headers=AUTH)


class TestSerial:
    """POST /api/serial/{id}/connect and /disconnect."""

    def _connect(self, api):
        sid = _session(api, "idle")
        # Keep the websocket event queue out of it: other tests can leave a closed loop behind.
        with patch("reacher.api.routers.serial._ws.enqueue_event"):
            return api.post(f"/api/serial/{sid}/connect", headers=AUTH)

    def test_open_failure_is_500(self, api):
        api.instance.open_serial.side_effect = serial.SerialException("could not open port")
        assert self._connect(api).status_code == 500

    def test_open_programming_error_surfaces(self, api):
        api.instance.open_serial.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            self._connect(api)

    def test_identify_failure_does_not_fail_the_connect(self, api):
        api.instance.send_command.side_effect = OSError("write failed")
        resp = self._connect(api)
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"

    def test_identify_programming_error_surfaces(self, api):
        api.instance.send_command.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            self._connect(api)

    def test_disconnect_failure_is_500(self, api):
        sid = _session(api)
        api.instance.close_serial.side_effect = OSError("busy")
        assert api.post(f"/api/serial/{sid}/disconnect", headers=AUTH).status_code == 500

    def test_disconnect_programming_error_surfaces(self, api):
        sid = _session(api)
        api.instance.close_serial.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            api.post(f"/api/serial/{sid}/disconnect", headers=AUTH)


class TestFirmwareUpload:
    """POST /api/firmware/upload/{id}."""

    def _upload(self, api, **body):
        sid = _session(api)
        with patch("reacher.api.routers.firmware.asyncio.sleep", new=AsyncMock()):
            return sid, api.post(f"/api/firmware/upload/{sid}", json={"paradigm": "fr", **body}, headers=AUTH)

    def _post(self, api, sid):
        with patch("reacher.api.routers.firmware.asyncio.sleep", new=AsyncMock()):
            return api.post(f"/api/firmware/upload/{sid}", json={"paradigm": "fr"}, headers=AUTH)

    def _state(self, api, sid):
        return api.app.state.session_manager.get_session(sid).state

    def test_invalid_base64_is_400(self, api):
        _, resp = self._upload(api, hex_data="not base64 !!")
        assert resp.status_code == 400

    def test_non_ascii_base64_is_400(self, api):
        _, resp = self._upload(api, hex_data="é")
        assert resp.status_code == 400

    def test_uploader_os_error_is_500_and_session_returns_to_idle(self, api):
        with patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(side_effect=OSError("no avrdude"))):
            sid, resp = self._upload(api)
        assert resp.status_code == 500
        assert self._state(api, sid) == "idle"

    def test_uploader_programming_error_surfaces_and_session_returns_to_idle(self, api):
        sid = _session(api)
        with patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(side_effect=TypeError("bug"))):
            with pytest.raises(TypeError):
                self._post(api, sid)
        assert self._state(api, sid) == "idle"

    def test_reconnect_failure_is_500_and_session_returns_to_idle(self, api):
        api.instance.open_serial.side_effect = serial.SerialException("busy")
        with patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(return_value=True)):
            sid, resp = self._upload(api)
        assert resp.status_code == 500
        assert self._state(api, sid) == "idle"

    def test_reconnect_programming_error_surfaces_and_session_returns_to_idle(self, api):
        sid = _session(api)
        api.instance.open_serial.side_effect = TypeError("bug")
        with patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(return_value=True)):
            with pytest.raises(TypeError):
                self._post(api, sid)
        assert self._state(api, sid) == "idle"

    def test_reconnect_termios_failure_is_500_and_session_returns_to_idle(self, api):
        termios = pytest.importorskip("termios")
        api.instance.open_serial.side_effect = termios.error(5, "Input/output error")
        with patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(return_value=True)):
            sid, resp = self._upload(api)
        assert resp.status_code == 500
        assert self._state(api, sid) == "idle"

    def test_a_second_upload_is_refused_without_disturbing_the_first(self, api):
        sid = _session(api, "uploading")
        resp = self._post(api, sid)
        assert resp.status_code == 409
        assert self._state(api, sid) == "uploading"
