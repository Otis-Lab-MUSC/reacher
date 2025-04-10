import pytest
import panel as pn
from reacher.interface.schedule_tab import ScheduleTab
from unittest.mock import Mock, patch

# Fixture to mock REACHER
@pytest.fixture
def mock_reacher():
    reacher = Mock()
    reacher.send_serial_command = Mock()
    return reacher

# Fixture to initialize ScheduleTab with mocked REACHER
@pytest.fixture
def schedule_tab(mock_reacher):
    return ScheduleTab(reacher=mock_reacher)

def test_schedule_tab_init(schedule_tab):
    assert isinstance(schedule_tab, ScheduleTab)
    assert isinstance(schedule_tab.timeout_intslider, pn.widgets.IntSlider)
    assert schedule_tab.timeout_intslider.value == 20
    assert isinstance(schedule_tab.send_timeout_button, pn.widgets.Button)
    assert isinstance(schedule_tab.trace_intslider, pn.widgets.IntSlider)
    assert schedule_tab.trace_intslider.value == 0
    assert isinstance(schedule_tab.send_trace_button, pn.widgets.Button)
    assert isinstance(schedule_tab.fixed_ratio_intslider, pn.widgets.IntSlider)
    assert schedule_tab.fixed_ratio_intslider.value == 1
    assert isinstance(schedule_tab.send_fixed_ratio_button, pn.widgets.Button)
    assert isinstance(schedule_tab.progressive_ratio_intslider, pn.widgets.IntSlider)
    assert schedule_tab.progressive_ratio_intslider.value == 2
    assert isinstance(schedule_tab.send_progressive_ratio_button, pn.widgets.Button)
    assert isinstance(schedule_tab.variable_interval_intslider, pn.widgets.IntSlider)
    assert schedule_tab.variable_interval_intslider.value == 15
    assert isinstance(schedule_tab.send_variable_interval_button, pn.widgets.Button)
    assert isinstance(schedule_tab.omission_interval_intslider, pn.widgets.IntSlider)
    assert schedule_tab.omission_interval_intslider.value == 20
    assert isinstance(schedule_tab.send_omission_interval_button, pn.widgets.Button)
    # Check inherited attributes from Dashboard
    assert isinstance(schedule_tab.header, pn.pane.Alert)
    assert isinstance(schedule_tab.response_textarea, pn.pane.HTML)
    assert isinstance(schedule_tab.toggle_button, pn.widgets.Button)
    assert isinstance(schedule_tab.reset_button, pn.widgets.Button)
    assert schedule_tab.dashboard is None

def test_send_timeout_success(schedule_tab, mock_reacher, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.timeout_intslider.value = 30
    schedule_tab.send_timeout(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_TIMEOUT_PERIOD_LENGTH:30000")
    schedule_tab.add_response.assert_called_once_with("Set timeout period to 30000")

def test_send_timeout_failure(schedule_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(schedule_tab, "add_error")
    schedule_tab.timeout_intslider.value = 30
    schedule_tab.send_timeout(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_TIMEOUT_PERIOD_LENGTH:30000")
    schedule_tab.add_error.assert_called_once_with("Failed to send timeout interval", "Serial error")

def test_send_trace_success(schedule_tab, mock_reacher, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.trace_intslider.value = 5
    schedule_tab.send_trace(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_TRACE_INTERVAL:5000")
    schedule_tab.add_response.assert_called_once_with("Set trace interval to 5000")

def test_send_trace_failure(schedule_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(schedule_tab, "add_error")
    schedule_tab.trace_intslider.value = 5
    schedule_tab.send_trace(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_TRACE_INTERVAL:5000")
    schedule_tab.add_error.assert_called_once_with("Failed to send trace interval", "Serial error")

def test_send_fixed_ratio_success(schedule_tab, mock_reacher, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.fixed_ratio_intslider.value = 10
    schedule_tab.send_fixed_ratio(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_RATIO:10")
    schedule_tab.add_response.assert_called_once_with("Set fixed ratio to 10")

def test_send_fixed_ratio_failure(schedule_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(schedule_tab, "add_error")
    schedule_tab.fixed_ratio_intslider.value = 10
    schedule_tab.send_fixed_ratio(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_RATIO:10")
    schedule_tab.add_error.assert_called_once_with("Failed to send fixed ratio interval", "Serial error")

def test_send_progressive_ratio_success(schedule_tab, mock_reacher, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.progressive_ratio_intslider.value = 5
    schedule_tab.send_progressive_ratio(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_RATIO:5")
    schedule_tab.add_response.assert_called_once_with("Set progressive ratio to 5")

def test_send_progressive_ratio_failure(schedule_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(schedule_tab, "add_error")
    schedule_tab.progressive_ratio_intslider.value = 5
    schedule_tab.send_progressive_ratio(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_RATIO:5")
    schedule_tab.add_error.assert_called_once_with("Failed to send progressive ratio interval", "Serial error")

def test_send_variable_interval_success(schedule_tab, mock_reacher, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.variable_interval_intslider.value = 25
    schedule_tab.send_variable_interval(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_VARIABLE_INTERVAL:25")
    schedule_tab.add_response.assert_called_once_with("Set variable interval to 25")

def test_send_variable_interval_failure(schedule_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(schedule_tab, "add_error")
    schedule_tab.variable_interval_intslider.value = 25
    schedule_tab.send_variable_interval(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_VARIABLE_INTERVAL:25")
    schedule_tab.add_error.assert_called_once_with("Failed to send variable interval", "Serial error")

def test_send_omission_interval_success(schedule_tab, mock_reacher, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.omission_interval_intslider.value = 30
    schedule_tab.send_omission_interval(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_OMISSION_INTERVAL:30000")
    schedule_tab.add_response.assert_called_once_with("Set omission interval to 30000")

def test_send_omission_interval_failure(schedule_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(schedule_tab, "add_error")
    schedule_tab.omission_interval_intslider.value = 30
    schedule_tab.send_omission_interval(None)
    mock_reacher.send_serial_command.assert_called_once_with("SET_OMISSION_INTERVAL:30000")
    schedule_tab.add_error.assert_called_once_with("Failed to send omission interval", "Serial error")

def test_reset(schedule_tab, mocker):
    mocker.patch.object(schedule_tab, "add_response")
    schedule_tab.timeout_intslider.value = 50
    schedule_tab.trace_intslider.value = 10
    schedule_tab.fixed_ratio_intslider.value = 5
    schedule_tab.progressive_ratio_intslider.value = 10
    schedule_tab.variable_interval_intslider.value = 30
    schedule_tab.omission_interval_intslider.value = 40
    schedule_tab.reset()
    assert schedule_tab.timeout_intslider.value == 20
    assert schedule_tab.trace_intslider.value == 0
    assert schedule_tab.fixed_ratio_intslider.value == 1
    assert schedule_tab.progressive_ratio_intslider.value == 2
    assert schedule_tab.variable_interval_intslider.value == 15
    assert schedule_tab.omission_interval_intslider.value == 20
    schedule_tab.add_response.assert_called_once_with("Resetting schedule tab")

def test_layout(schedule_tab):
    layout = schedule_tab.layout()
    assert isinstance(layout, pn.Row)
    assert len(layout) == 3  # within-trial area, spacer, training schedule area
    within_trial_area = layout[0]
    assert isinstance(within_trial_area, pn.Column)
    assert within_trial_area[0].object == "### Within-Trial Dynamics"
    assert isinstance(within_trial_area[1], pn.Row)
    assert within_trial_area[1][0] == schedule_tab.timeout_intslider
    assert within_trial_area[1][1] == schedule_tab.send_timeout_button
    assert isinstance(within_trial_area[2], pn.Row)
    assert within_trial_area[2][0] == schedule_tab.trace_intslider
    assert within_trial_area[2][1] == schedule_tab.send_trace_button
    assert isinstance(layout[1], pn.Spacer)
    training_schedule_area = layout[2]
    assert isinstance(training_schedule_area, pn.Column)
    assert training_schedule_area[0].object == "### Training Schedule"
    assert isinstance(training_schedule_area[1], pn.Row)
    assert training_schedule_area[1][0] == schedule_tab.fixed_ratio_intslider
    assert training_schedule_area[1][1] == schedule_tab.send_fixed_ratio_button
    assert isinstance(training_schedule_area[2], pn.Row)
    assert training_schedule_area[2][0] == schedule_tab.progressive_ratio_intslider
    assert training_schedule_area[2][1] == schedule_tab.send_progressive_ratio_button
    assert isinstance(training_schedule_area[3], pn.Row)
    assert training_schedule_area[3][0] == schedule_tab.variable_interval_intslider
    assert training_schedule_area[3][1] == schedule_tab.send_variable_interval_button
    assert isinstance(training_schedule_area[4], pn.Row)
    assert training_schedule_area[4][0] == schedule_tab.omission_interval_intslider
    assert training_schedule_area[4][1] == schedule_tab.send_omission_interval_button