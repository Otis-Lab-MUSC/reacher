import panel as pn
from typing import Any
from .dashboard import Dashboard
from reacher.kernel import REACHER

class HomeTab(Dashboard):
    """A class to manage the Home tab UI for REACHER experiments, inheriting from Dashboard."""

    def __init__(self, reacher: REACHER) -> None:
        """Initialize the HomeTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Extends Dashboard to provide controls for microcontroller connections.
        - Sets up buttons and a menu for COM port management.
        """
        super().__init__()
        self.reacher = reacher
        self.search_microcontrollers_button: pn.widgets.Button = pn.widgets.Button(name="Search Microcontrollers", icon="search")
        self.search_microcontrollers_button.on_click(self.search_for_microcontrollers)
        self.microcontroller_menu: pn.widgets.Select = pn.widgets.Select(name="Microcontroller", options=[])
        self.serial_connect_button: pn.widgets.Button = pn.widgets.Button(name="Connect", icon="plug")
        self.serial_connect_button.on_click(self.connect_to_microcontroller)
        self.serial_disconnect_button: pn.widgets.Button = pn.widgets.Button(name="Disconnect")
        self.serial_disconnect_button.on_click(self.disconnect_from_microcontroller)

    def search_for_microcontrollers(self, _: Any) -> None:
        """Search for available microcontrollers and update the menu.

        **Description:**
        - Scans for available COM ports and populates the microcontroller menu.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.add_response("Searching for microcontrollers...")
        available_ports = self.reacher.get_COM_ports()
        if available_ports and "No available ports" not in available_ports:
            self.microcontroller_menu.options = available_ports
            self.add_response(f"Found {len(available_ports)} available ports.")
        else:
            self.add_response("No valid COM ports found. Please connect a device and try again.")

    def set_COM(self) -> None:
        """Set the selected COM port for the REACHER instance.

        **Description:**
        - Configures the REACHER instance to use the selected COM port.
        """
        try:
            self.reacher.set_COM_port(self.microcontroller_menu.value)
            self.add_response(f"Set COM port to {self.microcontroller_menu.value}")
        except Exception as e:
            self.add_error("Exception caught while setting COM port", str(e))

    def connect_to_microcontroller(self, _: Any) -> None:
        """Connect to the selected microcontroller.

        **Description:**
        - Opens a serial connection to the selected COM port.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.set_COM()
            self.reacher.open_serial()
            self.add_response("Opened serial connection")
        except Exception as e:
            self.add_error(f"Failed to connect to {self.microcontroller_menu.value}", str(e))

    def disconnect_from_microcontroller(self, _: Any) -> None:
        """Disconnect from the microcontroller.

        **Description:**
        - Closes the serial connection to the microcontroller.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.close_serial()
            self.add_response("Closed serial connection")
        except Exception as e:
            self.add_error(f"Failed to disconnect from {self.microcontroller_menu.value}", str(e))

    def reset(self) -> None:
        """Reset the HomeTab to its initial state.

        **Description:**
        - Clears the microcontroller menu options.
        """
        self.add_response("Resetting home tab")
        self.microcontroller_menu.options = []

    def layout(self) -> pn.Column:
        """Construct the layout for the HomeTab.

        **Description:**
        - Builds the tab-specific UI with COM port selection and connection controls.

        **Returns:**
        - `pn.Column`: The HomeTab layout.
        """
        microcontroller_layout = pn.Column(
            pn.pane.Markdown("### COM Connection"),
            self.microcontroller_menu,
            self.search_microcontrollers_button,
            pn.Row(self.serial_connect_button, self.serial_disconnect_button)
        )
        return pn.Column(microcontroller_layout)

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """