import panel as pn
pn.extension('plotly')
from .dashboard import Dashboard
from .home_tab import HomeTab
from .program_tab import ProgramTab
from .hardware_tab import HardwareTab
from .monitor_tab import MonitorTab
from .schedule_tab import ScheduleTab

class Interface(Dashboard):
    """A class to instantiate and manage all wireless REACHER dashboard tabs, inheriting from Dashboard."""

    def __init__(self) -> None:
        """Initialize the WirelessDashboard with all tab instances.

        **Description:**
        - Extends Dashboard to create and organize all tab components.
        - Sets up the tabbed interface with Home, Program, Hardware, Monitor, and Schedule tabs.
        - Links MonitorTab with ProgramTab and HardwareTab for inter-tab dependencies.
        """
        super().__init__()
        self.home_tab: HomeTab = HomeTab()
        self.program_tab: ProgramTab = ProgramTab()
        self.hardware_tab: HardwareTab = HardwareTab()
        self.monitor_tab: MonitorTab = MonitorTab()
        self.schedule_tab: ScheduleTab = ScheduleTab()
        self.monitor_tab.program_tab = self.program_tab
        self.monitor_tab.hardware_tab = self.hardware_tab
        self.tabs: pn.Tabs = pn.Tabs(
            ("Home", self.home_tab.layout()),
            ("Program", self.program_tab.layout()),
            ("Hardware", self.hardware_tab.layout()),
            ("Monitor", self.monitor_tab.layout()),
            ("Schedule", self.schedule_tab.layout()),
            tabs_location="left",
        )

    """
    **Contact:**
    - For inquiries or support, please email: [thejoshbq@proton.me](mailto:thejoshbq@proton.me).
    """