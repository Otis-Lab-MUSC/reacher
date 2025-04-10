import panel as pn
import time
from typing import Any, Dict
from .dashboard import Dashboard
import socket
import json

class HomeTab(Dashboard):
    """A class to manage the Home tab UI for wireless REACHER experiments, inheriting from Dashboard."""

    def __init__(self) -> None:
        """Initialize the HomeTab with inherited Dashboard components and tab-specific UI.

        **Description:**
        - Extends Dashboard to provide controls for discovering devices and managing API/serial connections.
        - Sets up UI for device discovery and microcontroller selection.
        """
        super().__init__()
        self.server_select: pn.widgets.Select = pn.widgets.Select(name="Discovered Devices", options=[])
        self.search_server_button: pn.widgets.Button = pn.widgets.Button(icon="search", name="Search Devices")
        self.search_server_button.on_click(self.search_reacher_devices)
        self.verify_connection_button: pn.widgets.Button = pn.widgets.Button(name="Verify", icon="link")
        self.verify_connection_button.on_click(self.connect_to_api)
        self.search_microcontrollers_button: pn.widgets.Button = pn.widgets.Button(name="Search Microcontrollers", icon="search")
        self.search_microcontrollers_button.on_click(self.search_for_microcontrollers)
        self.microcontroller_menu: pn.widgets.Select = pn.widgets.Select(name="Microcontroller", options=[])
        self.serial_connect_button: pn.widgets.Button = pn.widgets.Button(name="Connect", icon="plug")
        self.serial_connect_button.on_click(self.connect_to_microcontroller)
        self.serial_disconnect_button: pn.widgets.Button = pn.widgets.Button(name="Disconnect", icon="plug-circle-xmark")
        self.serial_disconnect_button.on_click(self.disconnect_from_microcontroller)
        self.devices_dict: Dict[str, Dict[str, Any]] = {}

    def search_reacher_devices(self, _: Any) -> None:
        """Search for REACHER devices broadcasting on the network.

        **Description:**
        - Listens for UDP broadcasts from REACHER devices and populates the device menu.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        self.add_response(f"Listening for REACHER devices broadcasting on {local_ip}")
        try:
            services = self.discover_reacher_services(timeout=5)
            if services:
                self.add_response(f"Found {len(services)} device(s)")
                for _, info in services.items():
                    self.devices_dict[info['name']] = {
                        "host": info['address'],
                        "port": info['port'],
                        "key": info['key']
                    }
                    self.add_response(f"{info['name']} at {info['address']}:{info['port']}")
                self.server_select.options = list(self.devices_dict.keys())
            else:
                self.add_response("No devices found.")
        except Exception as e:
            self.add_error("Error during device search", str(e))

    def discover_reacher_services(self, timeout: int = 5) -> Dict[str, Dict[str, Any]]:
        """Discover REACHER services via UDP broadcasts.

        **Description:**
        - Receives UDP packets on port 7899 to identify REACHER devices.

        **Args:**
        - `timeout (int)`: Duration to listen for broadcasts (default: 5 seconds).

        **Returns:**
        - `Dict[str, Dict[str, Any]]`: Dictionary of discovered services with name, address, port, and key.
        """
        UDP_PORT = 7899
        services = {}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', UDP_PORT))
            sock.settimeout(timeout)
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    data, addr = sock.recvfrom(1024)
                    payload = json.loads(data.decode('utf-8'))
                    if payload.get('message') == "REACHER_DEVICE_DISCOVERY":
                        key = payload['key']
                        services[key] = {
                            'name': payload['name'],
                            'address': payload['address'],
                            'port': payload['port'],
                            'key': key
                        }
                except socket.timeout:
                    break
                except Exception as e:
                    self.add_error("Error receiving broadcast", str(e))
        return services

    def set_ip_address(self) -> None:
        """Set the API address based on the selected device.

        **Description:**
        - Configures the API with the host, port, and key of the selected device.
        """
        try:
            selected_device = self.server_select.value
            if selected_device:
                self.set_api_config(self.devices_dict[selected_device])
                self.add_response(f"Set API to {self.devices_dict[selected_device]['host']}:{self.devices_dict[selected_device]['port']}")
            else:
                self.add_response("No device selected.")
        except Exception as e:
            self.add_error("Unable to set API address", str(e))

    def connect_to_api(self, _: Any) -> None:
        """Verify and connect to the REACHER API.

        **Description:**
        - Tests the API connection and sets the connection status.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        self.set_ip_address()
        self.add_response("Verifying connection to API...")
        api_config = self.get_api_config()
        if not api_config['host'] or not api_config['port']:
            self.add_error("API configuration incomplete", "Host or port not set")
            return
        try:
            import requests
            response = requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/connection")
            response.raise_for_status()
            response_data = response.json()
            if response_data.get('connected'):
                self.api_connected = True
                self.add_response(response_data.get('status', 'Connected successfully'))
            else:
                self.add_response("Connection failed.")
        except Exception as e:
            self.add_error(f"Failed to connect to {api_config['host']}:{api_config['port']}", str(e))

    def search_for_microcontrollers(self, _: Any) -> None:
        """Search for available microcontrollers via the API.

        **Description:**
        - Queries the API for available COM ports and updates the microcontroller menu.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        self.add_response("Searching for microcontrollers...")
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.get(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/serial/comports")
            response_data = response.json()
            ports = response_data.get('ports', [])
            self.add_response(response_data.get('status', 'Ports retrieved'))
            if ports and "No available ports" not in ports:
                self.microcontroller_menu.options = ports
                self.add_response(f"Found {len(ports)} available ports.")
            else:
                self.add_response("No valid COM ports found.")
        except Exception as e:
            self.add_error("Failed to search for microcontrollers", str(e))

    def connect_to_microcontroller(self, _: Any) -> None:
        """Connect to the selected microcontroller via the API.

        **Description:**
        - Sets the COM port and opens a serial connection through the API.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        port = self.microcontroller_menu.value
        if not port:
            self.add_response("Please select a microcontroller.")
            return
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/serial/port", json={'port': port})
            response.raise_for_status()
            self.add_response(response.json().get('status', f'COM port set to {port}'))
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/serial/transmission")
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Serial connection opened'))
        except Exception as e:
            self.add_error(f"Failed to connect to {port}", str(e))

    def disconnect_from_microcontroller(self, _: Any) -> None:
        """Disconnect from the microcontroller via the API.

        **Description:**
        - Closes the serial connection through the API.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Not connected to API.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/serial/termination")
            response.raise_for_status()
            self.add_response(response.json().get('status', 'Serial connection closed'))
        except Exception as e:
            self.add_error("Failed to disconnect from microcontroller", str(e))

    def layout(self) -> pn.Column:
        """Construct the layout for the HomeTab.

        **Description:**
        - Builds the tab-specific UI with API and COM connection controls.

        **Returns:**
        - `pn.Column`: The HomeTab layout.
        """
        server_layout = pn.Column(
            pn.pane.Markdown("### API Connection"),
            self.search_server_button,
            self.server_select,
            self.verify_connection_button
        )
        microcontroller_layout = pn.Column(
            pn.pane.Markdown("### COM Connection"),
            self.microcontroller_menu,
            self.search_microcontrollers_button,
            pn.Row(self.serial_connect_button, self.serial_disconnect_button)
        )
        return pn.Column(server_layout, pn.Spacer(height=50), microcontroller_layout)

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """