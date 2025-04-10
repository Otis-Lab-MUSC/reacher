import panel as pn
from typing import Any, Optional
from reacher.kernel.reacher import REACHER
import time

class Dashboard:
    """A base class for managing the REACHER experiment dashboard UI."""

    def __init__(self, reacher: Optional[REACHER] = None) -> None:
        """Initialize the Dashboard with REACHER integration and core UI components.

        **Description:**
        - Sets up the base dashboard with a REACHER instance, header, response terminal, and tab infrastructure.
        - Serves as a superclass for tab-specific classes.

        **Attributes:**
        - `reacher (REACHER)`: The REACHER instance for hardware communication.
        - `header (pn.pane.Alert)`: Displays program status.
        - `response_textarea (pn.pane.HTML)`: Terminal for output messages.
        - `toggle_button (pn.widgets.Button)`: Toggles response terminal visibility.
        - `reset_button (pn.widgets.Button)`: Resets the session.
        - `dashboard (pn.Tabs)`: Container for tabbed interface (set by subclasses).
        """
        super().__init__()
        self.reacher = reacher if reacher is not None else REACHER()
        self.header: pn.pane.Alert = pn.pane.Alert("Program not started...", alert_type="info")
        self.response_textarea: pn.pane.HTML = pn.pane.HTML(
            "REACHER Output:<br><br>",
            styles={"background-color": "#1e1e1e", "color": "white"},
            width=450,
            height=600,
            visible=True
        )
        self.toggle_button: pn.widgets.Button = pn.widgets.Button(name="Hide Response", button_type="primary")
        self.toggle_button.on_click(self.toggle_response_visibility)
        self.reset_button: pn.widgets.Button = pn.widgets.Button(name="Reset", icon="reset", button_type="danger")
        self.reset_button.on_click(self.reset_session)
        self.dashboard: pn.Tabs = None 

    def layout(self) -> pn.Column:
        """Construct the main layout of the dashboard.

        **Description:**
        - Assembles the core UI with a header, tabs, and response area.
        - Must be called after tabs are initialized by a subclass.

        **Returns:**
        - `pn.Column`: The complete dashboard layout.
        """
        if self.dashboard is None:
            raise ValueError("Dashboard tabs must be initialized before calling layout.")
        header_row = pn.Row(self.header, self.toggle_button)
        main_row = pn.Row(self.dashboard, self.response_textarea)
        return pn.Column(header_row, main_row, self.reset_button)

    def get_response_terminal(self) -> pn.pane.HTML:
        """Get the response terminal pane.

        **Description:**
        - Provides access to the HTML pane for output messages.

        **Returns:**
        - `pn.pane.HTML`: The response terminal pane.
        """
        return self.response_textarea

    def add_response(self, response: str) -> None:
        """Add a response message to the response terminal.

        **Description:**
        - Appends a timestamped message to the response terminal in cyan.

        **Args:**
        - `response (str)`: The message to display.
        """
        local_time = time.localtime()
        formatted_time = time.strftime("%H:%M:%S", local_time)
        writeout = f"""
        <span style="color: cyan;">>>></span>
        <span style="color: grey;"> [{formatted_time}]:</span>
        <span style="color: white;"> {response}</span><br>
        """
        self.response_textarea.object += writeout

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
        writeout = f"""
        <span style="color: red;">>>></span>
        <span style="color: grey;"> [{formatted_time}]:</span>
        <span style="color: red; font-weight: bold;"> !!!ERROR!!!</span>
        <span style="color: white;"> {response}</span><br>
        <span style="color: grey;">     Details - {details}</span><br>
        """
        self.response_textarea.object += writeout

    def toggle_response_visibility(self, event: Any) -> None:
        """Toggle the visibility of the response_textarea and update button label.

        **Description:**
        - Shows or hides the response terminal and updates the toggle button text.

        **Args:**
        - `event (Any)`: The event object from the button click.
        """
        if self.response_textarea.visible:
            self.response_textarea.visible = False
            self.toggle_button.name = "Show Response"
        else:
            self.response_textarea.visible = True
            self.toggle_button.name = "Hide Response"

    def reset_session(self, _: Any) -> None:
        """Reset the REACHER session.

        **Description:**
        - Resets the REACHER instance and logs the action.

        **Args:**
        - `_ (Any)`: Unused event argument.
        """
        try:
            self.reacher.reset()
            self.add_response("Session reset.")
        except Exception as e:
            self.add_error("Failed to reset session.", str(e))

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """