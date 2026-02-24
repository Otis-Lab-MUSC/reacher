"""Board profile registry for supported Arduino boards.

Maps board identifiers to avrdude parameters and Arduino CLI FQBNs.
Adding a new board requires only a new entry in BOARD_PROFILES.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


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

DEFAULT_BOARD = "uno"
SUPPORTED_BOARDS: Tuple[str, ...] = tuple(BOARD_PROFILES.keys())


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
