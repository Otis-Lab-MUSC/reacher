"""Board profile registry for supported Arduino boards.

Maps board identifiers to avrdude parameters and Arduino CLI FQBNs.
Adding a new board requires only a new entry in BOARD_PROFILES.
"""

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from serial.tools import list_ports


@dataclass(frozen=True)
class BoardProfile:
    """Hardware profile for a specific Arduino board."""

    board_id: str
    display_name: str
    fqbn: str
    avrdude_args: Tuple[str, ...]


BOARD_PROFILES: Dict[str, BoardProfile] = {
    "uno": BoardProfile(
        board_id="uno",
        display_name="Arduino UNO",
        fqbn="arduino:avr:uno",
        avrdude_args=("-p", "atmega328p", "-c", "arduino", "-b", "115200"),
    ),
    "mega": BoardProfile(
        board_id="mega",
        display_name="Arduino MEGA 2560",
        fqbn="arduino:avr:mega:cpu=atmega2560",
        avrdude_args=("-p", "atmega2560", "-c", "wiring", "-D", "-b", "115200"),
    ),
}

_USB_ID_MAP: Dict[Tuple[int, int], str] = {
    (0x2341, 0x0043): "uno",
    (0x2341, 0x0001): "uno",
    (0x2A03, 0x0043): "uno",
    (0x2341, 0x0042): "mega",
    (0x2341, 0x0010): "mega",
    (0x2A03, 0x0042): "mega",
}

# The hardware-free port every session may select (see kernel.simulator).
# "SIMULATOR" is the generic entry offered in the port list; the session manager
# binds each session that picks it to its own numbered instance (SIM1, SIM2, ...)
# so several simulated rigs can run side by side in one application.
SIMULATOR_PORT = "SIMULATOR"
_SIM_INSTANCE_RE = re.compile(r"^SIM[1-9][0-9]*$")


def is_simulator_port(port: Optional[str]) -> bool:
    """True for the generic ``SIMULATOR`` entry and any numbered ``SIMn`` instance."""
    return port == SIMULATOR_PORT or bool(port and _SIM_INSTANCE_RE.match(port))

DEFAULT_BOARD = "mega"
SUPPORTED_BOARDS: Tuple[str, ...] = tuple(BOARD_PROFILES.keys())


def detect_board_from_port(port_device: str) -> Optional[str]:
    """Detect the board type from a serial port's USB VID/PID.

    Returns the board identifier (e.g. ``"uno"``) or ``None`` if the
    port is a simulator, uses a clone chip, or is unrecognized.
    """
    if is_simulator_port(port_device):
        return None
    for port_info in list_ports.comports():
        if port_info.device == port_device and port_info.vid and port_info.pid:
            return _USB_ID_MAP.get((port_info.vid, port_info.pid))
    return None


def get_board_profile(board: str) -> BoardProfile:
    """Look up a board profile by identifier.

    Args:
        board: Case-insensitive board identifier (e.g. ``"uno"``, ``"MEGA"``).

    Returns:
        The matching ``BoardProfile``.

    Raises:
        ValueError: If *board* is not a supported board type.
    """
    key = board.lower()
    try:
        return BOARD_PROFILES[key]
    except KeyError:
        raise ValueError(
            f"Unknown board: {board!r}. Supported boards: {SUPPORTED_BOARDS}"
        )
