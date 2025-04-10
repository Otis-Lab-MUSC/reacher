import panel as pn
from typing import Any, Dict
import time

class Dashboard:
    """A base class for managing the REACHER wireless experiment interface."""

    def __init__(self) -> None:
        """Initialize the Dashboard with core UI components and API configuration.

        **Description:**
        - Sets up the base interface with a header, response terminal, and API connection state.
        - Serves as a superclass for tab-specific classes.

        **Attributes:**
        - `header (pn.pane.Alert)`: Displays program status.
        - `response_html (pn.pane.HTML)`: HTML pane for output messages.
        - `response_textarea (pn.Column)`: Scrollable container for response output.
        - `toggle_button (pn.widgets.Button)`: Toggles response terminal visibility.
        - `reset_button (pn.widgets.Button)`: Resets the session.
        - `api_config (Dict[str, Any])`: API connection details (host, port, key).
        - `api_connected (bool)`: Tracks API connection status.
        - `tabs (pn.Tabs)`: Container for tabbed interface (set by subclasses).
        """
        self.header: pn.pane.Alert = pn.pane.Alert("Program not started...", alert_type="info")
        self.response_html: pn.pane.HTML = pn.pane.HTML(
            "REACHER Output:<br><br>",
            styles={
                "background-color": "#1e1e1e",
                "color": "white",
                "white-space": "pre-wrap",
                "padding": "10px",
            },
            width=450,
        )
        self.response_textarea: pn.Column = pn.Column(
            self.response_html,
            scroll=True,
            height=600,
            width=450,
            visible=True
        )
        self.toggle_button: pn.widgets.Button = pn.widgets.Button(name="Hide Response", button_type="primary")
        self.toggle_button.on_click(self.toggle_response_visibility)
        self.reset_button: pn.widgets.Button = pn.widgets.Button(name="Reset", icon="reset", button_type="danger")
        self.reset_button.on_click(self.reset_session)
        self.api_config: Dict[str, Any] = {"host": None, "port": None, "key": None}
        self.api_connected: bool = False
        self.tabs: pn.Tabs = None  # To be set by subclass like WirelessDashboard

    def layout(self) -> pn.Column:
        """Construct the main layout of the interface.

        **Description:**
        - Assembles the core UI with a header, tabs, and response area.
        - Must be called after tabs are initialized by a subclass.

        **Returns:**
        - `pn.Column`: The complete interface layout.
        """
        if self.tabs is None:
            raise ValueError("Dashboard tabs must be initialized before calling layout.")
        header_row = pn.Row(self.header, self.toggle_button)
        main_row = pn.Row(self.tabs, self.response_textarea)
        return pn.Column(header_row, main_row, self.reset_button)

    def get_api_config(self) -> Dict[str, Any]:
        """Get the current API configuration.

        **Description:**
        - Returns the API configuration dictionary.

        **Returns:**
        - `Dict[str, Any]`: API configuration with host, port, and key.
        """
        return self.api_config

    def set_api_config(self, config: Dict[str, Any]) -> None:
        """Set the API configuration.

        **Description:**
        - Updates the API configuration with provided values.

        **Args:**
        - `config (Dict[str, Any])`: Dictionary containing host, port, and key.
        """
        self.api_config = config

    def add_response(self, response: str) -> None:
        """Add a response message to the response terminal.

        **Description:**
        - Appends a timestamped message to the response terminal in cyan.

        **Args:**
        - `response (str)`: The message to display.
        """
        local_time = time.localtime()
        formatted_time = time.strftime("%H:%M:%S", local_time)
        writeout = f"""<span style="color: cyan;">>>></span><span style="color: grey;"> [{formatted_time}]:</span><span style="color: white;"> {response}</span><br>"""
        self.response_html.object += writeout

    def add_error(self, response: str, details: str) -> None:
        """Add an error message to the response terminal.

        **Description:**
        - Appends a timestamped error message with details in red.

        **Args:**
        - `response (str)`: The error message.
        - `details (str)`: Additional details about the error.
        """
        local_time = time.localtime()
        formatted_time = time.strftime("%H:%M:%S", local_time)
        writeout = f"""<span style="color: red;">>>></span><span style="color: grey;"> [{formatted_time}]:</span><span style="color: red; font-weight: bold;"> !!!ERROR!!!</span><span style="color: white;"> {response}</span><br><span style="color: grey;">     Details - {details}</span><br>"""
        self.response_html.object += writeout

    def toggle_response_visibility(self, event: Any) -> None:
        """Toggle the visibility of the response_textarea and update button label.

        **Description:**
        - Shows or hides the response terminal and updates the toggle button text.

        **Args:**
        - `event (Any)`: The event object from the button click.
        """
        self.response_textarea.visible = not self.response_textarea.visible
        self.toggle_button.name = "Show Responses" if not self.response_textarea.visible else "Hide Responses"

    def reset_session(self, _: Any) -> None:
        """Reset the REACHER session via the API.

        **Description:**
        - Sends a reset request to the API and logs the action.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        if not self.api_connected:
            self.add_response("Please connect to the API first.")
            return
        api_config = self.get_api_config()
        try:
            import requests
            response = requests.post(timeout=5, url=f"http://{api_config['host']}:{api_config['port']}/reset")
            response.raise_for_status()
            response_data = response.json()
            self.add_response(response_data.get('status', 'Session reset.'))
        except Exception as e:
            self.add_error("Failed to reset session.", str(e))

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """