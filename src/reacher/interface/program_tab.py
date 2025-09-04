import panel as pn
from typing import List, Any, Dict
from .dashboard import Dashboard
from reacher.kernel import REACHER

class ProgramTab(Dashboard):
    """A class to manage the Program tab UI for configuring REACHER experiments, inheriting from Dashboard."""

    def __init__(self, reacher: REACHER, response_textarea: pn.pane.HTML) -> None:
        """Initialize the ProgramTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Configures UI elements for setting experiment parameters like hardware, limits, and file output.
        - Supports preset configurations for quick setup.
        """
        super().__init__()
        self.reacher = reacher
        self.response_textarea = response_textarea
        self.hardware_checkbuttongroup: pn.widgets.CheckButtonGroup = pn.widgets.CheckButtonGroup(
            name="Select hardware to use:",
            options=["LH Lever", "RH Lever", "Cue", "Pump", "Lick Circuit", "Laser", "Imaging Microscope"],
            orientation='vertical',
            button_style="outline",
            button_type="primary"
        )
        self.presets_dict: Dict[str, callable] = {
            "Custom": lambda: None,
            "SA High": lambda: self.set_preset("Both", 10, 3600, 10),
            "SA Mid": lambda: self.set_preset("Both", 20, 3600, 10),
            "SA Low": lambda: self.set_preset("Both", 40, 3600, 10),
            "SA Extinction": lambda: self.set_preset("Time", 0, 3600, 0)
        }
        self.presets_menubutton: pn.widgets.Select = pn.widgets.Select(
            name="Select a preset:",
            options=list(self.presets_dict.keys()),
        )
        self.limit_type_radiobutton: pn.widgets.RadioButtonGroup = pn.widgets.RadioButtonGroup(
            name="Limit Type",
            options=["Time", "Infusion", "Both"],
            button_type="primary"
        )
        self.time_limit_hour: pn.widgets.IntInput = pn.widgets.IntInput(name="Hour(s)", value=0, start=0, end=10, step=1)
        self.time_limit_min: pn.widgets.IntInput = pn.widgets.IntInput(name="Minute(s)", value=0, start=0, end=59, step=1)
        self.time_limit_sec: pn.widgets.IntInput = pn.widgets.IntInput(name="Second(s)", value=0, start=0, end=59, step=5)
        self.formatted_time_limit_output = pn.bind(self.format_time, self.time_limit_hour, self.time_limit_min, self.time_limit_sec)
        self.time_limit_area: pn.Row = pn.Row(
            pn.Column(self.time_limit_hour, self.time_limit_min, self.time_limit_sec),
            pn.pane.Markdown(pn.bind(lambda x: f"**{x}**", self.formatted_time_limit_output))
        )
        self.infusion_limit_intslider: pn.widgets.IntInput = pn.widgets.IntInput(name="Infusion(s)", value=0, start=0, end=100, step=1)
        self.stop_delay_intslider: pn.widgets.IntInput = pn.widgets.IntInput(name="Stop Delay (s)", value=0, start=0, end=59, step=1)
        self.set_program_limit_button: pn.widgets.Button = pn.widgets.Button(name="Set Program Limit", icon="gear", button_type="primary")
        self.set_program_limit_button.on_click(self.set_program_limit)
        self.filename_textinput: pn.widgets.TextInput = pn.widgets.TextInput(name="File name:", placeholder="e.g., experiment1.csv")
        self.file_destination_textinput: pn.widgets.TextInput = pn.widgets.TextInput(name="Folder name:", placeholder="e.g., ~/REACHER/DATA")
        self.set_file_config_button: pn.widgets.Button = pn.widgets.Button(name="Set File Configuration", icon="file", button_type="primary")
        self.set_file_config_button.on_click(self.set_file_configuration)

    def set_preset(self, limit_type: str, infusion_limit: int, time_limit: int, stop_delay: int) -> None:
        """Set program parameters based on a preset configuration.

        **Description:**
        - Applies a predefined set of experiment parameters to the UI controls.

        **Args:**
        - `limit_type (str)`: The type of limit ('Time', 'Infusion', or 'Both').
        - `infusion_limit (int)`: The maximum number of infusions.
        - `time_limit (int)`: The time limit in seconds.
        - `stop_delay (int)`: The stop delay in seconds.
        """
        self.limit_type_radiobutton.value = limit_type
        self.infusion_limit_intslider.value = infusion_limit
        hours, remainder = divmod(time_limit, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.time_limit_hour.value = hours
        self.time_limit_min.value = minutes
        self.time_limit_sec.value = seconds
        self.stop_delay_intslider.value = stop_delay

    def set_program_limit(self, _: Any) -> None:
        """Set the program limits based on user input.

        **Description:**
        - Configures the REACHER instance with user-defined or preset limits.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            set_limit = self.presets_dict.get(self.presets_menubutton.value)
            if set_limit:
                set_limit()
            self.reacher.set_limit_type(self.limit_type_radiobutton.value)
            self.reacher.set_infusion_limit(self.infusion_limit_intslider.value)
            total_seconds = (self.time_limit_hour.value * 60 * 60) + (self.time_limit_min.value * 60) + self.time_limit_sec.value
            self.reacher.set_time_limit(total_seconds)
            self.reacher.set_stop_delay(self.stop_delay_intslider.value)
            self.add_response(f"Set limit type to {self.limit_type_radiobutton.value}")
            self.add_response(f"Set infusion limit to {self.infusion_limit_intslider.value}")
            self.add_response(f"Set time limit to {total_seconds}")
            self.add_response(f"Set stop delay to {self.stop_delay_intslider.value}")
        except Exception as e:
            self.add_error("Failed to set program limit", str(e))

    def format_time(self, hours: int, minutes: int, seconds: int) -> str:
        """Format time inputs into a readable string.

        **Description:**
        - Converts hours, minutes, and seconds into a human-readable time string.

        **Args:**
        - `hours (int)`: Hours component.
        - `minutes (int)`: Minutes component.
        - `seconds (int)`: Seconds component.

        **Returns:**
        - `str`: Formatted time string (e.g., "1hr 30min 45s").
        """
        total_minutes = minutes
        extra_hours, minutes = divmod(total_minutes, 60)
        hours += extra_hours
        return f"{hours}hr {minutes}min {seconds}s"
    
    def get_formatted_time(self) -> str:
        hours = self.time_limit_hour.value
        minutes = self.time_limit_min.value
        seconds = self.time_limit_sec.value
        total_minutes = minutes
        extra_hours, minutes = divmod(total_minutes, 60)
        hours += extra_hours
        return f"{hours}hr {minutes}min {seconds}s"

    def get_hardware(self) -> List[str]:
        """Get the selected hardware components.

        **Description:**
        - Retrieves the list of hardware components selected by the user.

        **Returns:**
        - `List[str]`: List of selected hardware component names.
        """
        return self.hardware_checkbuttongroup.value

    def set_file_configuration(self, _: Any) -> None:
        """Set the file configuration for data output.

        **Description:**
        - Configures the REACHER instance with the filename and destination for data logging.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.set_filename(self.filename_textinput.value)
            self.add_response(f"Set filename to {self.filename_textinput.value}")
        except Exception as e:
            self.add_error("Failed to set file name", str(e))
        try:
            self.reacher.set_data_destination(self.file_destination_textinput.value)
            self.add_response(f"Set data destination to {self.file_destination_textinput.value}")
        except Exception as e:
            self.add_error("Failed to get file destination", str(e))

    def reset(self) -> None:
        """Reset the ProgramTab to its initial state.

        **Description:**
        - Restores default values for hardware selection, limits, and presets.
        """
        self.add_response("Resetting program tab")
        self.hardware_checkbuttongroup.value = ["LH Lever", "RH Lever", "Cue", "Pump"]
        self.presets_menubutton.name = "Select a preset:"
        self.limit_type_radiobutton.value = None
        self.time_limit_hour.value = 0
        self.time_limit_min.value = 0
        self.time_limit_sec.value = 0
        self.infusion_limit_intslider.value = 0

    def layout(self) -> pn.Column:
        """Construct the layout for the ProgramTab.

        **Description:**
        - Assembles the UI with sections for presets, hardware, limits, and file configuration.

        **Returns:**
        - `pn.Column`: The ProgramTab layout.
        """
        components_area = pn.Column(
            pn.pane.Markdown("### Components"),
            self.hardware_checkbuttongroup
        )
        limits_area = pn.Column(
            pn.pane.Markdown("### Limits"),
            self.limit_type_radiobutton,
            self.time_limit_area,
            self.infusion_limit_intslider,
            self.stop_delay_intslider,
        )
        file_configuration_area = pn.Column(
            pn.pane.Markdown("### File Configuration"),
            self.filename_textinput,
            self.file_destination_textinput,
            self.set_file_config_button
        )
        return pn.Column(
            pn.Row(self.presets_menubutton, self.set_program_limit_button),
            pn.Spacer(height=50),
            pn.Row(components_area, pn.Spacer(width=100), limits_area),
            pn.Spacer(height=50),
            file_configuration_area
        )

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """