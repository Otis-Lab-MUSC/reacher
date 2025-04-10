import pytest
import panel as pn
import pandas as pd
import plotly.graph_objects as go
from reacher.interface.monitor_tab import MonitorTab
from unittest.mock import Mock, patch

@pytest.fixture
def mock_reacher():
    reacher = Mock()
    reacher.get_behavior_data = Mock(return_value=[{"Component": "LH_LEVER", "Action": "ACTIVE_PRESS", "Start Timestamp": 1, "End Timestamp": 2}])
    reacher.get_program_running = Mock(return_value=False)
    reacher.start_program = Mock()
    reacher.pause_program = Mock()
    reacher.resume_program = Mock()
    reacher.stop_program = Mock()
    reacher.get_frame_data = Mock(return_value=[1, 2, 3])
    reacher.get_start_time = Mock(return_value=1609459200)  # 2021-01-01 00:00:00
    reacher.get_end_time = Mock(return_value=1609459260)  # 2021-01-01 00:01:00
    reacher.get_arduino_configuration = Mock(return_value={"config": "test"})
    reacher.make_destination_folder = Mock(return_value="/tmp/test")
    reacher.get_filename = Mock(return_value="test.csv")
    reacher.set_logging_stream_destination = Mock()
    reacher.program_flag = Mock(is_set=Mock(return_value=True))
    reacher.ser = Mock(is_open=True)
    return reacher

@pytest.fixture
def monitor_tab(mock_reacher):
    return MonitorTab(reacher=mock_reacher)

def test_monitor_tab_init(monitor_tab):
    assert isinstance(monitor_tab, MonitorTab)
    assert isinstance(monitor_tab.animation_image, pn.pane.Image)
    assert isinstance(monitor_tab.animation_markdown, pn.pane.Markdown)
    assert isinstance(monitor_tab.df, pd.DataFrame)
    assert monitor_tab.df.empty
    assert isinstance(monitor_tab.plotly_pane, pn.pane.Plotly)
    assert isinstance(monitor_tab.summary_pane, pn.pane.DataFrame)
    assert isinstance(monitor_tab.start_program_button, pn.widgets.Button)
    assert isinstance(monitor_tab.pause_program_button, pn.widgets.Button)
    assert isinstance(monitor_tab.stop_program_button, pn.widgets.Button)
    assert isinstance(monitor_tab.download_button, pn.widgets.Button)
    assert monitor_tab.periodic_callback is None
    assert monitor_tab.program_tab is None
    assert monitor_tab.hardware_tab is None
    assert isinstance(monitor_tab.header, pn.pane.Alert)

def test_fetch_data_success(monitor_tab, mock_reacher):
    df = monitor_tab.fetch_data()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["Component"] == "LH_LEVER"

def test_fetch_data_failure(monitor_tab, mock_reacher, mocker):
    mock_reacher.get_behavior_data.side_effect = Exception("Data error")
    mocker.patch.object(monitor_tab, "add_error")
    df = monitor_tab.fetch_data()
    assert df.empty
    monitor_tab.add_error.assert_called_once_with("Unexpected error fetching data", "Data error")

def test_update_summary_table(monitor_tab):
    df = pd.DataFrame([{"Action": "ACTIVE_PRESS", "Component": "LH_LEVER"}])
    summary = monitor_tab.update_summary_table(df)
    assert len(summary) == 1
    assert summary.iloc[0]["Count"] == 1

def test_generate_plotly_plot_with_data(monitor_tab):
    monitor_tab.df = pd.DataFrame([{"Component": "LH_LEVER", "Action": "ACTIVE_PRESS", "Start Timestamp": 1, "End Timestamp": 2}])
    fig = monitor_tab.generate_plotly_plot()
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert fig.data[0].line.color == "red"

def test_generate_plotly_plot_empty(monitor_tab):
    monitor_tab.df = pd.DataFrame()
    fig = monitor_tab.generate_plotly_plot()
    assert isinstance