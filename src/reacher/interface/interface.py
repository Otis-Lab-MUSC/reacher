import panel as pn
from .dashboard import Dashboard
from .home_tab import HomeTab
from .program_tab import ProgramTab
from .hardware_tab import HardwareTab
from .monitor_tab import MonitorTab
from .schedule_tab import ScheduleTab
from reacher.kernel import REACHER

class Interface(Dashboard):
    """A class to instantiate and manage all REACHER dashboard tabs with UI components."""

    def __init__(self, behavior_chamber: str) -> None:
        """Initialize the Interface with all tab instances and UI components.

        **Description:**
        - Extends Dashboard to create and organize all tab components.
        - Sets up the tabbed interface with shared UI components (header, response_textarea, etc.).
        """
        super().__init__()
        self.behavior_chamber = behavior_chamber
        self.header = pn.pane.Alert("Program not started...", alert_type="info")
        self.response_textarea = pn.pane.HTML(
            "REACHER Output:<br><br>",
            styles={"background-color": "#1e1e1e", "color": "white"},
            width=450,
            height=600,
            visible=True
        )
        self.toggle_button = pn.widgets.Button(name="Hide Response", button_type="primary")
        self.toggle_button.on_click(self.toggle_response_visibility)
        self.reset_button = pn.widgets.Button(name="Reset", icon="reset", button_type="danger")
        self.reset_button.on_click(self.reset)

        self.reacher = REACHER()
        self.reacher.set_box_name(self.behavior_chamber)
        self.home_tab = HomeTab(self.reacher, self.response_textarea)
        self.program_tab = ProgramTab(self.reacher, self.response_textarea)
        self.hardware_tab = HardwareTab(self.reacher, self.response_textarea)
        self.schedule_tab = ScheduleTab(self.reacher, self.response_textarea)
        self.monitor_tab = MonitorTab(
            reacher=self.reacher,
            program_tab=self.program_tab,
            hardware_tab=self.hardware_tab,
            schedule_tab=self.schedule_tab,
            response_textarea=self.response_textarea,
            header=self.header
        )
        self.tabs = [self.home_tab, self.program_tab, self.hardware_tab, self.monitor_tab, self.schedule_tab]

        self.dashboard = pn.Tabs(
            ("Home", self.home_tab.layout()),
            ("Program", self.program_tab.layout()),
            ("Hardware", self.hardware_tab.layout()),
            ("Schedule", self.schedule_tab.layout()),
            ("Monitor", self.monitor_tab.layout()),
            tabs_location="left",
        )

    def reset(self, _) -> None:
        for tab in self.tabs:
            if hasattr(tab, "reset"):
                tab.reset()
        self.reacher = REACHER()
        self.reacher.set_box_name(self.behavior_chamber)
        self.home_tab = HomeTab(self.reacher, self.response_textarea)
        self.program_tab = ProgramTab(self.reacher, self.response_textarea)
        self.hardware_tab = HardwareTab(self.reacher, self.response_textarea)
        self.schedule_tab = ScheduleTab(self.reacher, self.response_textarea)
        self.monitor_tab = MonitorTab(
            reacher=self.reacher,
            program_tab=self.program_tab,
            hardware_tab=self.hardware_tab,
            schedule_tab=self.schedule_tab,
            response_textarea=self.response_textarea,
            header=self.header
        )
        self.header.alert_type = "info"
        self.header.object = "Program not started..."
        self.response_textarea.object = "REACHER Output:<br><br>"
        self.response_textarea.styles = {"background-color": "#1e1e1e", "color": "white"}
        self.response_textarea.visible = True
    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """