import panel as pn
from typing import Any
from .dashboard import Dashboard
from reacher.kernel import REACHER
import json

class HomeTab(Dashboard):
    """A class to manage the Home tab UI for REACHER experiments."""

    def __init__(self, reacher: REACHER, response_textarea: pn.pane.HTML) -> None:
        """Initialize the HomeTab with REACHER and shared response_textarea.

        **Description:**
        - Provides controls for microcontroller connections.
        - Uses the shared response_textarea for output.

        **Args:**
        - `reacher (REACHER)`: The REACHER instance.
        - `response_textarea (pn.pane.HTML)`: The shared response terminal pane.
        """
        super().__init__(reacher=reacher)
        self.response_textarea = response_textarea
        self.search_microcontrollers_button = pn.widgets.Button(name="Search Microcontrollers", icon="search")
        self.search_microcontrollers_button.on_click(self.search_for_microcontrollers)
        self.microcontroller_menu = pn.widgets.Select(name="Microcontroller", options=[])
        self.serial_connect_button = pn.widgets.Button(name="Connect", icon="plug")
        self.serial_connect_button.on_click(self.connect_to_microcontroller)
        self.serial_disconnect_button = pn.widgets.Button(name="Disconnect")
        self.serial_disconnect_button.on_click(self.disconnect_from_microcontroller)
        self.sketch_name_textbox = pn.widgets.StaticText(name="Firmware", value="(none loaded)")
        self.sketch_version_textbox = pn.widgets.StaticText(name="Version", value="(none loaded)")
        self.sketch_schedule_textbox = pn.widgets.StaticText(name="Schedule", value="(none loaded)")

    def search_for_microcontrollers(self, _: Any) -> None:
        """Search for available microcontrollers and update the menu."""
        self.add_response("Searching for microcontrollers...")
        available_ports = self.reacher.get_COM_ports()
        if available_ports and "No available ports" not in available_ports:
            self.microcontroller_menu.options = available_ports
            self.add_response(f"Found {len(available_ports)} available ports.")
        else:
            self.add_response("No valid COM ports found. Please connect a device and try again.")

    def set_COM(self) -> None:
        """Set the selected COM port for the REACHER instance."""
        try:
            self.reacher.set_COM_port(self.microcontroller_menu.value)
            self.add_response(f"Set COM port to {self.microcontroller_menu.value}")
        except Exception as e:
            self.add_error("Exception caught while setting COM port", str(e))

    def connect_to_microcontroller(self, _: Any) -> None:
        """Connect to the selected microcontroller."""
        try:
            self.set_COM()
            self.reacher.open_serial()
            self.add_response("Opened serial connection")
            
            config = self.reacher.get_arduino_configuration()
            
            if config["sketch"] == None:
                sketch_name = config["sketch"] if config["sketch"] != None else "None specified"
                version = config["version"]if config["version"] != None else "None specified"
                schedule = config["desc"]if config["desc"] != None else "None specified"
                
                self.sketch_name_textbox.value = sketch_name
                self.sketch_version_textbox.value = version
                self.sketch_schedule_textbox.value = schedule
            else:
                self.add_error("Loaded firmware is incompatible. Please upload a qualified file.", "Firmware missing valid fields.")
                self.add_response("Closing serial connection")
                self.reacher.close_serial()
            
        except Exception as e:
            self.add_error(f"Failed to connect to {self.microcontroller_menu.value}", str(e))

    def disconnect_from_microcontroller(self, _: Any) -> None:
        """Disconnect from the microcontroller."""
        try:
            self.reacher.close_serial()
            self.add_response("Closed serial connection")
        except Exception as e:
            self.add_error(f"Failed to disconnect from {self.microcontroller_menu.value}", str(e))

    def reset(self) -> None:
        """Reset the HomeTab to its initial state."""
        self.add_response("Resetting home tab")
        self.microcontroller_menu.options = []

    def layout(self) -> pn.Column:
        """Construct the layout for the HomeTab."""
        microcontroller_layout = pn.Column(
            pn.pane.Markdown("### COM Connection"),
            self.microcontroller_menu,
            self.search_microcontrollers_button,
            pn.Row(self.serial_connect_button, self.serial_disconnect_button),
            pn.Spacer(height=50),
            pn.Column(
                self.sketch_name_textbox,
                self.sketch_version_textbox,
                self.sketch_schedule_textbox
            )
        )
        return pn.Column(microcontroller_layout)

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """