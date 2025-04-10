import pytest
import panel as pn
import time
from reacher.interface.dashboard import Dashboard  # Adjust if path differs
from unittest.mock import Mock, patch

# Fixture to mock REACHER
@pytest.fixture
def mock_reacher():
    reacher = Mock()
    reacher.reset = Mock()
    return reacher

# Fixture to initialize Dashboard with mocked REACHER
@pytest.fixture
def dashboard(mock_reacher):
    # Pass mock_reacher directly to Dashboard instead of patching REACHER
    return Dashboard(reacher=mock_reacher)

def test_dashboard_init(dashboard):
    assert isinstance(dashboard.header, pn.pane.Alert)
    assert dashboard.header.object == "Program not started..."
    assert dashboard.header.alert_type == "info"
    assert isinstance(dashboard.response_textarea, pn.pane.HTML)
    assert "REACHER Output:" in dashboard.response_textarea.object
    assert dashboard.response_textarea.visible is True
    assert isinstance(dashboard.toggle_button, pn.widgets.Button)
    assert dashboard.toggle_button.name == "Hide Response"
    assert isinstance(dashboard.reset_button, pn.widgets.Button)
    assert dashboard.reset_button.name == "Reset"
    assert dashboard.dashboard is None

def test_layout_without_dashboard(dashboard):
    with pytest.raises(ValueError, match="Dashboard tabs must be initialized before calling layout."):
        dashboard.layout()

def test_layout_with_dashboard(dashboard, mocker):
    mock_tabs = mocker.MagicMock(spec=pn.Tabs)
    dashboard.dashboard = mock_tabs
    layout = dashboard.layout()
    assert isinstance(layout, pn.Column)
    assert len(layout) == 3  # header_row, main_row, reset_button
    header_row = layout[0]
    assert isinstance(header_row, pn.Row)
    assert header_row[0] == dashboard.header
    assert header_row[1] == dashboard.toggle_button
    main_row = layout[1]
    assert isinstance(main_row, pn.Row)
    assert main_row[0] == mock_tabs
    assert main_row[1] == dashboard.response_textarea
    assert layout[2] == dashboard.reset_button

def test_get_response_terminal(dashboard):
    terminal = dashboard.get_response_terminal()
    assert terminal == dashboard.response_textarea
    assert isinstance(terminal, pn.pane.HTML)

def test_add_response(dashboard, mocker):
    mocker.patch("time.time", return_value=1609459200)  # 2021-01-01 00:00:00 UTC
    mocker.patch("time.localtime", return_value=time.gmtime(1609459200))
    dashboard.add_response("Test message")
    output = dashboard.response_textarea.object
    assert ">>>" in output
    assert "[00:00:00]" in output
    assert "Test message" in output
    assert "color: cyan" in output
    assert "<br>" in output

def test_add_error(dashboard, mocker):
    mocker.patch("time.time", return_value=1609459200)  # 2021-01-01 00:00:00 UTC
    mocker.patch("time.localtime", return_value=time.gmtime(1609459200))
    dashboard.add_error("Error occurred", "Details here")
    output = dashboard.response_textarea.object
    assert ">>>" in output
    assert "[00:00:00]" in output
    assert "!!!ERROR!!!" in output
    assert "color: red" in output
    assert "<br>" in output

def test_toggle_response_visibility(dashboard):
    assert dashboard.response_textarea.visible is True
    assert dashboard.toggle_button.name == "Hide Response"
    
    dashboard.toggle_response_visibility(None)
    assert dashboard.response_textarea.visible is False
    assert dashboard.toggle_button.name == "Show Response"
    
    dashboard.toggle_response_visibility(None)
    assert dashboard.response_textarea.visible is True
    assert dashboard.toggle_button.name == "Hide Response"

def test_reset_session_success(dashboard, mock_reacher, mocker):
    mocker.patch.object(dashboard, "add_response")
    dashboard.reset_session(None)
    mock_reacher.reset.assert_called_once()
    dashboard.add_response.assert_called_once_with("Session reset.")

def test_reset_session_failure(dashboard, mock_reacher, mocker):
    mock_reacher.reset.side_effect = Exception("Reset failed")
    mocker.patch.object(dashboard, "add_error")
    dashboard.reset_session(None)
    mock_reacher.reset.assert_called_once()
    dashboard.add_error.assert_called_once_with("Failed to reset session.", "Reset failed")