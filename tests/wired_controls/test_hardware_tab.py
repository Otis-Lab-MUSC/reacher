import pytest
import panel as pn
import matplotlib.pyplot as plt
import numpy as np
from reacher.interface.hardware_tab import HardwareTab
from unittest.mock import Mock, patch

@pytest.fixture
def mock_reacher():
    reacher = Mock()
    reacher.send_serial_command = Mock()
    return reacher

@pytest.fixture
def hardware_tab(mock_reacher):
    return HardwareTab(reacher=mock_reacher)

def test_hardware_tab_init(hardware_tab):
    assert isinstance(hardware_tab, HardwareTab)
    assert isinstance(hardware_tab.active_lever_button, pn.widgets.MenuButton)
    assert isinstance(hardware_tab.arm_rh_lever_button, pn.widgets.Toggle)
    assert hardware_tab.rh_lever_armed is False
    assert isinstance(hardware_tab.arm_lh_lever_button, pn.widgets.Toggle)
    assert hardware_tab.lh_lever_armed is False
    assert isinstance(hardware_tab.arm_cue_button, pn.widgets.Toggle)
    assert hardware_tab.cue_armed is False
    assert isinstance(hardware_tab.send_cue_configuration_button, pn.widgets.Button)
    assert isinstance(hardware_tab.cue_frequency_intslider, pn.widgets.IntInput)
    assert isinstance(hardware_tab.cue_duration_intslider, pn.widgets.IntInput)
    assert isinstance(hardware_tab.arm_pump_button, pn.widgets.Toggle)
    assert hardware_tab.pump_armed is False
    assert isinstance(hardware_tab.arm_lick_circuit_button, pn.widgets.Toggle)
    assert hardware_tab.lick_circuit_armed is False
    assert isinstance(hardware_tab.arm_microscope_button, pn.widgets.Toggle)
    assert hardware_tab.microscope_armed is False
    assert isinstance(hardware_tab.arm_laser_button, pn.widgets.Toggle)
    assert hardware_tab.laser_armed is False
    assert isinstance(hardware_tab.stim_mode_widget, pn.widgets.Select)
    assert isinstance(hardware_tab.stim_frequency_slider, pn.widgets.IntInput)
    assert isinstance(hardware_tab.stim_duration_slider, pn.widgets.IntInput)
    assert isinstance(hardware_tab.send_laser_config_button, pn.widgets.Button)
    assert isinstance(hardware_tab.header, pn.pane.Alert)
    assert isinstance(hardware_tab.response_textarea, pn.pane.HTML)

def test_set_active_lever(hardware_tab, mock_reacher):
    hardware_tab.set_active_lever(Mock(new="LH Lever"))
    mock_reacher.send_serial_command.assert_called_once_with("ACTIVE_LEVER_LH")
    mock_reacher.send_serial_command.reset_mock()
    hardware_tab.set_active_lever(Mock(new="RH Lever"))
    mock_reacher.send_serial_command.assert_called_once_with("ACTIVE_LEVER_RH")

def test_arm_rh_lever(hardware_tab, mock_reacher, mocker):
    mocker.patch.object(hardware_tab, "add_error")
    # Set initial state and call directly, avoiding double trigger from param.watch
    hardware_tab.rh_lever_armed = False
    hardware_tab.arm_rh_lever_button.value = False  # Reset to avoid watch trigger
    hardware_tab.arm_rh_lever(None)
    assert hardware_tab.rh_lever_armed is True
    assert hardware_tab.arm_rh_lever_button.icon == "unlock"
    mock_reacher.send_serial_command.assert_called_once_with("ARM_LEVER_RH")
    mock_reacher.send_serial_command.reset_mock()
    hardware_tab.arm_rh_lever(None)
    assert hardware_tab.rh_lever_armed is False
    assert hardware_tab.arm_rh_lever_button.icon == "lock"
    mock_reacher.send_serial_command.assert_called_once_with("DISARM_LEVER_RH")

def test_arm_rh_lever_failure(hardware_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(hardware_tab, "add_error")
    hardware_tab.rh_lever_armed = False
    hardware_tab.arm_rh_lever_button.value = False  # Reset to avoid watch trigger
    hardware_tab.arm_rh_lever(None)
    mock_reacher.send_serial_command.assert_called_once_with("ARM_LEVER_RH")
    hardware_tab.add_error.assert_called_once_with("Serial error", "Serial error")

def test_send_cue_configuration(hardware_tab, mock_reacher):
    hardware_tab.cue_frequency_intslider.value = 1000
    hardware_tab.cue_duration_intslider.value = 2000
    hardware_tab.send_cue_configuration(None)
    mock_reacher.send_serial_command.assert_any_call("SET_FREQUENCY_CS:1000")
    mock_reacher.send_serial_command.assert_any_call("SET_DURATION_CS:2000")
    assert mock_reacher.send_serial_command.call_count == 2

def test_send_laser_configuration_success(hardware_tab, mock_reacher):
    hardware_tab.stim_mode_widget.value = "Cycle"
    hardware_tab.stim_duration_slider.value = 10
    hardware_tab.stim_frequency_slider.value = 50
    hardware_tab.send_laser_configuration(None)
    mock_reacher.send_serial_command.assert_any_call("LASER_STIM_MODE_CYCLE")
    mock_reacher.send_serial_command.assert_any_call("LASER_DURATION:10")
    mock_reacher.send_serial_command.assert_any_call("LASER_FREQUENCY:50")
    assert mock_reacher.send_serial_command.call_count == 3

def test_send_laser_configuration_failure(hardware_tab, mock_reacher, mocker):
    mock_reacher.send_serial_command.side_effect = Exception("Serial error")
    mocker.patch.object(hardware_tab, "add_error")
    hardware_tab.send_laser_configuration(None)
    mock_reacher.send_serial_command.assert_called()
    hardware_tab.add_error.assert_called_once_with("Failed to send laser configuration", "Serial error")

def test_plot_square_wave(hardware_tab):
    fig = hardware_tab.plot_square_wave(20)
    assert isinstance(fig, plt.Figure)
    assert fig.axes[0].get_title() == "Square Wave - 20 Hz"
    plt.close(fig)

def test_arm_devices(hardware_tab, mock_reacher, mocker):
    # Mock the callable in hardware_components instead of the method directly
    mocker.patch.dict(hardware_tab.hardware_components, {
        "LH Lever": mocker.MagicMock(),
        "RH Lever": mocker.MagicMock()
    })
    hardware_tab.arm_devices(["LH Lever", "RH Lever"])
    hardware_tab.hardware_components["LH Lever"].assert_called_once_with(None)
    hardware_tab.hardware_components["RH Lever"].assert_called_once_with(None)

def test_layout(hardware_tab):
    layout = hardware_tab.layout()
    assert isinstance(layout, pn.Row)
    assert len(layout) == 3  # levers/cue/reward column, spacer, opto column
    left_column = layout[0]
    assert isinstance(left_column, pn.Column)
    assert left_column.objects[0].objects[0].object == "### Levers"  # Access nested Markdown in levers_area
    assert isinstance(layout[1], pn.Spacer)
    opto_area = layout[2]
    assert isinstance(opto_area, pn.Column)
    assert opto_area.objects[0].object == "### Scope"  # Direct access to Markdown in opto_area