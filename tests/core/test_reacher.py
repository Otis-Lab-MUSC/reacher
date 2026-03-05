import pytest
import queue
import json
import time
from unittest.mock import Mock, patch, call
from reacher.kernel.reacher import REACHER


@pytest.fixture
def mock_serial():
    """Fixture providing a mocked serial connection with predefined COM ports."""
    with patch("serial.Serial") as mock_serial_class, \
         patch("serial.tools.list_ports.comports") as mock_comports:
        mock_serial_instance = Mock()
        mock_serial_instance.baudrate = 115200
        mock_serial_class.return_value = mock_serial_instance
        mock_comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
        yield mock_serial_instance


@pytest.fixture
def reacher(mock_serial):
    """Fixture providing a REACHER instance with mocked threading and logging."""
    with patch("threading.Thread"), \
         patch("os.makedirs"), \
         patch("logging.basicConfig"):
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
    with patch("threading.Thread"), \
         patch("os.makedirs"), \
         patch("logging.basicConfig"):
        cb = Mock()
        r = REACHER(session_id="abc123", event_callback=cb)
        assert r.session_id == "abc123"
        assert r.event_callback is cb


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
    """Test that get_COM_ports returns available ports or a fallback message."""
    ports = reacher.get_COM_ports()
    assert ports == ["COM1"]

    with patch("serial.tools.list_ports.comports", return_value=[]):
        ports = reacher.get_COM_ports()
        assert ports == ["No available ports"]


def test_set_COM_port(reacher):
    """Test that set_COM_port updates the serial port only if valid."""
    reacher.set_COM_port("COM1")
    assert reacher.ser.port == "COM1"

    reacher.set_COM_port("COM2")  # Should not change if COM2 isn't available
    assert reacher.ser.port == "COM1"


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
    expected = json.dumps({"cmd": 101}).encode() + b'\n'
    mock_serial.write.assert_called_with(expected)
    mock_serial.flush.assert_called_once()

    reacher.ser.is_open = False
    with pytest.raises(Exception, match="Serial port is not open"):
        reacher.send_serial_command({"cmd": 100})


def test_send_command(reacher, mock_serial):
    """Test that send_command uses the command registry to build payloads."""
    reacher.ser.is_open = True
    reacher.send_command(371, 8000)
    expected = json.dumps({"cmd": 371, "frequency": 8000}).encode() + b'\n'
    mock_serial.write.assert_called_with(expected)


def test_send_command_no_value(reacher, mock_serial):
    """Test send_command without a value."""
    reacher.ser.is_open = True
    reacher.send_command(101)
    expected = json.dumps({"cmd": 101}).encode() + b'\n'
    mock_serial.write.assert_called_with(expected)


def test_handle_data_json_config(reacher, mocker):
    """Test that handle_data processes JSON firmware configuration."""
    mocker.patch("builtins.open", mocker.mock_open())
    config = {"level": "000", "device": "CONTROLLER", "sketch": "fr", "version": "v2.0.0"}
    reacher.handle_data(json.dumps(config))
    assert reacher.firmware_information == config


def test_handle_data_hardware_settings(reacher, mocker):
    """Test that handle_data appends hardware settings for non-controller devices."""
    mocker.patch("builtins.open", mocker.mock_open())
    hw = {"level": "000", "device": "CUE", "frequency": 2900}
    reacher.handle_data(json.dumps(hw))
    assert hw in reacher.hardware_settings


def test_handle_behavioral_events(reacher, mocker):
    """Test that handle_data processes behavioral event data into behavior_data."""
    mocker.patch("builtins.open", mocker.mock_open())
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


def test_handle_frame_events(reacher, mocker):
    """Test that handle_data processes frame event data into frame_data."""
    mocker.patch("builtins.open", mocker.mock_open())
    event = {"level": "008", "timestamp": 54321}
    reacher.handle_data(json.dumps(event))
    assert reacher.frame_data == [54321]


def test_start_program(reacher, mock_serial):
    """Test that start_program sets flags, sends command, and records start time."""
    reacher.ser.is_open = True
    with patch("time.time", return_value=1000.0):
        reacher.start_program()
    assert not reacher.program_flag.is_set()
    expected = json.dumps({"cmd": 101}).encode() + b'\n'
    mock_serial.write.assert_called_with(expected)
    assert reacher.program_start_time == 1000.0


def test_stop_program(reacher, mocker, mock_serial):
    """Test that stop_program resets flags, sends command, and records end time."""
    reacher.ser.is_open = True
    mocker.patch.object(reacher, "clear_queue")
    mocker.patch.object(reacher, "close_serial")
    with patch("time.time", return_value=2000.0):
        reacher.stop_program()
    assert reacher.program_flag.is_set()
    expected = json.dumps({"cmd": 100}).encode() + b'\n'
    mock_serial.write.assert_called_with(expected)
    reacher.clear_queue.assert_called_once()
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
    reacher.behavior_data.append({"device": "PUMP", "event": "INFUSION", "start_timestamp": 1005000, "end_timestamp": 1006000})
    reacher.behavior_data.append({"device": "PUMP", "event": "INFUSION", "start_timestamp": 1010000, "end_timestamp": 1011000})

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
    with patch("threading.Thread"), \
         patch("os.makedirs"), \
         patch("logging.basicConfig"):
        cb = Mock()
        r = REACHER(session_id="sess1", event_callback=cb)
        r._emit("event", {"foo": "bar"})
        cb.assert_called_once_with("sess1", "event", {"foo": "bar"})


def test_get_detected_paradigm(reacher):
    """Test paradigm detection from firmware info."""
    assert reacher.get_detected_paradigm() is None
    reacher.firmware_information = {"schedule": "FR", "device": "CONTROLLER"}
    assert reacher.get_detected_paradigm() == "fr"
