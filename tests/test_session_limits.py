"""Infusion/time limit enforcement: no-delay default, reset keeps limits, split-proof counting."""

import logging
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.reacher import REACHER

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def reacher(mock_serial):
    with (
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        instance = REACHER()
    instance.stop_program = Mock()
    instance.program_start_time = 1000.0
    return instance


@pytest.fixture
def mock_serial():
    with patch("serial.Serial") as mock_serial_class, patch("serial.tools.list_ports.comports") as mock_comports:
        mock_serial_instance = Mock()
        mock_serial_instance.baudrate = 115200
        mock_serial_class.return_value = mock_serial_instance
        mock_comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
        yield mock_serial_instance


def _at(monkeypatch, now):
    monkeypatch.setattr(time, "time", lambda: now)


def test_infusion_limit_without_delay_stops(reacher, monkeypatch):
    reacher.set_limit_type("Infusion")
    reacher.set_infusion_limit(2)
    reacher._infusion_count = 2
    _at(monkeypatch, 1010.0)

    reacher.check_limit_met()

    reacher.stop_program.assert_called_once()


def test_both_limit_without_delay_stops_on_infusion_and_on_time(reacher, monkeypatch):
    reacher.set_limit_type("Both")
    reacher.set_infusion_limit(2)
    reacher.set_time_limit(60)

    reacher._infusion_count = 2
    _at(monkeypatch, 1010.0)
    reacher.check_limit_met()
    reacher.stop_program.assert_called_once()

    reacher.stop_program.reset_mock()
    reacher._infusion_count = 0
    _at(monkeypatch, 1061.0)
    reacher.check_limit_met()
    reacher.stop_program.assert_called_once()


def test_explicit_delay_holds_stop_until_elapsed(reacher, monkeypatch):
    reacher.set_limit_type("Infusion")
    reacher.set_infusion_limit(1)
    reacher.set_stop_delay(5)
    reacher._infusion_count = 1

    _at(monkeypatch, 1010.0)
    reacher.check_limit_met()
    reacher.stop_program.assert_not_called()

    _at(monkeypatch, 1014.9)
    reacher.check_limit_met()
    reacher.stop_program.assert_not_called()

    _at(monkeypatch, 1015.0)
    reacher.check_limit_met()
    reacher.stop_program.assert_called_once()


def test_split_does_not_restart_infusion_count(reacher, monkeypatch):
    reacher.set_limit_type("Infusion")
    reacher.set_infusion_limit(3)
    reacher._cumulative_infusion_count = 2
    reacher._infusion_count = 1
    _at(monkeypatch, 1010.0)

    reacher.check_limit_met()

    reacher.stop_program.assert_called_once()


def test_reset_preserves_limits(reacher, mocker):
    reacher.set_limit_type("Both")
    reacher.set_infusion_limit(4)
    reacher.set_time_limit(120)
    reacher.set_stop_delay(9)
    mocker.patch.object(reacher, "clear_queue")
    mocker.patch.object(reacher, "close_serial")
    mocker.patch("threading.Thread")

    reacher.reset()

    assert reacher.limit_type == "Both"
    assert reacher.infusion_limit == 4
    assert reacher.time_limit == 120
    assert reacher.stop_delay == 9


def test_half_configured_limits_never_raise(reacher, monkeypatch):
    reacher.set_limit_type("Infusion")
    reacher._infusion_count = 99
    _at(monkeypatch, 2000.0)
    reacher.check_limit_met()

    reacher.set_limit_type("Both")
    reacher.set_infusion_limit(None)
    reacher.set_time_limit(None)
    reacher.check_limit_met()

    reacher.stop_program.assert_not_called()


def test_api_omitted_delay_keeps_earlier_explicit_delay():
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/api/sessions", json={"port": "SIMULATOR"}, headers=AUTH_HEADER)
        assert resp.status_code == 201
        sid = resp.json()["session_id"]
        instance = app.state.session_manager.get_session(sid).instance
        try:
            assert instance.stop_delay == 0

            body = {"type": "Infusion", "infusion_limit": 2}
            assert client.post(f"/api/program/{sid}/limit", json=body, headers=AUTH_HEADER).status_code == 200
            assert instance.stop_delay == 0

            body["delay"] = 7
            assert client.post(f"/api/program/{sid}/limit", json=body, headers=AUTH_HEADER).status_code == 200
            assert instance.stop_delay == 7

            body.pop("delay")
            assert client.post(f"/api/program/{sid}/limit", json=body, headers=AUTH_HEADER).status_code == 200
            assert instance.stop_delay == 7
        finally:
            client.delete(f"/api/sessions/{sid}", headers=AUTH_HEADER)
