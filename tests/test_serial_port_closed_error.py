"""A closed serial port raises a typed error that the API maps to 409."""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.errors import SerialPortClosedError
from reacher.kernel.reacher import REACHER

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}
SET_ACTIVE_PUMP_FREE_CODE = 100


@pytest.fixture
def closed_reacher():
    """Return a REACHER whose serial port reports closed."""
    with patch("reacher.kernel.reacher.serial.Serial") as serial_class:
        serial_class.return_value = Mock(is_open=False)
        instance = REACHER()
    instance.ser = Mock(is_open=False)
    return instance


def test_error_is_still_an_exception():
    assert issubclass(SerialPortClosedError, Exception)


def test_send_serial_command_raises_typed_error(closed_reacher):
    with pytest.raises(SerialPortClosedError, match="Serial port is not open"):
        closed_reacher.send_serial_command({"cmd": 100})


def test_start_program_raises_typed_error(closed_reacher):
    with pytest.raises(SerialPortClosedError, match="Serial port is not open"):
        closed_reacher.start_program()


@pytest.fixture
def api_client():
    """Return a TestClient whose REACHER mock raises on send_command."""
    with patch("reacher.session_manager.REACHER") as mock_class, patch("os.makedirs"):
        instance = Mock()
        instance.ser = Mock(is_open=False)
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        mock_class.return_value = instance
        with TestClient(create_app()) as client:
            client.instance = instance
            yield client


def _connected_session(client):
    resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"}, headers=AUTH_HEADER)
    session_id = resp.json()["session_id"]
    client.app.state.session_manager.set_state(session_id, "connected")
    return session_id


def test_command_on_closed_port_is_409(api_client):
    session_id = _connected_session(api_client)
    api_client.instance.send_command.side_effect = SerialPortClosedError("Serial port is not open.")
    resp = api_client.post(
        f"/api/hardware/{session_id}/command",
        json={"code": SET_ACTIVE_PUMP_FREE_CODE, "value": 1},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 409


def test_command_failure_is_500_without_leaking_detail(api_client):
    session_id = _connected_session(api_client)
    api_client.instance.send_command.side_effect = OSError("write failed")
    resp = api_client.post(
        f"/api/hardware/{session_id}/command",
        json={"code": SET_ACTIVE_PUMP_FREE_CODE, "value": 1},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Command failed"
