import panel as pn
from typing import Any
from .dashboard import Dashboard

class ScheduleTab(Dashboard):
    """A class to manage the Schedule tab UI for configuring wireless REACHER schedules, inheriting from Dashboard
."""

    def __init__(self) -> None:
        """Initialize the ScheduleTab with inherited Dashboard
     components and tab-specific UI.

        **Description:**
        - Sets up UI controls for configuring within-trial dynamics and training schedules.
        - Allows sending schedule parameters to the API.
        """
        super().__init__()
        self.timeout_intslider: pn.widgets.IntSlider = pn.widgets.IntSlider(name="Timeout Duration(s)", value=20, start=0, end=600, step=5)
        self.send_timeout_button: pn.widgets.Button = pn.widgets.Button(icon="upload")
        self.send_timeout_button.on_click(self.send_timeout)
        self.trace_intslider: pn.widgets.IntSlider = pn.widgets.IntSlider(name="Trace Duration(s)", value=0, start=0, end=60, step=1)
        self.send_trace_button: pn.widgets.Button = pn.widgets.Button(icon="upload")
        self.send_trace_button.on_click(self.send_trace)
        self.fixed_ratio_intslider: pn.widgets.IntSlider = pn.widgets.IntSlider(name="Fixed Ratio Interval", value=1, start=1, end=50, step=1)
        self.send_fixed_ratio_button: pn.widgets.Button = pn.widgets.Button(icon="upload")
        self.send_fixed_ratio_button.on_click(self.send_fixed_ratio)
        self.progressive_ratio_intslider: pn.widgets.IntSlider = pn.widgets.IntSlider(name="Progressive Ratio", value=2, start=1, end=50, step=1)
        self.send_progressive_ratio_button: pn.widgets.Button = pn.widgets.Button(icon="upload")
        self.send_progressive_ratio_button.on_click(self.send_progressive_ratio)
        self.variable_interval_intslider: pn.widgets.IntSlider = pn.widgets.IntSlider(name="Variable Interval", value=15, start=1, end=100, step=1)
        self.send_variable_interval_button: pn.widgets.Button = pn.widgets.Button(icon="upload")
        self.send_variable_interval_button.on_click(self.send_variable_interval)
        self.omission_interval_intslider: pn.widgets.IntSlider = pn.widgets.IntSlider(name="Omission Interval", value=20, start=1, end=100, step=1)
        self.send_omission_interval_button: pn.widgets.Button = pn.widgets.Button(icon="upload")
        self.send_omission_interval_button.on_click(self.send_omission_interval)

    def send_command(self, command: str, value: Any) -> None:
        """Send a command with a value to the API.

        **Description:**
        - Generic method to send schedule-related commands to the API.

        **Args:**
        - `command (str)`: The command to send.
        - `value (Any)`: The value associated with the command.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/serial/command", 
                                    json={'command': f"{command}:{value}"})
            response.raise_for_status()
            self.add_response(response.json().get('status', f"{command} set to {value}"))
        except Exception as e:
            self.add_error(f"Failed to send {command}", str(e))

    def send_timeout(self, _: Any) -> None:
        """Send the timeout duration to the API.

        **Description:**
        - Transmits the timeout duration in milliseconds.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.send_command("SET_TIMEOUT_PERIOD_LENGTH", self.timeout_intslider.value * 1000)

    def send_trace(self, _: Any) -> None:
        """Send the trace duration to the API.

        **Description:**
        - Transmits the trace duration in milliseconds.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.send_command("SET_TRACE_INTERVAL", self.trace_intslider.value * 1000)

    def send_fixed_ratio(self, _: Any) -> None:
        """Send the fixed ratio interval to the API.

        **Description:**
        - Transmits the fixed ratio interval.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.send_command("SET_RATIO", self.fixed_ratio_intslider.value)

    def send_progressive_ratio(self, _: Any) -> None:
        """Send the progressive ratio interval to the API.

        **Description:**
        - Transmits the progressive ratio interval.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.send_command("SET_RATIO", self.progressive_ratio_intslider.value)

    def send_variable_interval(self, _: Any) -> None:
        """Send the variable interval to the API.

        **Description:**
        - Transmits the variable interval.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.send_command("SET_VARIABLE_INTERVAL", self.variable_interval_intslider.value)

    def send_omission_interval(self, _: Any) -> None:
        """Send the omission interval to the API.

        **Description:**
        - Transmits the omission interval in milliseconds.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.send_command("SET_OMISSION_INTERVAL", self.omission_interval_intslider.value * 1000)

    def layout(self) -> pn.Row:
        """Construct the layout for the ScheduleTab.

        **Description:**
        - Assembles the UI with sections for within-trial dynamics and training schedules.

        **Returns:**
        - `pn.Row`: The ScheduleTab layout.
        """
        within_trial_dynamics_area = pn.Column(
            pn.pane.Markdown("### Within-Trial Dynamics"),
            pn.Row(self.timeout_intslider, self.send_timeout_button),
            pn.Row(self.trace_intslider, self.send_trace_button)
        )
        training_schedule_area = pn.Column(
            pn.pane.Markdown("### Training Schedule"),
            pn.Row(self.fixed_ratio_intslider, self.send_fixed_ratio_button),
            pn.Row(self.progressive_ratio_intslider, self.send_progressive_ratio_button),
            pn.Row(self.variable_interval_intslider, self.send_variable_interval_button),
            pn.Row(self.omission_interval_intslider, self.send_omission_interval_button),
        )
        return pn.Row(within_trial_dynamics_area, pn.Spacer(width=100), training_schedule_area)

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """