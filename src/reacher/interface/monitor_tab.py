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

    def __init__(self, reacher: REACHER, program_tab: Any, hardware_tab: Any, response_textarea: pn.pane.HTML, header: pn.pane.Alert) -> None:
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
        self.response_textarea = response_textarea
        self.header = header
        assets_dir = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(__file__), 'assets')
        self.img_path: str = os.path.join(assets_dir, 'mouse_still.jpg')
        self.gif_path: str = os.path.join(assets_dir, 'mouse.gif')
        self.animation_image: pn.pane.Image = pn.pane.Image(self.img_path, width=200)
        self.animation_markdown: pn.pane.Markdown = pn.pane.Markdown("`Waiting...`")
        self.df: pd.DataFrame = pd.DataFrame()
        self.plotly_pane: pn.pane.Plotly = pn.pane.Plotly(sizing_mode="stretch_width", height=600)
        self.summary_pane: pn.pane.DataFrame = pn.pane.DataFrame(index=False, max_rows=10, styles={"background-color": "#1e1e1e", "color": "white"})
        self.start_program_button: pn.widgets.Button = pn.widgets.Button(icon="player-play")
        self.start_program_button.on_click(self.start)
        self.pause_program_button: pn.widgets.Button = pn.widgets.Button(icon="player-pause")
        self.pause_program_button.on_click(self.pause)
        self.stop_program_button: pn.widgets.Button = pn.widgets.Button(icon="player-stop")
        self.stop_program_button.on_click(self.stop)
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
            return pd.DataFrame(columns=["event", "device", "count"])
        try:
            summary = df[df["event"] != "START"].groupby(["event", "device"]).size().reset_index(name="count")
            return summary
        except KeyError as e:
            self.add_error("KeyError: Missing column(s) in DataFrame.", str(e))
            return pd.DataFrame(columns=["event", "device", "count"])

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
        components = self.df['device'].unique()
        y_positions = {component: i for i, component in enumerate(components)}
        colors = {
            'ACTIVE_PRESS': 'red',
            'TIMEOUT_PRESS': 'grey',
            'INACTIVE_PRESS': 'black',
            'LICK': 'pink',
            'INFUSION': 'red',
            'STIM': 'green',
            'START': 'red'
        }
        fig = go.Figure(layout=dict(height=600))
        for _, row in self.df.iterrows():
            component = row['device']
            action = row['event']
            start = row['start_timestamp']
            end = row['end_timestamp']
            y_pos = y_positions[component]

            if action == 'START':
                fig.add_vline(
                    x=start,
                    line=dict(color=colors.get(action, 'grey'), width=2, dash='dash'),
                    name=component
                )
            elif action == 'STIM':
                fig.add_vrect(
                    x0=start,
                    x1=end,
                    fillcolor='rgba(0,128,0,0.15)',
                    line_width=0,
                    layer='below',
                    name=component
                )
            else:
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

    def start(self, _: Any) -> None:
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

    def pause(self, _: Any) -> None:
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

    def stop(self, _: Any) -> None:
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
            df = pd.DataFrame.from_records(data, columns=['device', 'event', 'start_timestamp', 'end_timestamp'])
            series = pd.Series(frames)
            rh_active_data = df[(df['device'] == 'RH_LEVER') & (df['event'] == 'ACTIVE_PRESS')]
            rh_timeout_data = df[(df['device'] == 'RH_LEVER') & (df['event'] == 'TIMEOUT_PRESS')]
            rh_inactive_data = df[(df['device'] == 'RH_LEVER') & (df['event'] == 'INACTIVE_PRESS')]
            lh_active_data = df[(df['device'] == 'LH_LEVER') & (df['event'] == 'ACTIVE_PRESS')]
            lh_timeout_data = df[(df['device'] == 'LH_LEVER') & (df['event'] == 'TIMEOUT_PRESS')]
            lh_inactive_data = df[(df['device'] == 'LH_LEVER') & (df['event'] == 'INACTIVE_PRESS')]
            pump_data = df[df['device'] == 'PUMP']
            lick_data = df[df['device'] == 'LICK_CIRCUIT']
            laser_data = df[df['device'] == 'LASER']
            summary_dict = {
                'Start Time': start_time,
                'End Time': end_time,
                'Behavior Chamber': self.reacher.get_box_name(),
                'RH Active Presses': len(rh_active_data) if not rh_active_data.empty else 0,
                'RH Timeout Presses': len(rh_timeout_data) if not rh_timeout_data.empty else 0,
                'RH Inactive Presses': len(rh_inactive_data) if not rh_inactive_data.empty else 0,
                'LH Active Presses': len(lh_active_data) if not lh_active_data.empty else 0,
                'LH Timeout Presses': len(lh_timeout_data) if not lh_timeout_data.empty else 0,
                'LH Inactive Presses': len(lh_inactive_data) if not lh_inactive_data.empty else 0,
                'Infusions': len(pump_data[pump_data['event'] == 'INFUSION']) if not pump_data.empty else 0,
                'Licks': len(lick_data[lick_data['event'] == 'LICK']) if not lick_data.empty else 0,
                'Stims': len(laser_data[laser_data['event'] == 'STIM']) if not laser_data.empty else 0,
                'Frames Collected': len(frames)
            }
            summary = pd.Series(summary_dict)
            destination = self.reacher.make_destination_folder()
            output = os.path.join(destination, f'{self.reacher.get_filename()}.xlsx')
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                summary.to_excel(writer, sheet_name='Session Summary')
                df.to_excel(writer, sheet_name='Behavior Data')
                arduino_configuration_summary.to_excel(writer, sheet_name='Arduino Configuration')
                series.to_excel(writer, sheet_name='Frame Timestamps')
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
        self.start_program_button.disabled = False

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
                width=300
            ),
            styles=dict(background="white")
        )
        return pn.Column(program_control_area, plot_area)

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """