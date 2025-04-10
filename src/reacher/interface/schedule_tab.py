import panel as pn
from typing import Any
from .dashboard import Dashboard
from reacher.kernel import REACHER

class ScheduleTab(Dashboard):
    """A class to manage the Schedule tab UI for configuring REACHER schedules, inheriting from Dashboard."""

    def __init__(self, reacher: REACHER, response_textarea: pn.pane.HTML) -> None:
        """Initialize the ScheduleTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Sets up UI controls for configuring within-trial dynamics and training schedules.
        - Allows sending schedule parameters to the microcontroller.
        """
        super().__init__()
        self.reacher = reacher
        self.response_textarea = response_textarea
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

    def send_timeout(self, _: Any) -> None:
        """Send the timeout duration to the Arduino.

        **Description:**
        - Transmits the timeout duration in milliseconds to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.send_serial_command(f"SET_TIMEOUT_PERIOD_LENGTH:{self.timeout_intslider.value * 1000}")
            self.add_response(f"Set timeout period to {self.timeout_intslider.value * 1000}")
        except Exception as e:
            self.add_error("Failed to send timeout interval", str(e))

    def send_trace(self, _: Any) -> None:
        """Send the trace duration to the Arduino.

        **Description:**
        - Transmits the trace duration in milliseconds to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.send_serial_command(f"SET_TRACE_INTERVAL:{self.trace_intslider.value * 1000}")
            self.add_response(f"Set trace interval to {self.trace_intslider.value * 1000}")
        except Exception as e:
            self.add_error("Failed to send trace interval", str(e))

    def send_fixed_ratio(self, _: Any) -> None:
        """Send the fixed ratio interval to the Arduino.

        **Description:**
        - Transmits the fixed ratio interval to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.send_serial_command(f"SET_RATIO:{self.fixed_ratio_intslider.value}")
            self.add_response(f"Set fixed ratio to {self.fixed_ratio_intslider.value}")
        except Exception as e:
            self.add_error("Failed to send fixed ratio interval", str(e))

    def send_progressive_ratio(self, _: Any) -> None:
        """Send the progressive ratio interval to the Arduino.

        **Description:**
        - Transmits the progressive ratio interval to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.send_serial_command(f"SET_RATIO:{self.progressive_ratio_intslider.value}")
            self.add_response(f"Set progressive ratio to {self.progressive_ratio_intslider.value}")
        except Exception as e:
            self.add_error("Failed to send progressive ratio interval", str(e))

    def send_variable_interval(self, _: Any) -> None:
        """Send the variable interval to the Arduino.

        **Description:**
        - Transmits the variable interval to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.send_serial_command(f"SET_VARIABLE_INTERVAL:{self.variable_interval_intslider.value}")
            self.add_response(f"Set variable interval to {self.variable_interval_intslider.value}")
        except Exception as e:
            self.add_error("Failed to send variable interval", str(e))

    def send_omission_interval(self, _: Any) -> None:
        """Send the omission interval to the Arduino.

        **Description:**
        - Transmits the omission interval in milliseconds to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.send_serial_command(f"SET_OMISSION_INTERVAL:{self.omission_interval_intslider.value * 1000}")
            self.add_response(f"Set omission interval to {self.omission_interval_intslider.value * 1000}")
        except Exception as e:
            self.add_error("Failed to send omission interval", str(e))

    def reset(self) -> None:
        """Reset the ScheduleTab to its initial state.

        **Description:**
        - Restores default values for all schedule parameters.
        """
        self.add_response("Resetting schedule tab")
        self.timeout_intslider.value = 20
        self.trace_intslider.value = 0
        self.fixed_ratio_intslider.value = 1
        self.progressive_ratio_intslider.value = 2
        self.variable_interval_intslider.value = 15
        self.omission_interval_intslider.value = 20

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