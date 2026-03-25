"""Complete command registry mirroring Commands.h in firmware/.

Single source of truth for all serial command codes, their metadata,
and paradigm-specific filtering.
"""

from enum import IntEnum
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class CommandCode(IntEnum):
    """All serial command codes matching reacher-firmware/libraries/REACHERDevices/src/Commands.h."""

    # --- Controller (1xx) ---
    SESSION_END = 100
    SESSION_START = 101
    IDENTIFY = 102
    TEST_CHAIN = 103
    TEST_MODE = 104
    SESSION_PAUSE = 105

    # --- Session Setup (2xx) ---
    SET_RATIO = 201
    SET_PARADIGM = 202
    SET_OMISSION_INTERVAL = 203
    SET_VI_INTERVAL = 204
    SET_PR_STEP = 205

    # Pavlovian parameters (206-219)
    PAV_CS_PLUS_PROB = 206
    PAV_CS_MINUS_PROB = 207
    PAV_CS_PLUS_COUNT = 208
    PAV_CS_MINUS_COUNT = 209
    PAV_CS_PLUS_FREQ = 210
    PAV_CS_MINUS_FREQ = 211
    PAV_COUNTERBALANCE = 212
    PAV_CUE_DURATION = 213
    PAV_TRACE_INTERVAL = 214
    PAV_CONSUMPTION = 215
    PAV_ITI_MEAN = 216
    PAV_ITI_MIN = 217
    PAV_ITI_MAX = 218
    PAV_PULSE_CONFIG = 219

    SET_TRACE_INTERVAL = 220

    # --- Cue (3xx) ---
    CUE_DISARM = 300
    CUE_ARM = 301
    CUE_TEST = 303
    CUE2_DISARM = 310
    CUE2_ARM = 311
    CUE2_TEST = 313
    CUE_SET_FREQUENCY = 371
    CUE_SET_DURATION = 372
    CUE_SET_TRACE = 373  # deprecated
    CUE_SET_PULSE_ON = 374
    CUE_SET_PULSE_OFF = 375
    CUE2_SET_FREQUENCY = 381
    CUE2_SET_DURATION = 382
    CUE2_SET_PULSE_ON = 384
    CUE2_SET_PULSE_OFF = 385

    # --- Pump (4xx) ---
    PUMP_DISARM = 400
    PUMP_ARM = 401
    PUMP_TEST = 403
    PUMP2_DISARM = 410
    PUMP2_ARM = 411
    PUMP2_TEST = 413
    PUMP_SET_DURATION = 472
    PUMP_SET_TRACE = 473  # deprecated
    PUMP2_SET_DURATION = 482

    # --- Lick Circuit (5xx) ---
    LICK_DISARM = 500
    LICK_ARM = 501

    # --- Laser (6xx) ---
    LASER_DISARM = 600
    LASER_ARM = 601
    LASER_TEST = 603
    LASER_SET_FREQUENCY = 671
    LASER_SET_DURATION = 672
    LASER_SET_TRACE = 673  # deprecated
    LASER_MODE_CONTINGENT = 681
    LASER_MODE_INDEPENDENT = 682
    PAV_LASER_CS_PLUS = 691
    PAV_LASER_CS_MINUS = 692
    PAV_LASER_CS_BOTH = 693
    PAV_LASER_PHASE_REWARD = 694
    PAV_LASER_PHASE_CUE = 695

    # --- Microscope (9xx) ---
    MICROSCOPE_DISARM = 900
    MICROSCOPE_ARM = 901
    MICROSCOPE_TEST = 903

    # --- RH Lever (10xx) ---
    LEVER_RH_DISARM = 1000
    LEVER_RH_ARM = 1001
    LEVER_RH_SET_TIMEOUT = 1074
    LEVER_RH_SET_RATIO = 1075
    LEVER_RH_SET_INACTIVE = 1080
    LEVER_RH_SET_ACTIVE = 1081

    # --- LH Lever (13xx) ---
    LEVER_LH_DISARM = 1300
    LEVER_LH_ARM = 1301
    LEVER_LH_SET_TIMEOUT = 1374
    LEVER_LH_SET_RATIO = 1375
    LEVER_LH_SET_INACTIVE = 1380
    LEVER_LH_SET_ACTIVE = 1381


# Paradigm identifiers used throughout the system
PARADIGMS = ("fr", "pr", "vi", "omission", "pavlovian")
ALL_PARADIGMS = list(PARADIGMS)

SCHEDULE_TO_PARADIGM: Dict[str, str] = {
    "FIXED_RATIO": "fr",
    "PROGRESSIVE_RATIO": "pr",
    "VARIABLE_INTERVAL": "vi",
    "OMISSION": "omission",
    "PAVLOVIAN": "pavlovian",
}


@dataclass
class CommandSpec:
    """Specification for a single serial command."""

    code: CommandCode
    name: str
    description: str
    payload_key: Optional[str] = None
    payload_type: Optional[str] = None  # "int", "bool"
    paradigms: List[str] = field(default_factory=lambda: list(ALL_PARADIGMS))
    deprecated: bool = False


# Complete command registry — every command from Commands.h
COMMAND_REGISTRY: Dict[int, CommandSpec] = {
    # --- Controller ---
    100: CommandSpec(
        CommandCode.SESSION_END, "SESSION_END",
        "End the current session",
    ),
    101: CommandSpec(
        CommandCode.SESSION_START, "SESSION_START",
        "Start a new session",
    ),
    102: CommandSpec(
        CommandCode.IDENTIFY, "IDENTIFY",
        "Request firmware identification (*IDN? equivalent)",
    ),
    103: CommandSpec(
        CommandCode.TEST_CHAIN, "TEST_CHAIN",
        "Run full hardware test chain",
    ),
    104: CommandSpec(
        CommandCode.TEST_MODE, "TEST_MODE",
        "Toggle test mode on the controller",
        payload_key="enable", payload_type="bool",
    ),
    105: CommandSpec(
        CommandCode.SESSION_PAUSE, "SESSION_PAUSE",
        "Pause/resume the current session",
        payload_key="paused", payload_type="bool",
    ),

    # --- Session Setup ---
    201: CommandSpec(
        CommandCode.SET_RATIO, "SET_RATIO",
        "Set the fixed/progressive ratio",
        payload_key="ratio", payload_type="int",
        paradigms=["fr", "pr"],
    ),
    202: CommandSpec(
        CommandCode.SET_PARADIGM, "SET_PARADIGM",
        "Set the active paradigm",
        payload_key="paradigm", payload_type="int",
    ),
    203: CommandSpec(
        CommandCode.SET_OMISSION_INTERVAL, "SET_OMISSION_INTERVAL",
        "Set omission interval (ms)",
        payload_key="interval", payload_type="int",
        paradigms=["omission"],
    ),
    204: CommandSpec(
        CommandCode.SET_VI_INTERVAL, "SET_VI_INTERVAL",
        "Set variable interval (ms)",
        payload_key="interval", payload_type="int",
        paradigms=["vi"],
    ),
    205: CommandSpec(
        CommandCode.SET_PR_STEP, "SET_PR_STEP",
        "Set progressive ratio step size",
        payload_key="step", payload_type="int",
        paradigms=["pr"],
    ),

    # --- Pavlovian Parameters ---
    206: CommandSpec(
        CommandCode.PAV_CS_PLUS_PROB, "PAV_CS_PLUS_PROB",
        "CS+ probability (0-100)",
        payload_key="probability", payload_type="int",
        paradigms=["pavlovian"],
    ),
    207: CommandSpec(
        CommandCode.PAV_CS_MINUS_PROB, "PAV_CS_MINUS_PROB",
        "CS- probability (0-100)",
        payload_key="probability", payload_type="int",
        paradigms=["pavlovian"],
    ),
    208: CommandSpec(
        CommandCode.PAV_CS_PLUS_COUNT, "PAV_CS_PLUS_COUNT",
        "Number of CS+ trials",
        payload_key="count", payload_type="int",
        paradigms=["pavlovian"],
    ),
    209: CommandSpec(
        CommandCode.PAV_CS_MINUS_COUNT, "PAV_CS_MINUS_COUNT",
        "Number of CS- trials",
        payload_key="count", payload_type="int",
        paradigms=["pavlovian"],
    ),
    210: CommandSpec(
        CommandCode.PAV_CS_PLUS_FREQ, "PAV_CS_PLUS_FREQ",
        "CS+ tone frequency (Hz)",
        payload_key="frequency", payload_type="int",
        paradigms=["pavlovian"],
    ),
    211: CommandSpec(
        CommandCode.PAV_CS_MINUS_FREQ, "PAV_CS_MINUS_FREQ",
        "CS- tone frequency (Hz)",
        payload_key="frequency", payload_type="int",
        paradigms=["pavlovian"],
    ),
    212: CommandSpec(
        CommandCode.PAV_COUNTERBALANCE, "PAV_COUNTERBALANCE",
        "Enable/disable counterbalancing",
        payload_key="enabled", payload_type="bool",
        paradigms=["pavlovian"],
        deprecated=True,
    ),
    213: CommandSpec(
        CommandCode.PAV_CUE_DURATION, "PAV_CUE_DURATION",
        "Cue presentation duration (ms)",
        payload_key="duration", payload_type="int",
        paradigms=["pavlovian"],
    ),
    214: CommandSpec(
        CommandCode.PAV_TRACE_INTERVAL, "PAV_TRACE_INTERVAL",
        "Trace interval between CS and US (ms)",
        payload_key="interval", payload_type="int",
        paradigms=["pavlovian"],
    ),
    215: CommandSpec(
        CommandCode.PAV_CONSUMPTION, "PAV_CONSUMPTION",
        "Consumption window duration (ms)",
        payload_key="duration", payload_type="int",
        paradigms=["pavlovian"],
        deprecated=True,
    ),
    216: CommandSpec(
        CommandCode.PAV_ITI_MEAN, "PAV_ITI_MEAN",
        "Mean inter-trial interval (ms)",
        payload_key="iti_mean", payload_type="int",
        paradigms=["pavlovian"],
    ),
    217: CommandSpec(
        CommandCode.PAV_ITI_MIN, "PAV_ITI_MIN",
        "Minimum inter-trial interval (ms)",
        payload_key="iti_min", payload_type="int",
        paradigms=["pavlovian"],
    ),
    218: CommandSpec(
        CommandCode.PAV_ITI_MAX, "PAV_ITI_MAX",
        "Maximum inter-trial interval (ms)",
        payload_key="iti_max", payload_type="int",
        paradigms=["pavlovian"],
    ),
    219: CommandSpec(
        CommandCode.PAV_PULSE_CONFIG, "PAV_PULSE_CONFIG",
        "Pulse configuration for Pavlovian paradigm",
        payload_key="config", payload_type="int",
        paradigms=["pavlovian"],
        deprecated=True,
    ),
    220: CommandSpec(
        CommandCode.SET_TRACE_INTERVAL, "SET_TRACE_INTERVAL",
        "Set trace interval (ms)",
        payload_key="interval", payload_type="int",
    ),

    # --- Cue ---
    300: CommandSpec(
        CommandCode.CUE_DISARM, "CUE_DISARM",
        "Disarm primary cue speaker",
    ),
    301: CommandSpec(
        CommandCode.CUE_ARM, "CUE_ARM",
        "Arm primary cue speaker",
    ),
    303: CommandSpec(
        CommandCode.CUE_TEST, "CUE_TEST",
        "Test primary cue speaker",
    ),
    310: CommandSpec(
        CommandCode.CUE2_DISARM, "CUE2_DISARM",
        "Disarm secondary cue speaker",
    ),
    311: CommandSpec(
        CommandCode.CUE2_ARM, "CUE2_ARM",
        "Arm secondary cue speaker",
    ),
    313: CommandSpec(
        CommandCode.CUE2_TEST, "CUE2_TEST",
        "Test secondary cue speaker",
    ),
    371: CommandSpec(
        CommandCode.CUE_SET_FREQUENCY, "CUE_SET_FREQUENCY",
        "Set primary cue frequency (Hz)",
        payload_key="frequency", payload_type="int",
    ),
    372: CommandSpec(
        CommandCode.CUE_SET_DURATION, "CUE_SET_DURATION",
        "Set primary cue duration (ms)",
        payload_key="duration", payload_type="int",
    ),
    373: CommandSpec(
        CommandCode.CUE_SET_TRACE, "CUE_SET_TRACE",
        "Set primary cue trace interval (deprecated)",
        payload_key="interval", payload_type="int",
        deprecated=True,
    ),
    374: CommandSpec(
        CommandCode.CUE_SET_PULSE_ON, "CUE_SET_PULSE_ON",
        "Set CS+ pulse ON duration (ms); 0 = continuous",
        payload_key="pulse_on", payload_type="int",
        paradigms=["pavlovian"],
    ),
    375: CommandSpec(
        CommandCode.CUE_SET_PULSE_OFF, "CUE_SET_PULSE_OFF",
        "Set CS+ pulse OFF duration (ms)",
        payload_key="pulse_off", payload_type="int",
        paradigms=["pavlovian"],
    ),
    381: CommandSpec(
        CommandCode.CUE2_SET_FREQUENCY, "CUE2_SET_FREQUENCY",
        "Set secondary cue frequency (Hz)",
        payload_key="frequency", payload_type="int",
    ),
    382: CommandSpec(
        CommandCode.CUE2_SET_DURATION, "CUE2_SET_DURATION",
        "Set secondary cue duration (ms)",
        payload_key="duration", payload_type="int",
    ),
    384: CommandSpec(
        CommandCode.CUE2_SET_PULSE_ON, "CUE2_SET_PULSE_ON",
        "Set CS- pulse ON duration (ms); 0 = continuous",
        payload_key="pulse_on", payload_type="int",
        paradigms=["pavlovian"],
    ),
    385: CommandSpec(
        CommandCode.CUE2_SET_PULSE_OFF, "CUE2_SET_PULSE_OFF",
        "Set CS- pulse OFF duration (ms)",
        payload_key="pulse_off", payload_type="int",
        paradigms=["pavlovian"],
    ),

    # --- Pump ---
    400: CommandSpec(
        CommandCode.PUMP_DISARM, "PUMP_DISARM",
        "Disarm primary pump",
    ),
    401: CommandSpec(
        CommandCode.PUMP_ARM, "PUMP_ARM",
        "Arm primary pump",
    ),
    403: CommandSpec(
        CommandCode.PUMP_TEST, "PUMP_TEST",
        "Test primary pump",
    ),
    410: CommandSpec(
        CommandCode.PUMP2_DISARM, "PUMP2_DISARM",
        "Disarm secondary pump",
    ),
    411: CommandSpec(
        CommandCode.PUMP2_ARM, "PUMP2_ARM",
        "Arm secondary pump",
    ),
    413: CommandSpec(
        CommandCode.PUMP2_TEST, "PUMP2_TEST",
        "Test secondary pump",
    ),
    472: CommandSpec(
        CommandCode.PUMP_SET_DURATION, "PUMP_SET_DURATION",
        "Set primary pump duration (ms)",
        payload_key="duration", payload_type="int",
    ),
    473: CommandSpec(
        CommandCode.PUMP_SET_TRACE, "PUMP_SET_TRACE",
        "Set primary pump trace interval (deprecated)",
        payload_key="interval", payload_type="int",
        deprecated=True,
    ),
    482: CommandSpec(
        CommandCode.PUMP2_SET_DURATION, "PUMP2_SET_DURATION",
        "Set secondary pump duration (ms)",
        payload_key="duration", payload_type="int",
    ),

    # --- Lick Circuit ---
    500: CommandSpec(
        CommandCode.LICK_DISARM, "LICK_DISARM",
        "Disarm lick detection circuit",
    ),
    501: CommandSpec(
        CommandCode.LICK_ARM, "LICK_ARM",
        "Arm lick detection circuit",
    ),

    # --- Laser ---
    600: CommandSpec(
        CommandCode.LASER_DISARM, "LASER_DISARM",
        "Disarm optogenetic laser",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    601: CommandSpec(
        CommandCode.LASER_ARM, "LASER_ARM",
        "Arm optogenetic laser",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    603: CommandSpec(
        CommandCode.LASER_TEST, "LASER_TEST",
        "Test optogenetic laser",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    671: CommandSpec(
        CommandCode.LASER_SET_FREQUENCY, "LASER_SET_FREQUENCY",
        "Set laser frequency (Hz)",
        payload_key="frequency", payload_type="int",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    672: CommandSpec(
        CommandCode.LASER_SET_DURATION, "LASER_SET_DURATION",
        "Set laser pulse duration (ms)",
        payload_key="duration", payload_type="int",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    673: CommandSpec(
        CommandCode.LASER_SET_TRACE, "LASER_SET_TRACE",
        "Set laser trace interval (deprecated)",
        payload_key="interval", payload_type="int",
        paradigms=["fr", "pr", "vi", "omission"],
        deprecated=True,
    ),
    681: CommandSpec(
        CommandCode.LASER_MODE_CONTINGENT, "LASER_MODE_CONTINGENT",
        "Set laser to contingent mode (triggered by lever press)",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    682: CommandSpec(
        CommandCode.LASER_MODE_INDEPENDENT, "LASER_MODE_INDEPENDENT",
        "Set laser to independent mode (free-running)",
        paradigms=["fr", "pr", "vi", "omission", "pavlovian"],
    ),
    691: CommandSpec(
        CommandCode.PAV_LASER_CS_PLUS, "PAV_LASER_CS_PLUS",
        "Set Pavlovian laser to CS+ trials only",
        paradigms=["pavlovian"],
    ),
    692: CommandSpec(
        CommandCode.PAV_LASER_CS_MINUS, "PAV_LASER_CS_MINUS",
        "Set Pavlovian laser to CS- trials only",
        paradigms=["pavlovian"],
    ),
    693: CommandSpec(
        CommandCode.PAV_LASER_CS_BOTH, "PAV_LASER_CS_BOTH",
        "Set Pavlovian laser to both trial types",
        paradigms=["pavlovian"],
    ),
    694: CommandSpec(
        CommandCode.PAV_LASER_PHASE_REWARD, "PAV_LASER_PHASE_REWARD",
        "Set Pavlovian laser to fire during reward phase",
        paradigms=["pavlovian"],
    ),
    695: CommandSpec(
        CommandCode.PAV_LASER_PHASE_CUE, "PAV_LASER_PHASE_CUE",
        "Set Pavlovian laser to fire during cue phase",
        paradigms=["pavlovian"],
    ),

    # --- Microscope ---
    900: CommandSpec(
        CommandCode.MICROSCOPE_DISARM, "MICROSCOPE_DISARM",
        "Disarm microscope sync",
    ),
    901: CommandSpec(
        CommandCode.MICROSCOPE_ARM, "MICROSCOPE_ARM",
        "Arm microscope sync",
    ),
    903: CommandSpec(
        CommandCode.MICROSCOPE_TEST, "MICROSCOPE_TEST",
        "Test microscope sync",
    ),

    # --- RH Lever ---
    1000: CommandSpec(
        CommandCode.LEVER_RH_DISARM, "LEVER_RH_DISARM",
        "Disarm right-hand lever",
    ),
    1001: CommandSpec(
        CommandCode.LEVER_RH_ARM, "LEVER_RH_ARM",
        "Arm right-hand lever",
    ),
    1074: CommandSpec(
        CommandCode.LEVER_RH_SET_TIMEOUT, "LEVER_RH_SET_TIMEOUT",
        "Set right-hand lever timeout (ms)",
        payload_key="timeout", payload_type="int",
        paradigms=["fr", "pr", "vi", "omission"],
    ),
    1075: CommandSpec(
        CommandCode.LEVER_RH_SET_RATIO, "LEVER_RH_SET_RATIO",
        "Set right-hand lever ratio",
        payload_key="ratio", payload_type="int",
        paradigms=["fr", "pr"],
    ),
    1080: CommandSpec(
        CommandCode.LEVER_RH_SET_INACTIVE, "LEVER_RH_SET_INACTIVE",
        "Set right-hand lever to inactive",
    ),
    1081: CommandSpec(
        CommandCode.LEVER_RH_SET_ACTIVE, "LEVER_RH_SET_ACTIVE",
        "Set right-hand lever to active",
    ),

    # --- LH Lever ---
    1300: CommandSpec(
        CommandCode.LEVER_LH_DISARM, "LEVER_LH_DISARM",
        "Disarm left-hand lever",
    ),
    1301: CommandSpec(
        CommandCode.LEVER_LH_ARM, "LEVER_LH_ARM",
        "Arm left-hand lever",
    ),
    1374: CommandSpec(
        CommandCode.LEVER_LH_SET_TIMEOUT, "LEVER_LH_SET_TIMEOUT",
        "Set left-hand lever timeout (ms)",
        payload_key="timeout", payload_type="int",
        paradigms=["fr", "pr", "vi", "omission"],
    ),
    1375: CommandSpec(
        CommandCode.LEVER_LH_SET_RATIO, "LEVER_LH_SET_RATIO",
        "Set left-hand lever ratio",
        payload_key="ratio", payload_type="int",
        paradigms=["fr", "pr"],
    ),
    1380: CommandSpec(
        CommandCode.LEVER_LH_SET_INACTIVE, "LEVER_LH_SET_INACTIVE",
        "Set left-hand lever to inactive",
    ),
    1381: CommandSpec(
        CommandCode.LEVER_LH_SET_ACTIVE, "LEVER_LH_SET_ACTIVE",
        "Set left-hand lever to active",
    ),
}


def get_commands_for_paradigm(paradigm: str) -> Dict[int, CommandSpec]:
    """Return only the commands applicable to a given paradigm."""
    paradigm = paradigm.lower()
    return {
        code: spec
        for code, spec in COMMAND_REGISTRY.items()
        if paradigm in spec.paradigms and not spec.deprecated
    }


def build_command_payload(code: int, value=None) -> dict:
    """Build a JSON-serializable command dict for sending over serial.

    Returns e.g. {"cmd": 371, "frequency": 8000} or {"cmd": 101}.
    """
    spec = COMMAND_REGISTRY.get(code)
    if spec is None:
        raise ValueError(f"Unknown command code: {code}")
    payload = {"cmd": code}
    if value is not None and spec.payload_key is not None:
        if spec.payload_type == "bool":
            payload[spec.payload_key] = bool(value)
        else:
            payload[spec.payload_key] = value
    return payload
