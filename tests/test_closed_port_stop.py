"""stop_program() on a closed or failing serial port must still export buffered data."""

import os
import threading
import time
from unittest.mock import Mock

import pytest

from reacher.kernel.reacher import REACHER
from reacher.session_manager import SessionManager


FAKE_EVENTS = [
    {"device": "LEVER", "event": "PRESS", "start_timestamp": "1000", "end_timestamp": "1000"},
    {"device": "PUMP", "event": "ON", "start_timestamp": "2000", "end_timestamp": "2500"},
]


def _export_path(instance):
    return os.path.join(instance.reacher_log_path, "behavior_events.csv")


@pytest.fixture
def running_reacher():
    instance = REACHER(session_id="test-closed-port-r4")
    instance.ser = Mock(is_open=False)
    instance.program_running = True
    instance.behavior_data = [dict(row) for row in FAKE_EVENTS]
    yield instance
    instance.serial_flag.set()
    instance.program_running = False


def test_closed_port_skips_end_handshake_and_still_exports(running_reacher, monkeypatch):
    instance = running_reacher
    send = Mock()
    monkeypatch.setattr(instance, "send_serial_command", send)
    exported = []
    real_export = instance._auto_export
    monkeypatch.setattr(instance, "_auto_export", lambda: (exported.append(True), real_export()))

    start = time.monotonic()
    instance.stop_program()
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"stop_program() took {elapsed:.2f}s on a closed port"
    send.assert_not_called()
    assert exported == [True]
    assert os.path.exists(_export_path(instance))


def test_send_failure_on_open_port_still_exports(running_reacher, monkeypatch):
    instance = running_reacher
    instance.ser = Mock(is_open=True)
    monkeypatch.setattr(instance, "send_serial_command", Mock(side_effect=Exception("Serial port is not open.")))

    start = time.monotonic()
    instance.stop_program()
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"stop_program() waited {elapsed:.2f}s after a failed send"
    assert os.path.exists(_export_path(instance))


def test_end_handshake_on_open_port_unchanged(running_reacher, monkeypatch):
    instance = running_reacher
    instance.ser = Mock(is_open=True)
    send = Mock()
    monkeypatch.setattr(instance, "send_serial_command", send)
    threading.Timer(0.1, instance._controller_end_received.set).start()

    start = time.monotonic()
    instance.stop_program()
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"stop_program() took {elapsed:.2f}s with CONTROLLER END arriving"
    send.assert_called_once_with({"cmd": 100})
    assert os.path.exists(_export_path(instance))


def test_destroy_running_session_with_closed_port_exports(monkeypatch):
    sm = SessionManager()
    sid = sm.create_session("SIMULATOR", "fr")
    instance = sm.get_session(sid).instance
    instance.ser = Mock(is_open=False)
    instance.program_running = True
    instance.behavior_data = [dict(row) for row in FAKE_EVENTS]

    sm.destroy_session(sid)

    assert os.path.exists(_export_path(instance))
    with pytest.raises(KeyError):
        sm.get_session(sid)
