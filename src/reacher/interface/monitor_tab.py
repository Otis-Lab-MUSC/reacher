from __future__ import annotations 
import matplotlib
from matplotlib import pyplot as plt
matplotlib.use('QtAgg') 
import panel as pn
import os
import sys
import time
import datetime
import pandas as pd
import plotly.graph_objects as go
import requests
from typing import Optional, Any
from reacher.kernel import REACHER
from reacher.interface import Dashboard

class MonitorTab(Dashboard):
    """A class to manage the Monitor tab UI for real-time experiment monitoring, inheriting from Dashboard."""

    def __init__(self, reacher: REACHER, program_tab: Any, hardware_tab: Any) -> None:
        from reacher.interface import ProgramTab, HardwareTab
        """Initialize the MonitorTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Sets up UI for starting, pausing, stopping, and monitoring experiments.
        - Displays real-time plots and summaries of behavioral data.

        **Note:**
        - Requires `program_tab` and `hardware_tab` to be set by a parent class (e.g., FullDashboard) for inter-tab dependencies.
        """
        super().__init__()
        self.reacher = reacher
        assets_dir = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(__file__), '../assets')
        self.img_path: str = os.path.join(assets_dir, 'mouse_still.jpg')
        self.gif_path: str = os.path.join(assets_dir, 'mouse.gif')
        self.animation_image: pn.pane.Image = pn.pane.Image(self.img_path, width=200)
        self.animation_markdown: pn.pane.Markdown = pn.pane.Markdown("`Waiting...`")
        self.df: pd.DataFrame = pd.DataFrame()
        self.plotly_pane: pn.pane.Plotly = pn.pane.Plotly(sizing_mode="stretch_width", height=600)
        self.summary_pane: pn.pane.DataFrame = pn.pane.DataFrame(index=False, max_rows=10, styles={"background-color": "#1e1e1e", "color": "white"})
        self.start_program_button: pn.widgets.Button = pn.widgets.Button(icon="player-play")
        self.start_program_button.on_click(self.start_program)
        self.pause_program_button: pn.widgets.Button = pn.widgets.Button(icon="player-pause")
        self.pause_program_button.on_click(self.pause_program)
        self.stop_program_button: pn.widgets.Button = pn.widgets.Button(icon="player-stop")
        self.stop_program_button.on_click(self.stop_program)
        self.download_button: pn.widgets.Button = pn.widgets.Button(name="Export data", icon="download")
        self.download_button.on_click(self.download)
        self.periodic_callback: Optional[Any] = None
        self.program_tab: ProgramTab = program_tab
        self.hardware_tab: HardwareTab = hardware_tab

    def fetch_data(self) -> pd.DataFrame:
        """Fetch behavioral data from the REACHER instance.

        **Description:**
        - Retrieves the latest behavioral data and converts it to a DataFrame.

        **Returns:**
        - `pd.DataFrame`: DataFrame containing behavioral data.
        """
        try:
            data = self.reacher.get_behavior_data()
            return pd.DataFrame(data)
        except requests.exceptions.RequestException as e:
            self.add_error("RequestException caught while attempting to fetch data", str(e))
        except Exception as e:
            self.add_error("Unexpected error fetching data", str(e))
        return pd.DataFrame()

    def update_summary_table(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate a summary table from behavioral data.

        **Description:**
        - Creates a summary of actions and components with their counts.

        **Args:**
        - `df (pd.DataFrame)`: The behavioral data DataFrame.

        **Returns:**
        - `pd.DataFrame`: Summary DataFrame with action and component counts.
        """
        if df.empty:
            self.add_response("No data available to summarize.")
            return pd.DataFrame(columns=["Action", "Component", "Count"])
        try:
            summary = df.groupby(["Action", "Component"]).size().reset_index(name="Count")
            return summary
        except KeyError as e:
            self.add_error("KeyError: Missing column(s) in DataFrame.", str(e))
            return pd.DataFrame(columns=["Action", "Component", "Count"])

    def generate_plotly_plot(self) -> go.Figure:
        """Generate a Plotly plot of the behavioral events.

        **Description:**
        - Creates a timeline visualization of behavioral events with color-coded actions.

        **Returns:**
        - `go.Figure`: The Plotly figure object.
        """
        if self.df.empty:
            fig = go.Figure()
            fig.add_annotation(text="No data available", showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
            return fig
        components = self.df['Component'].unique()
        y_positions = {component: i for i, component in enumerate(components)}
        colors = {
            'ACTIVE_PRESS': 'red',
            'TIMEOUT_PRESS': 'grey',
            'INACTIVE_PRESS': 'black',
            'LICK': 'pink',
            'INFUSION': 'red',
            'STIM': 'green'
        }
        fig = go.Figure(layout=dict(height=600))
        for _, row in self.df.iterrows():
            component = row['Component']
            action = row['Action']
            start = row['Start Timestamp']
            end = row['End Timestamp']
            y_pos = y_positions[component]
            fig.add_trace(go.Scatter(
                x=[start, end],
                y=[y_pos, y_pos],
                mode='lines+markers',
                line=dict(color=colors.get(action, 'blue'), width=2),
                marker=dict(symbol='line-ew-open', size=10),
                name=component
            ))
        fig.update_layout(
            title="Event Timeline",
            xaxis_title="Timestamp",
            yaxis=dict(
                title="Components",
                tickvals=list(y_positions.values()),
                ticktext=list(y_positions.keys())
            ),
            showlegend=False,
        )
        return fig

    def update_plot(self) -> None:
        """Update the plot and summary table based on current data.

        **Description:**
        - Refreshes the Plotly plot and summary table with the latest data.
        - Manages animation state based on program activity.
        """
        is_active = self.reacher.get_program_running()
        if not is_active:
            if self.periodic_callback:
                self.periodic_callback.stop()
                self.periodic_callback = None
            self.animation_image.object = self.img_path
            self.animation_markdown.object = """`Finished.`"""
            self.header.alert_type = "success"
            self.header.object = "Program finished."
            self.add_response("Program finished")
        new_data = self.fetch_data()
        if not new_data.empty:
            self.df = new_data
        self.plotly_pane.object = self.generate_plotly_plot()
        self.summary_pane.object = self.update_summary_table(new_data)

    def apply_preset(self) -> None:
        """Apply the selected program preset.

        **Description:**
        - Applies the currently selected preset from ProgramTab to the experiment.

        **Note:**
        - Requires `program_tab` to be set by a parent class.
        """
        if self.program_tab is None:
            self.add_error("ProgramTab not set", "Cannot apply preset without ProgramTab instance.")
            return
        self.preset_name = self.program_tab.presets_menubutton.value
        preset_action = self.program_tab.presets_dict.get(self.preset_name)
        if preset_action:
            preset_action()

    def start_program(self, _: Any) -> None:
        """Start the experimental program.

        **Description:**
        - Initiates the experiment, arms devices, and starts periodic updates.

        **Args:**
        - `_ (Any)`: Unused event argument.

        **Note:**
        - Requires `program_tab` and `hardware_tab` to be set by a parent class.
        """
        if self.program_tab is None or self.hardware_tab is None:
            self.add_error("Dependencies not set", "ProgramTab or HardwareTab not initialized.")
            return
        try:
            reacher_log_path = os.path.expanduser(r'~/REACHER/LOG')
            if os.path.exists(reacher_log_path):
                self.reacher.set_logging_stream_destination(reacher_log_path)
            else:
                os.makedirs(reacher_log_path, exist_ok=True)
                self.reacher.set_logging_stream_destination(reacher_log_path)
            if not self.reacher.ser.is_open:
                self.add_error("Serial port is not open", "Please connect to the microcontroller first.")
                return
            self.reacher.start_program()
            local_time = time.localtime()
            formatted_time = time.strftime("%Y-%m-%d, %H:%M:%S", local_time)
            self.add_response(f"Started program at {formatted_time}")
            if pn.state.curdoc:
                if self.periodic_callback is None:
                    self.periodic_callback = pn.state.add_periodic_callback(self.update_plot, period=5000)
            self.animation_image.object = self.gif_path
            self.apply_preset()
            self.hardware_tab.arm_devices(self.program_tab.get_hardware())
            self.animation_markdown.object = """`Running...`"""
            self.start_program_button.disabled = True
            self.header.alert_type = "warning"
            self.header.object = "WARNING: Program in progress..."
        except Exception as e:
            self.add_error("Failed to start program", str(e))

    def pause_program(self, _: Any) -> None:
        """Pause or resume the experimental program.

        **Description:**
        - Toggles between pausing and resuming the experiment, updating the UI accordingly.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            if self.reacher.program_flag.is_set():
                self.reacher.resume_program()
                self.animation_image.object = self.gif_path
                self.animation_markdown.object = """`Running...`"""
                self.pause_program_button.icon = "player-pause"
            else:
                self.reacher.pause_program()
                self.animation_image.object = self.img_path
                self.animation_markdown.object = """`Paused...`"""
                self.pause_program_button.icon = "player-play"
        except Exception as e:
            self.add_error("Failed to pause program", str(e))

    def stop_program(self, _: Any) -> None:
        """Stop the experimental program.

        **Description:**
        - Terminates the experiment and updates the UI to reflect completion.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.stop_program()
            local_time = time.localtime()
            formatted_time = time.strftime("%Y-%m-%d, %H:%M:%S", local_time)
            self.add_response(f"Ended program at {formatted_time}")
            self.animation_image.object = self.img_path
            if self.periodic_callback:
                self.periodic_callback.stop()
                self.periodic_callback = None
            self.animation_markdown.object = """`Finished.`"""
            self.header.alert_type = "success"
            self.header.object = "Program finished."
        except Exception as e:
            self.add_error("Failed to end program", str(e))

    def download(self, _: Any) -> None:
        """Download experiment data to files.

        **Description:**
        - Exports behavioral data, frame timestamps, and summaries to CSV files.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            start_time = datetime.datetime.fromtimestamp(self.reacher.get_start_time()).strftime('%H:%M:%S') if self.reacher.get_start_time() else "N/A"
            end_time = datetime.datetime.fromtimestamp(self.reacher.get_end_time()).strftime('%H:%M:%S') if self.reacher.get_end_time() else "N/A"
            arduino_configuration_summary = pd.Series(self.reacher.get_arduino_configuration())
            data = self.reacher.get_behavior_data()
            frames = self.reacher.get_frame_data()
            df = pd.DataFrame.from_records(data, columns=['Component', 'Action', 'Start Timestamp', 'End Timestamp'])
            series = pd.Series(frames)
            rh_active_data = df[(df['Component'] == 'RH_LEVER') & (df['Action'] == 'ACTIVE_PRESS')]
            rh_timeout_data = df[(df['Component'] == 'RH_LEVER') & (df['Action'] == 'TIMEOUT_PRESS')]
            rh_inactive_data = df[(df['Component'] == 'RH_LEVER') & (df['Action'] == 'INACTIVE_PRESS')]
            lh_active_data = df[(df['Component'] == 'LH_LEVER') & (df['Action'] == 'ACTIVE_PRESS')]
            lh_timeout_data = df[(df['Component'] == 'LH_LEVER') & (df['Action'] == 'TIMEOUT_PRESS')]
            lh_inactive_data = df[(df['Component'] == 'LH_LEVER') & (df['Action'] == 'INACTIVE_PRESS')]
            pump_data = df[df['Component'] == 'PUMP']
            lick_data = df[df['Component'] == 'LICK_CIRCUIT']
            laser_data = df[df['Component'] == 'LASER']
            summary_dict = {
                'Start Time': start_time,
                'End Time': end_time,
                'RH Active Presses': len(rh_active_data) if not rh_active_data.empty else 0,
                'RH Timeout Presses': len(rh_timeout_data) if not rh_timeout_data.empty else 0,
                'RH Inactive Presses': len(rh_inactive_data) if not rh_inactive_data.empty else 0,
                'LH Active Presses': len(lh_active_data) if not lh_active_data.empty else 0,
                'LH Timeout Presses': len(lh_timeout_data) if not lh_timeout_data.empty else 0,
                'LH Inactive Presses': len(lh_inactive_data) if not lh_inactive_data.empty else 0,
                'Infusions': len(pump_data[pump_data['Action'] == 'INFUSION']) if not pump_data.empty else 0,
                'Licks': len(lick_data[lick_data['Action'] == 'LICK']) if not lick_data.empty else 0,
                'Stims': len(laser_data[laser_data['Action'] == 'STIM']) if not laser_data.empty else 0,
                'Frames Collected': len(frames)
            }
            summary = pd.Series(summary_dict)
            destination = self.reacher.make_destination_folder()
            df.to_csv(os.path.join(destination, self.reacher.get_filename()))
            series.to_csv(os.path.join(destination, "frame-timestamps.csv"))
            summary.to_csv(os.path.join(destination, 'summary.csv'))
            arduino_configuration_summary.to_csv(os.path.join(destination, 'arduino-configuration.csv'))
            self.add_response(f"Data saved successfully at '{destination}'")
        except Exception as e:
            self.add_error("Failed to save data", str(e))

    def get_time(self) -> str:
        """Get the current time as a formatted string.

        **Description:**
        - Provides the current local time in a readable format.

        **Returns:**
        - `str`: The current time in 'YYYY-MM-DD_HH-MM-SS' format.
        """
        local_time = time.localtime()
        formatted_time = time.strftime("%Y-%m-%d_%H-%M-%S", local_time)
        return formatted_time

    def reset(self) -> None:
        """Reset the MonitorTab to its initial state.

        **Description:**
        - Clears data and resets UI elements to their default states.
        """
        self.add_response("Resetting monitor tab")
        self.df = pd.DataFrame(data=[])
        self.plotly_pane.object = None
        self.animation_image.object = self.img_path
        self.animation_markdown.object = """`Waiting...`"""

    def layout(self) -> pn.Column:
        """Construct the layout for the MonitorTab.

        **Description:**
        - Assembles the UI with program controls and real-time data visualization.

        **Returns:**
        - `pn.Column`: The MonitorTab layout.
        """
        program_control_area = pn.Column(
            pn.pane.Markdown("### Program Controls"),
            pn.Row(self.start_program_button, self.pause_program_button, self.stop_program_button, self.download_button)
        )
        plot_area = pn.Row(
            self.plotly_pane,
            pn.Column(
                pn.VSpacer(),
                self.animation_image,
                self.animation_markdown,
                pn.VSpacer(),
                self.summary_pane,
                width=250
            ),
            styles=dict(background="white")
        )
        return pn.Column(program_control_area, plot_area)

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """