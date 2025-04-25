import panel as pn
import os
import sys
import datetime
import pandas as pd
import plotly.graph_objects as go
from typing import Optional, Any
from .dashboard import Dashboard
from .program_tab import ProgramTab
from .hardware_tab import HardwareTab

class MonitorTab(Dashboard):
    """A class to manage the Monitor tab UI for real-time wireless experiment monitoring, inheriting from Dashboard."""

    def __init__(self, behavior_chamber: str) -> None:
        """Initialize the MonitorTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Sets up UI for starting, pausing, stopping, and monitoring experiments.
        - Displays real-time plots and summaries of behavioral data.

        **Note:**
        - Requires `program_tab` and `hardware_tab` to be set by a parent class (e.g., WirelessDashboard) for inter-tab dependencies.
        """
        super().__init__()
        assets_dir = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(__file__), 'assets')
        self.img_path: str = os.path.join(assets_dir, 'mouse_still.jpg')
        self.gif_path: str = os.path.join(assets_dir, 'mouse.gif')
        self.animation_image: pn.pane.Image = pn.pane.Image(self.img_path, width=200)
        self.animation_markdown: pn.pane.Markdown = pn.pane.Markdown("`Waiting...`")
        self.behavior_data: pd.DataFrame = pd.DataFrame()
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
        self.program_tab: ProgramTab = None
        self.hardware_tab: HardwareTab = None
        self.behavior_chamber: str = behavior_chamber

    def fetch_data(self) -> pd.DataFrame:
        """Fetch behavioral data from the API.

        **Description:**
        - Retrieves the latest behavioral data and converts it to a DataFrame.

        **Returns:**
        - `pd.DataFrame`: DataFrame containing behavioral data.
        """
        if not self.api_connected:
            return pd.DataFrame()
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/processor/behavior_data")
            response.raise_for_status()
            data = response.json().get('data', [])
            self.add_response(response.json().get('status', 'Data fetched'))
            return pd.DataFrame(data)
        except Exception as e:
            self.add_error("Failed to fetch data", str(e))
            return pd.DataFrame()

    def update_summary_table(self, behavior_data: pd.DataFrame) -> pd.DataFrame:
        """Generate a summary table from behavioral data.

        **Description:**
        - Creates a summary of actions and components with their counts.

        **Args:**
        - `behavior_data (pd.DataFrame)`: The behavioral data DataFrame.

        **Returns:**
        - `pd.DataFrame`: Summary DataFrame with action and component counts.
        """
        if behavior_data.empty:
            return pd.DataFrame(columns=["Action", "Component", "Count"])
        return behavior_data.groupby(["Action", "Component"]).size().reset_index(name="Count")

    def generate_plotly_plot(self) -> go.Figure:
        """Generate a Plotly plot of the behavioral events.

        **Description:**
        - Creates a timeline visualization of behavioral events with color-coded actions.

        **Returns:**
        - `go.Figure`: The Plotly figure object.
        """
        if self.behavior_data.empty:
            fig = go.Figure()
            fig.add_annotation(text="No data available", showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
            return fig
        components = self.behavior_data['Component'].unique()
        y_positions = {comp: i for i, comp in enumerate(components)}
        colors = {'ACTIVE_PRESS': 'red', 'TIMEOUT_PRESS': 'grey', 'INACTIVE_PRESS': 'black', 'LICK': 'pink', 'INFUSION': 'red', 'STIM': 'green'}
        fig = go.Figure()
        for _, row in self.behavior_data.iterrows():
            y_pos = y_positions[row['Component']]
            fig.add_trace(go.Scatter(
                x=[row['Start Timestamp'], row['End Timestamp']],
                y=[y_pos, y_pos],
                mode='lines+markers',
                line=dict(color=colors.get(row['Action'], 'blue'), width=2),
                marker=dict(symbol='line-ew-open', size=10),
                name=row['Component']
            ))
        fig.update_layout(
            title="Event Timeline", xaxis_title="Timestamp",
            yaxis=dict(title="Components", tickvals=list(y_positions.values()), ticktext=list(y_positions.keys())),
            showlegend=False, height=600
        )
        return fig

    def update_plot(self) -> None:
        """Update the plot and summary table based on current data.

        **Description:**
        - Refreshes the Plotly plot and summary table with the latest data.
        - Manages animation state based on program activity.
        """
        if not self.api_connected:
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/activity")
            is_active = response.json().get('activity', False)
            self.add_response(response.json().get('status', 'Activity checked'))
            if not is_active and self.periodic_callback:
                self.periodic_callback.stop()
                self.periodic_callback = None
                self.animation_image.object = self.img_path
                self.animation_markdown.object = "`Finished.`"
                self.header.alert_type = "success"
                self.header.object = "Program finished."
            new_data = self.fetch_data()
            if not new_data.empty:
                self.behavior_data = new_data
            self.plotly_pane.object = self.generate_plotly_plot()
            self.summary_pane.object = self.update_summary_table(new_data)
        except Exception as e:
            self.add_error("Failed to update plot", str(e))

    def start_program(self, _: Any) -> None:
        """Start the experimental program via the API.

        **Description:**
        - Initiates the experiment, arms devices, and starts periodic updates.

        **Args:**
        - `_ (Any)`: Unused event argument.

        **Note:**
        - Requires `program_tab` and `hardware_tab` to be set by a parent class.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        if self.program_tab is None or self.hardware_tab is None:
            self.add_error("Dependencies not set", "ProgramTab or HardwareTab not initialized.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/start")
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Program started'))
            if pn.state.curdoc and not self.periodic_callback:
                self.periodic_callback = pn.state.add_periodic_callback(self.update_plot, period=5000)
            self.animation_image.object = self.gif_path
            self.animation_markdown.object = "`Running...`"
            self.hardware_tab.arm_devices(self.program_tab.get_hardware())
            self.start_program_button.disabled = True
            self.header.alert_type = "warning"
            self.header.object = "WARNING: Program in progress..."
        except Exception as e:
            self.add_error("Failed to start program", str(e))

    def pause_program(self, _: Any) -> None:
        """Pause or resume the experimental program via the API.

        **Description:**
        - Toggles between pausing and resuming the experiment, updating the UI accordingly.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/interim")
            response.raise_for_status()
            data = response.json()
            if data.get('state'):
                self.animation_image.object = self.img_path
                self.animation_markdown.object = "`Paused...`"
                self.pause_program_button.icon = "player-play"
            else:
                self.animation_image.object = self.gif_path
                self.animation_markdown.object = "`Running...`"
                self.pause_program_button.icon = "player-pause"
            self.add_response(data.get('status', 'Program state toggled'))
        except Exception as e:
            self.add_error("Failed to pause/resume program", str(e))

    def stop_program(self, _: Any) -> None:
        """Stop the experimental program via the API.

        **Description:**
        - Terminates the experiment and updates the UI to reflect completion.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/end")
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Program stopped'))
            if self.periodic_callback:
                self.periodic_callback.stop()
                self.periodic_callback = None
            self.animation_image.object = self.img_path
            self.animation_markdown.object = "`Finished.`"
            self.start_program_button.disabled = False
            self.header.alert_type = "success"
            self.header.object = "Program finished."
        except Exception as e:
            self.add_error("Failed to stop program", str(e))

    def download(self, _: Any) -> None:
        """Download experiment data from the API to local files.

        **Description:**
        - Exports behavioral data, frame timestamps, and summaries to CSV files in the Downloads directory.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            responses = {
                'start_time': requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/start_time"),
                'end_time': requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/end_time"),
                'arduino_configuration': requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/processor/arduino_configuration"),
                'data': requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/processor/data"),
                'filename': requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/file/filename"),
            }
            for key, resp in responses.items():
                resp.raise_for_status()
                self.add_response(resp.json().get('status', f'{key} retrieved'))

            filename = responses['filename'].json().get('name')
            filename_root = filename.split('.')[0]
            downloads_dir = os.path.expanduser(f'~/Downloads/{filename_root}')
            if not os.path.exists(downloads_dir):
                os.makedirs(downloads_dir, exist_ok=True)

            behavior_data = pd.DataFrame(responses['data'].json()['data'])
            behavior_filepath = os.path.join(downloads_dir, filename)
            behavior_data.to_csv(behavior_filepath, index=False)

            frame_data = pd.Series(responses['data'].json()['frames'])
            frame_filepath = os.path.join(downloads_dir, "frame-timestamps.csv")
            frame_data.to_csv(frame_filepath, index=False)

            start_time = datetime.datetime.fromtimestamp(responses['start_time'].json()['start_time']).strftime('%H:%M:%S')
            end_time = datetime.datetime.fromtimestamp(responses['end_time'].json()['end_time']).strftime('%H:%M:%S')
            rh_active_data = behavior_data[(behavior_data['Component'] == 'RH_LEVER') & (behavior_data['Action'] == 'ACTIVE_PRESS')]
            rh_timeout_data = behavior_data[(behavior_data['Component'] == 'RH_LEVER') & (behavior_data['Action'] == 'TIMEOUT_PRESS')]
            rh_inactive_data = behavior_data[(behavior_data['Component'] == 'RH_LEVER') & (behavior_data['Action'] == 'INACTIVE_PRESS')]
            lh_active_data = behavior_data[(behavior_data['Component'] == 'LH_LEVER') & (behavior_data['Action'] == 'ACTIVE_PRESS')]
            lh_timeout_data = behavior_data[(behavior_data['Component'] == 'LH_LEVER') & (behavior_data['Action'] == 'TIMEOUT_PRESS')]
            lh_inactive_data = behavior_data[(behavior_data['Component'] == 'LH_LEVER') & (behavior_data['Action'] == 'INACTIVE_PRESS')]
            pump_data = behavior_data[behavior_data['Component'] == 'PUMP']
            lick_data = behavior_data[behavior_data['Component'] == 'LICK_CIRCUIT']
            laser_data = behavior_data[behavior_data['Component'] == 'LASER']
            summary_dict = {
                'Start Time': start_time,
                'End Time': end_time,
                'Behavior Chamber': self.behavior_chamber,
                'RH Active Presses': len(rh_active_data) if not rh_active_data.empty else 0,
                'RH Timeout Presses': len(rh_timeout_data) if not rh_timeout_data.empty else 0,
                'RH Inactive Presses': len(rh_inactive_data) if not rh_inactive_data.empty else 0,
                'LH Active Presses': len(lh_active_data) if not lh_active_data.empty else 0,
                'LH Timeout Presses': len(lh_timeout_data) if not lh_timeout_data.empty else 0,
                'LH Inactive Presses': len(lh_inactive_data) if not lh_inactive_data.empty else 0,
                'Infusions': len(pump_data[pump_data['Action'] == 'INFUSION']) if not pump_data.empty else 0,
                'Licks': len(lick_data[lick_data['Action'] == 'LICK']) if not lick_data.empty else 0,
                'Stims': len(laser_data[laser_data['Action'] == 'STIM']) if not laser_data.empty else 0,
                'Frames Collected': len(frame_data)
            }
            summary_filepath = os.path.join(downloads_dir, f"summary.csv")
            pd.Series(summary_dict).to_csv(summary_filepath)

            config = pd.Series(responses['arduino_configuration'].json()['arduino_configuration'])
            config_filepath = os.path.join(downloads_dir, "arduino_configuration.csv")
            config.to_csv(config_filepath, index=False)

            self.add_response(f"Data saved to {downloads_dir}")
        except Exception as e:
            self.add_error("Failed to download data", str(e))

    def layout(self) -> pn.Column:
        """Construct the layout for the MonitorTab.

        **Description:**
        - Assembles the UI with program controls and real-time data visualization.

        **Returns:**
        - `pn.Column`: The MonitorTab layout.
        """
        return pn.Column(
            pn.Row(self.start_program_button, self.pause_program_button, self.stop_program_button, self.download_button),
            pn.Row(self.plotly_pane, pn.Column(self.animation_image, self.animation_markdown, self.summary_pane, width=250))
        )

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """