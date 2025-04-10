import pytest
import serial
import queue
import threading
import time
import csv
import json
import os
from src.reacher.core.reacher import REACHER, logger
from unittest.mock import Mock, patch, call

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
    assert reacher.arduino_configuration == {}

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
    
    reacher.set_COM_port("COM2")  # Should not change if COM2 isn’t available
    assert reacher.ser.port == "COM1"

def test_open_serial(reacher, mock_serial):
    """Test that open_serial opens the port and sends the LINK command."""
    reacher.serial_flag.clear()
    reacher.open_serial()
    mock_serial.open.assert_called_once()
    mock_serial.write.assert_called_with(b"LINK\n")
    mock_serial.reset_input_buffer.assert_called_once()

def test_close_serial(reacher, mock_serial):
    """Test that close_serial closes the port and sends the UNLINK command."""
    reacher.ser.is_open = True
    reacher.close_serial()
    mock_serial.write.assert_called_with(b"UNLINK\n")
    mock_serial.close.assert_called_once()
    assert reacher.serial_flag.is_set()

def test_send_serial_command(reacher, mock_serial):
    """Test that send_serial_command sends data when port is open, raises error when closed."""
    reacher.ser.is_open = True
    reacher.send_serial_command("TEST")
    mock_serial.write.assert_called_with(b"TEST\n")
    mock_serial.flush.assert_called_once()
    
    reacher.ser.is_open = False
    with pytest.raises(Exception, match="Serial port is not open"):
        reacher.send_serial_command("TEST")

def test_handle_data_json(reacher):
    """Test that handle_data processes JSON configuration correctly."""
    config = {"key": "value"}
    reacher.handle_data(json.dumps(config))
    assert reacher.arduino_configuration == config

def test_handle_behavioral_events(reacher, mocker):
    """Test that handle_data processes behavioral event data into behavior_data."""
    mocker.patch("csv.DictWriter")
    mocker.patch("builtins.open", mocker.mock_open())
    
    data = "PUMP,INFUSION,12345,12346"
    reacher.handle_data(data)
    assert reacher.behavior_data == [{
        "Component": "PUMP",
        "Action": "INFUSION",
        "Start Timestamp": 12345,
        "End Timestamp": 12346
    }]

def test_handle_frame_events(reacher, mocker):
    """Test that handle_data processes frame event data into frame_data."""
    mocker.patch("csv.DictWriter")
    mocker.patch("builtins.open", mocker.mock_open())
    
    data = "_,54321"
    reacher.handle_data(data)
    assert reacher.frame_data == ["54321"]

def test_start_program(reacher, mock_serial):
    """Test that start_program sets flags, sends command, and records start time."""
    with patch("time.time", return_value=1000.0):
        reacher.start_program()
    assert not reacher.program_flag.is_set()
    mock_serial.write.assert_called_with(b"START-PROGRAM\n")
    assert reacher.program_start_time == 1000.0

def test_stop_program(reacher, mocker):
    """Test that stop_program resets flags, sends command, and records end time."""
    mocker.patch.object(reacher, "clear_queue")
    mocker.patch.object(reacher, "close_serial")
    with patch("time.time", return_value=2000.0):
        reacher.stop_program()
    assert reacher.program_flag.is_set()
    reacher.ser.write.assert_called_with(b"END-PROGRAM\n")
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
    mocker.patch("csv.DictWriter")
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch.object(reacher, "stop_program")
    
    reacher.limit_type = "Infusion"
    reacher.infusion_limit = 2
    reacher.stop_delay = 5
    
    # Start the program
    reacher.program_flag.clear()
    reacher.program_start_time = 1000.0  # Set start time for elapsed_time calculation
    
    # Add infusion events
    reacher.handle_data("PUMP,INFUSION,1005000,1006000")  # First infusion
    reacher.handle_data("PUMP,INFUSION,1010000,1011000")  # Second infusion
    
    # Check limit met after second infusion
    with patch("time.time", return_value=1010.0):
        reacher.check_limit_met()
        assert reacher.last_infusion_time == 1010.0  # Should be set here
    
    # Check stop after delay
    with patch("time.time", return_value=1016.0):  # 6 seconds after 1010
        reacher.check_limit_met()
    reacher.stop_program.assert_called_once()

def test_set_data_destination_and_filename(reacher):
    """Test that set_data_destination and set_filename update paths correctly."""
    reacher.set_data_destination("/path/to/data")
    assert reacher.data_destination == "/path/to/data"
    
    reacher.set_filename("test")
    assert reacher.behavior_filename == "test.csv"
    
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