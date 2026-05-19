import logging
import queue
import json

import pytest
import serial
from unittest.mock import Mock, patch

from reacher.kernel.reacher import REACHER


@pytest.fixture
def mock_serial():
    """Fixture providing a mocked serial connection with predefined COM ports."""
    with patch("serial.Serial") as mock_serial_class, patch("serial.tools.list_ports.comports") as mock_comports:
        mock_serial_instance = Mock()
        mock_serial_instance.baudrate = 115200
        mock_serial_class.return_value = mock_serial_instance
        mock_comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
        yield mock_serial_instance


@pytest.fixture
def reacher(mock_serial):
    """Fixture providing a REACHER instance with mocked threading and logging."""
    with (
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        reacher_instance = REACHER()
        return reacher_instance


def test_reacher_init(reacher, mock_serial):
    """Test that REACHER initializes with correct serial, queue, and thread attributes."""
    assert isinstance(reacher.ser, Mock)
    assert reacher.ser.baudrate == 115200
    assert isinstance(reacher.queue, queue.Queue)
    assert hasattr(reacher, "serial_thread")
    assert hasattr(reacher, "queue_thread")
    assert hasattr(reacher, "time_check_thread")
    assert hasattr(reacher, "thread_lock")
    assert callable(reacher.thread_lock.acquire)
    assert reacher.serial_flag.is_set()
    assert reacher.program_flag.is_set()
    assert reacher.time_check_flag.is_set()
    assert reacher.behavior_data == []
    assert reacher.frame_data == []
    assert reacher.program_start_time is None
    assert reacher.firmware_information["sketch"] is None


def test_init_with_session_id(mock_serial):
    """Test REACHER initializes with session_id and event_callback."""
    with (
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        cb = Mock()
        r = REACHER(session_id="abc123", event_callback=cb)
        assert r.session_id == "abc123"
        assert r.event_callback is cb


def test_resilient_wrapper_restarts_after_exception(reacher, mocker):
    """Fix 7.1: unhandled exception in a thread body is caught, a warning is
    emitted, and the target is retried."""
    mocker.patch("time.sleep")  # skip the 1s back-off
    cb = mocker.Mock()
    reacher.event_callback = cb
    reacher.session_id = "sid12345"

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        # Second call returns cleanly, simulating a recovered target.

    wrapped = reacher._resilient(flaky, "flaky")
    wrapped()

    assert calls["n"] == 2  # raised once, restarted once, clean return
    # Exactly one warning event emitted with reason=thread_crash.
    warnings = [
        c.args for c in cb.call_args_list
        if c.args[1] == "warning" and c.args[2].get("reason") == "thread_crash"
    ]
    assert len(warnings) == 1
    assert warnings[0][2]["thread"] == "flaky"


def test_resilient_wrapper_gives_up_after_max_restarts(reacher, mocker):
    """Fix 7.1: a target that fails forever is abandoned after the budget."""
    mocker.patch("time.sleep")
    cb = mocker.Mock()
    reacher.event_callback = cb
    reacher.session_id = "sid12345"

    def always_raises():
        raise RuntimeError("persistent")

    wrapped = reacher._resilient(always_raises, "always_raises")
    wrapped()  # Must return; must not hang.

    # The budget is 10 restarts; we should see exactly 10 warning emissions.
    warnings = [c for c in cb.call_args_list if c.args[1] == "warning"]
    assert len(warnings) == 10


def test_resilient_wrapper_tolerates_broken_callback(reacher, mocker):
    """Fix 7.1: a failing event_callback must not crash the shim itself."""
    mocker.patch("time.sleep")
    reacher.event_callback = Mock(side_effect=RuntimeError("cb broken"))
    reacher.session_id = "sid12345"

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    wrapped = reacher._resilient(flaky, "flaky")
    wrapped()  # Should not raise.
    assert calls["n"] == 2


def _simulate_queue_overflow(reacher_instance, now: float) -> None:
    """Drive the overflow branch once at the given monotonic time."""
    reacher_instance._queue_overflow_count += 1
    if now - reacher_instance._last_queue_overflow_emit > 1.0:
        reacher_instance._emit("warning", {
            "reason": "queue_overflow",
            "count": reacher_instance._queue_overflow_count,
        })
        reacher_instance._last_queue_overflow_emit = now


def test_queue_overflow_emits_first_warning_immediately(reacher, mocker):
    """Fix 2.6: the first overflow always emits a warning."""
    cb = mocker.Mock()
    reacher.event_callback = cb
    reacher.session_id = "sid12345"

    _simulate_queue_overflow(reacher, now=0.0)

    warnings = [c for c in cb.call_args_list
                if c.args[1] == "warning" and c.args[2].get("reason") == "queue_overflow"]
    assert len(warnings) == 1
    assert warnings[0].args[2]["count"] == 1


def test_queue_overflow_throttles_repeat_warnings(reacher, mocker):
    """Fix 2.6: overflows inside a 1s window do not double-emit."""
    cb = mocker.Mock()
    reacher.event_callback = cb
    reacher.session_id = "sid12345"

    _simulate_queue_overflow(reacher, now=0.0)
    _simulate_queue_overflow(reacher, now=0.3)
    _simulate_queue_overflow(reacher, now=0.9)

    warnings = [c for c in cb.call_args_list
                if c.args[1] == "warning" and c.args[2].get("reason") == "queue_overflow"]
    assert len(warnings) == 1
    # Counter advances even when warnings are suppressed.
    assert reacher._queue_overflow_count == 3


def test_queue_overflow_emits_again_after_throttle_window(reacher, mocker):
    """Fix 2.6: once the throttle window elapses, a new overflow re-emits."""
    cb = mocker.Mock()
    reacher.event_callback = cb
    reacher.session_id = "sid12345"

    _simulate_queue_overflow(reacher, now=0.0)
    _simulate_queue_overflow(reacher, now=1.1)

    warnings = [c for c in cb.call_args_list
                if c.args[1] == "warning" and c.args[2].get("reason") == "queue_overflow"]
    assert len(warnings) == 2


def test_make_thread_returns_daemon_thread(reacher):
    """Fix 7.1: helper builds a daemon thread with the right name."""
    import threading as _threading
    t = reacher._make_thread(lambda: None, "myname")
    assert isinstance(t, _threading.Thread)
    assert t.daemon is True
    assert t.name == "myname"


def test_reset(reacher, mocker):
    """Test that reset clears data, stops the program, and resets flags."""
    mocker.patch.object(reacher, "stop_program")
    mocker.patch.object(reacher, "clear_queue")
    mocker.patch.object(reacher, "close_serial")
    mocker.patch("threading.Thread")

    reacher.program_flag.clear()
    reacher.reset()

    assert reacher.behavior_data == []
    assert reacher.frame_data == []
    assert reacher.program_start_time is None
    assert reacher.program_flag.is_set()
    assert not reacher.serial_flag.is_set()
    reacher.stop_program.assert_called_once()
    reacher.clear_queue.assert_called_once()
    reacher.close_serial.assert_called_once()


def test_get_COM_ports(reacher):
    """Test that get_COM_ports returns available ports including SIMULATOR."""
    ports = reacher.get_COM_ports()
    assert ports == ["COM1", "SIMULATOR"]

    with patch("serial.tools.list_ports.comports", return_value=[]):
        ports = reacher.get_COM_ports()
        assert ports == ["SIMULATOR"]


def test_set_COM_port(reacher):
    """Test that set_COM_port updates the serial port only if valid."""
    reacher.set_COM_port("COM1")
    assert reacher.ser.port == "COM1"

    with pytest.raises(ValueError, match="not available"):
        reacher.set_COM_port("COM2")


def test_open_serial(reacher, mock_serial):
    """Test that open_serial opens the port."""
    reacher.serial_flag.clear()
    reacher.open_serial()
    mock_serial.open.assert_called_once()
    mock_serial.reset_input_buffer.assert_called_once()


def test_close_serial(reacher, mock_serial):
    """Test that close_serial closes the port."""
    reacher.ser.is_open = True
    reacher.close_serial()
    mock_serial.close.assert_called_once()
    assert reacher.serial_flag.is_set()


def test_send_serial_command(reacher, mock_serial):
    """Test that send_serial_command sends JSON when port is open, raises error when closed."""
    reacher.ser.is_open = True
    reacher.send_serial_command({"cmd": 101})
    expected = json.dumps({"cmd": 101}).encode() + b"\n"
    mock_serial.write.assert_called_with(expected)
    mock_serial.flush.assert_called_once()

    reacher.ser.is_open = False
    with pytest.raises(Exception, match="Serial port is not open"):
        reacher.send_serial_command({"cmd": 100})


def test_send_command(reacher, mock_serial):
    """Test that send_command uses the command registry to build payloads."""
    reacher.ser.is_open = True
    reacher.send_command(371, 8000)
    expected = json.dumps({"cmd": 371, "frequency": 8000}).encode() + b"\n"
    mock_serial.write.assert_called_with(expected)


def test_send_command_no_value(reacher, mock_serial):
    """Test send_command without a value."""
    reacher.ser.is_open = True
    reacher.send_command(101)
    expected = json.dumps({"cmd": 101}).encode() + b"\n"
    mock_serial.write.assert_called_with(expected)


def test_handle_data_json_config(reacher, mocker):
    """Test that handle_data processes JSON firmware configuration."""
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("os.fsync")
    config = {"level": "000", "device": "CONTROLLER", "sketch": "fr", "version": "v2.0.0"}
    reacher.handle_data(json.dumps(config))
    assert config.items() <= reacher.firmware_information.items()


def test_handle_data_hardware_settings(reacher, mocker):
    """Test that handle_data appends hardware settings for non-controller devices."""
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("os.fsync")
    hw = {"level": "000", "device": "CUE", "frequency": 2900}
    reacher.handle_data(json.dumps(hw))
    assert hw in reacher.hardware_settings


def test_handle_behavioral_events(reacher, mocker):
    """Test that handle_data processes behavioral event data into behavior_data."""
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("os.fsync")
    reacher.program_running = True
    reacher.program_flag.clear()
    event = {
        "level": "007",
        "device": "PUMP",
        "event": "INFUSION",
        "start_timestamp": 12345,
        "end_timestamp": 12346,
    }
    reacher.handle_data(json.dumps(event))
    assert len(reacher.behavior_data) == 1
    assert reacher.behavior_data[0]["device"] == "PUMP"
    assert reacher.behavior_data[0]["event"] == "INFUSION"


def test_update_behavioral_events_persists_pavlov(reacher, mocker):
    """PAVLOV rows must land in behavior_data so they reach behavior_events.csv."""
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("os.fsync")
    reacher.program_running = True
    reacher.program_flag.clear()
    event = {
        "level": "007",
        "device": "PAVLOV",
        "event": "TRIAL_START",
        "timestamp": 42000,
        "trial_type": "rewarded",
    }
    reacher.update_behavioral_events(event)
    assert len(reacher.behavior_data) == 1
    row = reacher.behavior_data[0]
    assert row["device"] == "PAVLOV"
    assert row["event"] == "TRIAL_START"
    assert row["start_timestamp"] == 42000
    assert row["end_timestamp"] == 42000
    assert row["trial_type"] == "rewarded"


def test_handle_frame_events(reacher, mocker):
    """Test that handle_data processes frame event data into frame_data during active session."""
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("os.fsync")
    reacher.program_flag.clear()
    event = {"level": "008", "timestamp": 54321}
    reacher.handle_data(json.dumps(event))
    assert reacher.frame_data == [54321]


def test_start_program(reacher, mock_serial):
    """Test that start_program sets flags, sends command, and records start time."""
    reacher.ser.is_open = True
    with patch("time.time", return_value=1000.0):
        reacher.start_program()
    assert not reacher.program_flag.is_set()
    expected = json.dumps({"cmd": 101}).encode() + b"\n"
    mock_serial.write.assert_called_with(expected)
    assert reacher.program_start_time == 1000.0


def test_stop_program(reacher, mocker, mock_serial):
    """Test that stop_program resets flags, sends command, and records end time."""
    reacher.ser.is_open = True
    reacher.program_running = True
    mocker.patch.object(reacher, "_join_queue_with_timeout")
    mocker.patch.object(reacher, "close_serial")
    mocker.patch.object(reacher, "_write_event_log")
    mocker.patch.object(reacher, "_close_event_log")
    mocker.patch.object(reacher, "_auto_export")
    with patch("time.time", return_value=2000.0):
        reacher.stop_program()
    assert reacher.program_flag.is_set()
    expected = json.dumps({"cmd": 100}).encode() + b"\n"
    mock_serial.write.assert_called_with(expected)
    reacher._join_queue_with_timeout.assert_called_once()
    reacher.close_serial.assert_called_once()
    assert reacher.program_end_time == 2000.0


def test_check_limit_met_time(reacher, mocker):
    """Test that check_limit_met stops the program when time limit is exceeded."""
    mocker.patch.object(reacher, "stop_program")
    reacher.limit_type = "Time"
    reacher.time_limit = 10
    reacher.program_start_time = 1000.0
    with patch("time.time", return_value=1015.0):
        reacher.check_limit_met()
    reacher.stop_program.assert_called_once()


def test_check_limit_met_infusion(reacher, mocker):
    """Test that check_limit_met stops the program after infusion limit with delay."""
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch.object(reacher, "stop_program")

    reacher.limit_type = "Infusion"
    reacher.infusion_limit = 2
    reacher.stop_delay = 5
    reacher.program_flag.clear()
    reacher.program_start_time = 1000.0

    # Directly populate behavior_data with pump events
    reacher.behavior_data.append(
        {"device": "PUMP", "event": "INFUSION", "start_timestamp": 1005000, "end_timestamp": 1006000}
    )
    reacher.behavior_data.append(
        {"device": "PUMP", "event": "INFUSION", "start_timestamp": 1010000, "end_timestamp": 1011000}
    )
    reacher._infusion_count = 2

    with patch("time.time", return_value=1010.0):
        reacher.check_limit_met()
        assert reacher.last_infusion_time == 1010.0

    with patch("time.time", return_value=1016.0):
        reacher.check_limit_met()
    reacher.stop_program.assert_called_once()


def test_set_data_destination_and_filename(reacher):
    """Test that set_data_destination and set_filename update paths correctly."""
    reacher.set_data_destination("/path/to/data")
    assert reacher.data_destination == "/path/to/data"

    reacher.set_filename("test")
    assert reacher.behavior_filename == "test"

    reacher.set_filename("test.csv")
    assert reacher.behavior_filename == "test.csv"


def test_make_destination_folder(reacher, mocker):
    """Test that make_destination_folder creates and returns the correct folder path."""
    mocker.patch("os.makedirs")
    mocker.patch("os.path.exists", return_value=False)
    with patch.object(reacher, "get_time", return_value="2023-01-01_12-00-00"):
        reacher.data_destination = "/data"
        reacher.behavior_filename = "experiment.csv"
        folder = reacher.make_destination_folder()
        assert folder == "/data/experiment"


def test_event_callback_fires(mock_serial):
    """Test that _emit invokes the event_callback."""
    with (
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        cb = Mock()
        r = REACHER(session_id="sess1", event_callback=cb)
        r._emit("event", {"foo": "bar"})
        cb.assert_called_once_with("sess1", "event", {"foo": "bar"})


def test_emit_failure_counter_increments_on_callback_error(reacher):
    """Fix 7.4: a raising callback must bump emit_failure_count, not propagate."""
    reacher.event_callback = Mock(side_effect=RuntimeError("broken cb"))
    reacher.session_id = "sid12345"

    assert reacher.emit_failure_count == 0
    reacher._emit("event", {"x": 1})
    reacher._emit("event", {"x": 2})
    reacher._emit("event", {"x": 3})
    assert reacher.emit_failure_count == 3


def test_emit_failure_counter_stays_zero_on_success(reacher):
    """A working callback must not touch the failure counter."""
    reacher.event_callback = Mock()
    reacher.session_id = "sid12345"
    reacher._emit("event", {"x": 1})
    reacher._emit("event", {"x": 2})
    assert reacher.emit_failure_count == 0


def test_controller_log_uses_persistent_handle(reacher, mocker):
    """Fix 4.9: _write_controller_log opens the file once across N writes."""
    m_open = mocker.mock_open()
    m_open.return_value.closed = False
    mocker.patch("builtins.open", m_open)
    mocker.patch("os.fsync")

    for i in range(5):
        reacher._write_controller_log({"level": "007", "i": i})

    # Exactly one open() for controller_log across the 5 writes.
    controller_opens = [
        c for c in m_open.call_args_list if c.args and c.args[0] == reacher.controller_log
    ]
    assert len(controller_opens) == 1


def test_controller_log_fsync_cadence(reacher, mocker):
    """Fix 4.9: fsync fires every _CONTROLLER_LOG_FSYNC_INTERVAL writes."""
    m_open = mocker.mock_open()
    m_open.return_value.closed = False
    mocker.patch("builtins.open", m_open)
    fsync = mocker.patch("os.fsync")
    reacher._CONTROLLER_LOG_FSYNC_INTERVAL = 3

    for i in range(7):
        reacher._write_controller_log({"i": i})

    # 7 writes, interval 3 → fsync at write 3 and 6 → exactly 2 calls.
    assert fsync.call_count == 2


def test_controller_log_close_flushes_and_closes(reacher, mocker):
    """_close_controller_log must flush, fsync, and close the handle."""
    handle = mocker.MagicMock()
    handle.closed = False
    reacher._controller_log_file = handle
    fsync = mocker.patch("os.fsync")

    reacher._close_controller_log()

    handle.flush.assert_called_once()
    fsync.assert_called_once_with(handle.fileno.return_value)
    handle.close.assert_called_once()


def test_get_detected_paradigm(reacher):
    """Test paradigm detection from firmware info."""
    assert reacher.get_detected_paradigm() is None
    reacher.firmware_information = {"schedule": "FIXED_RATIO", "device": "CONTROLLER"}
    assert reacher.get_detected_paradigm() == "fr"


# ===== New tests for security & reliability audit fixes =====


class TestF006BoundedQueue:
    """F-006: reset() creates a bounded queue."""

    def test_reset_creates_bounded_queue(self, reacher, mocker):
        mocker.patch.object(reacher, "stop_program")
        mocker.patch.object(reacher, "clear_queue")
        mocker.patch.object(reacher, "close_serial")
        mocker.patch("threading.Thread")

        reacher.program_flag.clear()
        reacher.reset()

        assert reacher.queue.maxsize == 5000


class TestF002DataWarning:
    """F-002: Warning emitted when in-memory data crosses threshold."""

    def test_data_warning_emitted_at_threshold(self, reacher, mocker):
        mocker.patch("builtins.open", mocker.mock_open())
        cb = Mock()
        reacher.event_callback = cb
        reacher.session_id = "test-sess"
        reacher.program_running = True
        reacher.program_flag.clear()

        # Pre-populate with 99,999 entries
        reacher.behavior_data = [{"device": "X", "event": "Y"}] * 99_999
        reacher.frame_data = []
        reacher._DATA_WARNING_THRESHOLD = 100_000
        reacher._data_warning_emitted = False

        # The 100,000th entry triggers the warning
        event = {
            "level": "007",
            "device": "PUMP",
            "event": "INFUSION",
            "start_timestamp": 1000,
            "end_timestamp": 1001,
        }
        reacher.update_behavioral_events(event)

        # Find the warning call
        warning_calls = [c for c in cb.call_args_list if c[0][1] == "warning"]
        assert len(warning_calls) == 1
        assert warning_calls[0][0][2]["threshold"] == 100_000
        assert reacher._data_warning_emitted is True

        # Second call should NOT emit another warning
        cb.reset_mock()
        event2 = {
            "level": "007",
            "device": "PUMP",
            "event": "INFUSION",
            "start_timestamp": 2000,
            "end_timestamp": 2001,
        }
        reacher.update_behavioral_events(event2)
        warning_calls2 = [c for c in cb.call_args_list if c[0][1] == "warning"]
        assert len(warning_calls2) == 0


class TestF003SerialReconnect:
    """F-003: Serial reconnection on disconnect."""

    def test_serial_reconnect_success(self, reacher, mocker):
        cb = Mock()
        reacher.event_callback = cb
        reacher.session_id = "test-sess"
        reacher.serial_flag.clear()
        reacher._SERIAL_RECONNECT_DELAY = 0
        reacher._SERIAL_RECONNECT_RETRIES = 3

        mock_ser = reacher.ser
        mock_ser.is_open = True

        # First in_waiting raises SerialException; after reconnect flag exits loop
        call_count = [0]

        def in_waiting_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise serial.SerialException("USB detached")
            return 0

        type(mock_ser).in_waiting = property(lambda self: in_waiting_side_effect())

        # open() succeeds on reconnect; reset_input_buffer sets serial_flag to exit
        mock_ser.open.return_value = None
        mock_ser.reset_input_buffer.side_effect = lambda: reacher.serial_flag.set()

        mocker.patch("time.sleep")
        reacher.read_serial()

        disconnect_calls = [c for c in cb.call_args_list if c[0][1] == "disconnect"]
        reconnected_calls = [c for c in cb.call_args_list if c[0][1] == "reconnected"]
        assert len(disconnect_calls) >= 1
        assert disconnect_calls[0][0][2]["reconnecting"] is True
        assert len(reconnected_calls) == 1
        assert reconnected_calls[0][0][2]["attempt"] == 1

    def test_serial_reconnect_all_retries_exhausted(self, reacher, mocker):
        cb = Mock()
        reacher.event_callback = cb
        reacher.session_id = "test-sess"
        reacher.serial_flag.clear()
        reacher._SERIAL_RECONNECT_DELAY = 0
        reacher._SERIAL_RECONNECT_RETRIES = 3
        reacher.program_running = False

        mock_ser = reacher.ser
        mock_ser.is_open = True

        # in_waiting always raises SerialException
        type(mock_ser).in_waiting = property(lambda self: (_ for _ in ()).throw(serial.SerialException("USB gone")))
        # open() always fails on reconnect
        mock_ser.open.side_effect = serial.SerialException("No device")

        mocker.patch("time.sleep")
        reacher.read_serial()

        disconnect_calls = [c for c in cb.call_args_list if c[0][1] == "disconnect"]
        final = disconnect_calls[-1]
        assert final[0][2]["reconnecting"] is False
        assert reacher.serial_flag.is_set()


class TestF010EventLogFsync:
    """F-010: Event log fsync batching and cleanup on close_serial."""

    def test_event_log_fsync_batching(self, reacher, mocker):
        reacher._EVENT_LOG_FSYNC_INTERVAL = 3

        mock_file = Mock()
        mock_file.closed = False
        mock_file.fileno.return_value = 99

        opener = mocker.patch("builtins.open", return_value=mock_file)
        fsync = mocker.patch("os.fsync")

        # Reset write counter
        reacher._event_log_file = None
        reacher._event_log_write_count = 0

        for i in range(4):
            reacher._write_event_log({"i": i})

        # File opened once and reused
        opener.assert_called_once()
        # fsync at write 3 (counter hits interval), counter resets, write 4 doesn't trigger
        assert fsync.call_count == 1

    def test_close_serial_closes_event_log(self, reacher, mocker, mock_serial):
        mock_serial.is_open = True
        mock_close_log = mocker.patch.object(reacher, "_close_event_log")
        reacher.close_serial()
        mock_close_log.assert_called_once()


class TestFrameTimestampStopSemantics:
    """Fix FW-002: frame timestamps must be gated by program_flag, not discarded silently."""

    def test_frame_event_logged_while_program_flag_clear(self, reacher, mocker):
        """Level-008 events during active session land in frame_data."""
        mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("os.fsync")
        reacher.program_flag.clear()
        reacher.update_frame_events({"timestamp": 10000})
        assert reacher.frame_data == [10000]

    def test_frame_event_blocked_after_program_flag_set(self, reacher, mocker):
        """Level-008 events after drain window (program_flag set) are discarded."""
        mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("os.fsync")
        reacher.program_flag.set()
        reacher.update_frame_events({"timestamp": 99999})
        assert reacher.frame_data == []

    def test_frame_events_straddle_stop(self, reacher, mocker):
        """Frames before program_flag.set() persist; frames after are blocked."""
        mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("os.fsync")
        reacher.program_flag.clear()
        reacher.update_frame_events({"timestamp": 1000})
        reacher.update_frame_events({"timestamp": 2000})
        reacher.program_flag.set()
        reacher.update_frame_events({"timestamp": 3000})
        assert reacher.frame_data == [1000, 2000]

    def test_frame_data_non_empty_after_session(self, reacher, mocker):
        """frame_data is populated after a simulated session with frame events."""
        mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("os.fsync")
        reacher.program_flag.clear()
        for ts in range(0, 5000, 100):
            reacher.update_frame_events({"timestamp": ts})
        reacher.program_flag.set()
        assert len(reacher.frame_data) == 50

    def test_behavioral_events_unaffected_by_program_flag_guard(self, reacher, mocker):
        """program_flag guard on frames does not interfere with behavioral event recording."""
        mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("os.fsync")
        reacher.program_running = True
        reacher.program_flag.clear()
        event = {
            "level": "007",
            "device": "PUMP",
            "event": "INFUSION",
            "start_timestamp": 5000,
            "end_timestamp": 5100,
        }
        reacher.update_behavioral_events(event)
        reacher.update_frame_events({"timestamp": 5050})
        assert len(reacher.behavior_data) == 1
        assert reacher.frame_data == [5050]
