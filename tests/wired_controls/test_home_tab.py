import pytest
import panel as pn
from reacher.wired_controls.home_tab import HomeTab
from unittest.mock import Mock, patch

# Fixture to mock REACHER
@pytest.fixture
def mock_reacher():
    reacher = Mock()
    reacher.get_COM_ports = Mock(return_value=["COM1", "COM2"])
    reacher.set_COM_port = Mock()
    reacher.open_serial = Mock()
    reacher.close_serial = Mock()
    return reacher

# Fixture to initialize HomeTab with mocked REACHER
@pytest.fixture
def home_tab(mock_reacher):
    return HomeTab(reacher=mock_reacher)

def test_home_tab_init(home_tab):
    assert isinstance(home_tab, HomeTab)
    assert isinstance(home_tab.search_microcontrollers_button, pn.widgets.Button)
    assert home_tab.search_microcontrollers_button.name == "Search Microcontrollers"
    assert isinstance(home_tab.microcontroller_menu, pn.widgets.Select)
    assert home_tab.microcontroller_menu.name == "Microcontroller"
    assert home_tab.microcontroller_menu.options == []
    assert isinstance(home_tab.serial_connect_button, pn.widgets.Button)
    assert home_tab.serial_connect_button.name == "Connect"
    assert isinstance(home_tab.serial_disconnect_button, pn.widgets.Button)
    assert home_tab.serial_disconnect_button.name == "Disconnect"
    # Check inherited attributes from Dashboard
    assert isinstance(home_tab.header, pn.pane.Alert)
    assert isinstance(home_tab.response_textarea, pn.pane.HTML)
    assert isinstance(home_tab.toggle_button, pn.widgets.Button)
    assert isinstance(home_tab.reset_button, pn.widgets.Button)
    assert home_tab.dashboard is None

def test_search_for_microcontrollers_success(home_tab, mock_reacher, mocker):
    mocker.patch.object(home_tab, "add_response")
    home_tab.search_for_microcontrollers(None)
    home_tab.add_response.assert_any_call("Searching for microcontrollers...")
    mock_reacher.get_COM_ports.assert_called_once()
    assert home_tab.microcontroller_menu.options == ["COM1", "COM2"]
    home_tab.add_response.assert_any_call("Found 2 available ports.")

def test_search_for_microcontrollers_no_ports(home_tab, mock_reacher, mocker):
    mock_reacher.get_COM_ports.return_value = ["No available ports"]
    mocker.patch.object(home_tab, "add_response")
    home_tab.search_for_microcontrollers(None)
    home_tab.add_response.assert_any_call("Searching for microcontrollers...")
    mock_reacher.get_COM_ports.assert_called_once()
    assert home_tab.microcontroller_menu.options == []
    home_tab.add_response.assert_any_call("No valid COM ports found. Please connect a device and try again.")

def test_set_COM_success(home_tab, mock_reacher, mocker):
    home_tab.microcontroller_menu.value = "COM1"
    mocker.patch.object(home_tab, "add_response")
    home_tab.set_COM()
    mock_reacher.set_COM_port.assert_called_once_with("COM1")
    home_tab.add_response.assert_called_once_with("Set COM port to COM1")

def test_set_COM_failure(home_tab, mock_reacher, mocker):
    home_tab.microcontroller_menu.value = "COM1"
    mock_reacher.set_COM_port.side_effect = Exception("Invalid port")
    mocker.patch.object(home_tab, "add_error")
    home_tab.set_COM()
    mock_reacher.set_COM_port.assert_called_once_with("COM1")
    home_tab.add_error.assert_called_once_with("Exception caught while setting COM port", "Invalid port")

def test_connect_to_microcontroller_success(home_tab, mock_reacher, mocker):
    home_tab.microcontroller_menu.value = "COM1"
    mocker.patch.object(home_tab, "set_COM")
    mocker.patch.object(home_tab, "add_response")
    home_tab.connect_to_microcontroller(None)
    home_tab.set_COM.assert_called_once()
    mock_reacher.open_serial.assert_called_once()
    home_tab.add_response.assert_called_once_with("Opened serial connection")

def test_connect_to_microcontroller_failure(home_tab, mock_reacher, mocker):
    home_tab.microcontroller_menu.value = "COM1"
    mock_reacher.open_serial.side_effect = Exception("Connection failed")
    mocker.patch.object(home_tab, "set_COM")
    mocker.patch.object(home_tab, "add_error")
    home_tab.connect_to_microcontroller(None)
    home_tab.set_COM.assert_called_once()
    mock_reacher.open_serial.assert_called_once()
    home_tab.add_error.assert_called_once_with("Failed to connect to COM1", "Connection failed")

def test_disconnect_from_microcontroller_success(home_tab, mock_reacher, mocker):
    home_tab.microcontroller_menu.value = "COM1"
    mocker.patch.object(home_tab, "add_response")
    home_tab.disconnect_from_microcontroller(None)
    mock_reacher.close_serial.assert_called_once()
    home_tab.add_response.assert_called_once_with("Closed serial connection")

def test_disconnect_from_microcontroller_failure(home_tab, mock_reacher, mocker):
    home_tab.microcontroller_menu.value = "COM1"
    mock_reacher.close_serial.side_effect = Exception("Disconnect failed")
    mocker.patch.object(home_tab, "add_error")
    home_tab.disconnect_from_microcontroller(None)
    mock_reacher.close_serial.assert_called_once()
    home_tab.add_error.assert_called_once_with("Failed to disconnect from COM1", "Disconnect failed")

def test_reset(home_tab, mocker):
    home_tab.microcontroller_menu.options = ["COM1", "COM2"]
    mocker.patch.object(home_tab, "add_response")
    home_tab.reset()
    assert home_tab.microcontroller_menu.options == []
    home_tab.add_response.assert_called_once_with("Resetting home tab")

def test_layout(home_tab):
    layout = home_tab.layout()
    assert isinstance(layout, pn.Column)
    assert len(layout) == 1  # Only the microcontroller_layout
    microcontroller_layout = layout[0]
    assert isinstance(microcontroller_layout, pn.Column)
    assert len(microcontroller_layout) == 4  # Markdown, menu, search button, connect/disconnect row
    assert isinstance(microcontroller_layout[0], pn.pane.Markdown)
    assert microcontroller_layout[0].object == "### COM Connection"
    assert microcontroller_layout[1] == home_tab.microcontroller_menu
    assert microcontroller_layout[2] == home_tab.search_microcontrollers_button
    assert isinstance(microcontroller_layout[3], pn.Row)
    assert microcontroller_layout[3][0] == home_tab.serial_connect_button
    assert microcontroller_layout[3][1] == home_tab.serial_disconnect_button