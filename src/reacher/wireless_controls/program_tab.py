import panel as pn
from typing import List, Any, Dict
from .dashboard import Dashboard

class ProgramTab(Dashboard):
    """A class to manage the Program tab UI for configuring wireless REACHER experiments, inheriting from Dashboard."""

    def __init__(self) -> None:
        """Initialize the ProgramTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Configures UI elements for setting experiment parameters like hardware, limits, and file output.
        - Supports preset configurations for quick setup.
        """
        super().__init__()
        self.hardware_checkbuttongroup: pn.widgets.CheckButtonGroup = pn.widgets.CheckButtonGroup(
            name="Select hardware to use:",
            options=["LH Lever", "RH Lever", "Cue", "Pump", "Lick Circuit", "Laser", "Imaging Microscope"],
            value=["LH Lever", "RH Lever", "Cue", "Pump"],
            orientation='vertical',
            button_style="outline"
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
            options=list(self.presets_dict.keys())
        )
        self.limit_type_radiobutton: pn.widgets.RadioButtonGroup = pn.widgets.RadioButtonGroup(
            name="Limit Type",
            options=["Time", "Infusion", "Both"]
        )
        self.time_limit_hour: pn.widgets.IntInput = pn.widgets.IntInput(name="Hour(s)", value=0, start=0, end=10, step=1)
        self.time_limit_min: pn.widgets.IntInput = pn.widgets.IntInput(name="Minute(s)", value=0, start=0, end=59, step=1)
        self.time_limit_sec: pn.widgets.IntInput = pn.widgets.IntInput(name="Second(s)", value=0, start=0, end=59, step=5)
        self.time_limit_area: pn.Row = pn.Row(
            pn.Column(self.time_limit_hour, self.time_limit_min, self.time_limit_sec),
            pn.pane.Markdown(pn.bind(lambda h, m, s: f"**{h}hr {m}min {s}s**", 
                                     self.time_limit_hour, self.time_limit_min, self.time_limit_sec))
        )
        self.infusion_limit_intslider: pn.widgets.IntInput = pn.widgets.IntInput(name="Infusion(s)", value=0, start=0, end=100, step=1)
        self.stop_delay_intslider: pn.widgets.IntInput = pn.widgets.IntInput(name="Stop Delay (s)", value=0, start=0, end=59, step=1)
        self.set_program_limit_button: pn.widgets.Button = pn.widgets.Button(name="Set Program Limit", icon="gear")
        self.set_program_limit_button.on_click(self.set_program_limit)
        self.filename_textinput: pn.widgets.TextInput = pn.widgets.TextInput(name="File name:", placeholder="e.g., experiment1.csv")
        self.file_destination_textinput: pn.widgets.TextInput = pn.widgets.TextInput(name="Folder name:", placeholder="e.g., ~/REACHER/DATA")
        self.set_file_config_button: pn.widgets.Button = pn.widgets.Button(name="Set File Configuration", icon="file")
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
        """Set the program limits via the API based on user input.

        **Description:**
        - Sends experiment limit parameters to the API.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        data = {
            'type': self.limit_type_radiobutton.value,
            'infusion_limit': self.infusion_limit_intslider.value,
            'time_limit': (self.time_limit_hour.value * 3600) + 
                          (self.time_limit_min.value * 60) + 
                          self.time_limit_sec.value,
            'delay': self.stop_delay_intslider.value
        }
        try:
            preset_func = self.presets_dict.get(self.presets_menubutton.value)
            if preset_func and self.presets_menubutton.value != "Custom":
                preset_func()
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/program/limit", json=data)
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Limits set successfully'))
        except Exception as e:
            self.add_error("Failed to set program limits", str(e))

    def set_file_configuration(self, _: Any) -> None:
        """Set the file configuration for data output via the API.

        **Description:**
        - Configures the API with the filename and destination for data logging.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/file/filename", 
                                    json={'name': self.filename_textinput.value})
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Filename set'))
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/file/destination", 
                                    json={'destination': self.file_destination_textinput.value})
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Destination set'))
        except Exception as e:
            self.add_error("Failed to set file configuration", str(e))

    def get_hardware(self) -> List[str]:
        """Get the selected hardware components.

        **Description:**
        - Retrieves the list of hardware components selected by the user.

        **Returns:**
        - `List[str]`: List of selected hardware component names.
        """
        return self.hardware_checkbuttongroup.value

    def layout(self) -> pn.Column:
        """Construct the layout for the ProgramTab.

        **Description:**
        - Assembles the UI with sections for presets, hardware, limits, and file configuration.

        **Returns:**
        - `pn.Column`: The ProgramTab layout.
        """
        return pn.Column(
            pn.Row(self.presets_menubutton, self.set_program_limit_button),
            pn.Spacer(height=50),
            pn.Row(
                pn.Column(pn.pane.Markdown("### Components"), self.hardware_checkbuttongroup),
                pn.Spacer(width=100),
                pn.Column(pn.pane.Markdown("### Limits"), self.limit_type_radiobutton, self.time_limit_area, 
                          self.infusion_limit_intslider, self.stop_delay_intslider)
            ),
            pn.Spacer(height=50),
            pn.Column(pn.pane.Markdown("### File Configuration"), self.filename_textinput, 
                      self.file_destination_textinput, self.set_file_config_button)
        )

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """