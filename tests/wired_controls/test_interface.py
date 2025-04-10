import pytest
import panel as pn
from reacher.interface.interface import Interface
from reacher.interface.home_tab import HomeTab
from reacher.interface.program_tab import ProgramTab
from reacher.interface.hardware_tab import HardwareTab
from reacher.interface.monitor_tab import MonitorTab
from reacher.interface.schedule_tab import ScheduleTab
from reacher.kernel import REACHER
from unittest.mock import Mock, patch

# Fixture to mock REACHER
@pytest.fixture
def mock_reacher():
    return Mock(spec=REACHER)

# Fixture to initialize Interface with mocked REACHER
@pytest.fixture
def interface(mock_reacher):
    with patch("reacher.wired_controls.interface.REACHER", return_value=mock_reacher):
        return Interface()

def test_interface_init(interface, mock_reacher):
    """Test the initialization of the Interface class."""
    assert isinstance(interface, Interface)
    assert interface.reacher == mock_reacher
    assert isinstance(interface.home_tab, HomeTab)
    assert interface.home_tab.reacher == mock_reacher
    assert isinstance(interface.program_tab, ProgramTab)
    assert interface.program_tab.reacher == mock_reacher
    assert isinstance(interface.hardware_tab, HardwareTab)
    assert interface.hardware_tab.reacher == mock_reacher
    assert isinstance(interface.monitor_tab, MonitorTab)
    assert interface.monitor_tab.reacher == mock_reacher
    assert isinstance(interface.schedule_tab, ScheduleTab)
    assert interface.schedule_tab.reacher == mock_reacher
    # Check inherited attributes from Dashboard
    assert isinstance(interface.header, pn.pane.Alert)
    assert isinstance(interface.response_textarea, pn.pane.HTML)
    assert isinstance(interface.toggle_button, pn.widgets.Button)
    assert isinstance(interface.reset_button, pn.widgets.Button)

def test_dashboard_layout(interface):
    """Test the layout of the dashboard in Interface."""
    dashboard = interface.dashboard
    assert isinstance(dashboard, pn.Tabs)
    assert dashboard.tabs_location == "left"
    assert len(dashboard) == 5  # Five tabs: Home, Program, Hardware, Monitor, Schedule
    
    # Extract tab names using dashboard._names
    tab_names = dashboard._names
    assert tab_names == ["Home", "Program", "Hardware", "Monitor", "Schedule"]
    
    # Verify each tab's content type and basic structure
    assert isinstance(dashboard[0], pn.Column)  # Home tab
    assert isinstance(dashboard[1], pn.Column)  # Program tab
    assert isinstance(dashboard[2], pn.Row)     # Hardware tab (from previous test)
    assert isinstance(dashboard[3], pn.Column)  # Monitor tab
    assert isinstance(dashboard[4], pn.Row)     # Schedule tab (from previous test)

def test_tab_instances_sharing_reacher(interface, mock_reacher):
    """Test that all tab instances share the same REACHER instance."""
    assert interface.home_tab.reacher is interface.reacher
    assert interface.program_tab.reacher is interface.reacher
    assert interface.hardware_tab.reacher is interface.reacher
    assert interface.monitor_tab.reacher is interface.reacher
    assert interface.schedule_tab.reacher is interface.reacher