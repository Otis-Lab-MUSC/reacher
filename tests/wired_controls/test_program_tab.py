import pytest
import panel as pn
from reacher.interface.program_tab import ProgramTab
from unittest.mock import Mock, patch

# Fixture to mock REACHER
@pytest.fixture
def mock_reacher():
    reacher = Mock()
    reacher.set_limit_type = Mock()
    reacher.set_infusion_limit = Mock()
    reacher.set_time_limit = Mock()
    reacher.set_stop_delay = Mock()
    reacher.set_filename = Mock()
    reacher.set_data_destination = Mock()
    return reacher

# Fixture to initialize ProgramTab with mocked REACHER
@pytest.fixture
def program_tab(mock_reacher):
    return ProgramTab(reacher=mock_reacher)

def test_program_tab_init(program_tab):
    assert isinstance(program_tab, ProgramTab)
    assert isinstance(program_tab.hardware_checkbuttongroup, pn.widgets.CheckButtonGroup)
    assert program_tab.hardware_checkbuttongroup.options == ["LH Lever", "RH Lever", "Cue", "Pump", "Lick Circuit", "Laser", "Imaging Microscope"]
    assert program_tab.hardware_checkbuttongroup.value == ["LH Lever", "RH Lever", "Cue", "Pump"]
    assert isinstance(program_tab.presets_menubutton, pn.widgets.Select)
    assert set(program_tab.presets_menubutton.options) == {"Custom", "SA High", "SA Mid", "SA Low", "SA Extinction"}
    assert isinstance(program_tab.limit_type_radiobutton, pn.widgets.RadioButtonGroup)
    assert program_tab.limit_type_radiobutton.options == ["Time", "Infusion", "Both"]
    assert isinstance(program_tab.time_limit_hour, pn.widgets.IntInput)
    assert isinstance(program_tab.time_limit_min, pn.widgets.IntInput)
    assert isinstance(program_tab.time_limit_sec, pn.widgets.IntInput)
    assert isinstance(program_tab.infusion_limit_intslider, pn.widgets.IntInput)
    assert isinstance(program_tab.stop_delay_intslider, pn.widgets.IntInput)
    assert isinstance(program_tab.set_program_limit_button, pn.widgets.Button)
    assert isinstance(program_tab.filename_textinput, pn.widgets.TextInput)
    assert isinstance(program_tab.file_destination_textinput, pn.widgets.TextInput)
    assert isinstance(program_tab.set_file_config_button, pn.widgets.Button)
    # Check inherited attributes from Dashboard
    assert isinstance(program_tab.header, pn.pane.Alert)
    assert isinstance(program_tab.response_textarea, pn.pane.HTML)
    assert isinstance(program_tab.toggle_button, pn.widgets.Button)
    assert isinstance(program_tab.reset_button, pn.widgets.Button)
    assert program_tab.dashboard is None

def test_set_preset(program_tab):
    program_tab.set_preset("Both", 10, 3665, 5)  # 1hr 1min 5s
    assert program_tab.limit_type_radiobutton.value == "Both"
    assert program_tab.infusion_limit_intslider.value == 10
    assert program_tab.time_limit_hour.value == 1
    assert program_tab.time_limit_min.value == 1
    assert program_tab.time_limit_sec.value == 5
    assert program_tab.stop_delay_intslider.value == 5

def test_set_program_limit_custom_success(program_tab, mock_reacher, mocker):
    mocker.patch.object(program_tab, "add_response")
    program_tab.presets_menubutton.value = "Custom"
    program_tab.limit_type_radiobutton.value = "Both"
    program_tab.infusion_limit_intslider.value = 15
    program_tab.time_limit_hour.value = 1
    program_tab.time_limit_min.value = 30
    program_tab.time_limit_sec.value = 45
    program_tab.stop_delay_intslider.value = 10
    program_tab.set_program_limit(None)
    mock_reacher.set_limit_type.assert_called_once_with("Both")
    mock_reacher.set_infusion_limit.assert_called_once_with(15)
    mock_reacher.set_time_limit.assert_called_once_with(5445)  # 1*3600 + 30*60 + 45
    mock_reacher.set_stop_delay.assert_called_once_with(10)
    program_tab.add_response.assert_any_call("Set limit type to Both")
    program_tab.add_response.assert_any_call("Set infusion limit to 15")
    program_tab.add_response.assert_any_call("Set time limit to 5445")
    program_tab.add_response.assert_any_call("Set stop delay to 10")

def test_set_program_limit_preset_success(program_tab, mock_reacher, mocker):
    mocker.patch.object(program_tab, "add_response")
    program_tab.presets_menubutton.value = "SA High"
    program_tab.set_program_limit(None)
    mock_reacher.set_limit_type.assert_called_once_with("Both")
    mock_reacher.set_infusion_limit.assert_called_once_with(10)
    mock_reacher.set_time_limit.assert_called_once_with(3600)
    mock_reacher.set_stop_delay.assert_called_once_with(10)
    program_tab.add_response.assert_any_call("Set limit type to Both")
    program_tab.add_response.assert_any_call("Set infusion limit to 10")
    program_tab.add_response.assert_any_call("Set time limit to 3600")
    program_tab.add_response.assert_any_call("Set stop delay to 10")

def test_set_program_limit_failure(program_tab, mock_reacher, mocker):
    mock_reacher.set_limit_type.side_effect = Exception("Limit error")
    mocker.patch.object(program_tab, "add_error")
    program_tab.presets_menubutton.value = "Custom"
    program_tab.set_program_limit(None)
    mock_reacher.set_limit_type.assert_called_once()
    program_tab.add_error.assert_called_once_with("Failed to set program limit", "Limit error")

def test_format_time(program_tab):
    assert program_tab.format_time(1, 30, 45) == "1hr 30min 45s"
    assert program_tab.format_time(0, 90, 0) == "1hr 30min 0s"  # 90 minutes = 1hr 30min
    assert program_tab.format_time(0, 0, 5) == "0hr 0min 5s"

def test_get_hardware(program_tab):
    program_tab.hardware_checkbuttongroup.value = ["LH Lever", "Cue"]
    assert program_tab.get_hardware() == ["LH Lever", "Cue"]

def test_set_file_configuration_success(program_tab, mock_reacher, mocker):
    mocker.patch.object(program_tab, "add_response")
    program_tab.filename_textinput.value = "test.csv"
    program_tab.file_destination_textinput.value = "~/data"
    program_tab.set_file_configuration(None)
    mock_reacher.set_filename.assert_called_once_with("test.csv")
    mock_reacher.set_data_destination.assert_called_once_with("~/data")
    program_tab.add_response.assert_any_call("Set filename to test.csv")
    program_tab.add_response.assert_any_call("Set data destination to ~/data")

def test_set_file_configuration_failure(program_tab, mock_reacher, mocker):
    mock_reacher.set_filename.side_effect = Exception("File error")
    mocker.patch.object(program_tab, "add_error")
    mocker.patch.object(program_tab, "add_response")
    program_tab.filename_textinput.value = "test.csv"
    program_tab.file_destination_textinput.value = "~/data"
    program_tab.set_file_configuration(None)
    mock_reacher.set_filename.assert_called_once_with("test.csv")
    program_tab.add_error.assert_called_once_with("Failed to set file name", "File error")
    mock_reacher.set_data_destination.assert_called_once_with("~/data")
    program_tab.add_response.assert_called_once_with("Set data destination to ~/data")

def test_reset(program_tab, mocker):
    mocker.patch.object(program_tab, "add_response")
    program_tab.hardware_checkbuttongroup.value = ["Cue"]
    program_tab.limit_type_radiobutton.value = "Time"
    program_tab.time_limit_hour.value = 2
    program_tab.time_limit_min.value = 15
    program_tab.time_limit_sec.value = 30
    program_tab.infusion_limit_intslider.value = 5
    program_tab.reset()
    assert program_tab.hardware_checkbuttongroup.value == ["LH Lever", "RH Lever", "Cue", "Pump"]
    assert program_tab.limit_type_radiobutton.value is None
    assert program_tab.time_limit_hour.value == 0
    assert program_tab.time_limit_min.value == 0
    assert program_tab.time_limit_sec.value == 0
    assert program_tab.infusion_limit_intslider.value == 0
    program_tab.add_response.assert_called_once_with("Resetting program tab")

def test_layout(program_tab):
    layout = program_tab.layout()
    assert isinstance(layout, pn.Column)
    assert len(layout) == 5  # presets row, spacer, components/limits row, spacer, file config
    assert isinstance(layout[0], pn.Row)
    assert layout[0][0] == program_tab.presets_menubutton
    assert layout[0][1] == program_tab.set_program_limit_button
    assert isinstance(layout[1], pn.Spacer)
    assert isinstance(layout[2], pn.Row)
    components_area = layout[2][0]
    assert isinstance(components_area, pn.Column)
    assert components_area[0].object == "### Components"
    assert components_area[1] == program_tab.hardware_checkbuttongroup
    limits_area = layout[2][2]
    assert isinstance(limits_area, pn.Column)
    assert limits_area[0].object == "### Limits"
    assert limits_area[1] == program_tab.limit_type_radiobutton
    assert limits_area[2] == program_tab.time_limit_area
    assert limits_area[3] == program_tab.infusion_limit_intslider
    assert limits_area[4] == program_tab.stop_delay_intslider
    assert isinstance(layout[3], pn.Spacer)
    file_config_area = layout[4]
    assert isinstance(file_config_area, pn.Column)
    assert file_config_area[0].object == "### File Configuration"
    assert file_config_area[1] == program_tab.filename_textinput
    assert file_config_area[2] == program_tab.file_destination_textinput
    assert file_config_area[3] == program_tab.set_file_config_button