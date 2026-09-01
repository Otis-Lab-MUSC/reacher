"""Machine-readable export of every REACHER registry, for external tooling.

The MCP server (``reacher.mcp``) and the parity tests both need one authoritative
view of "what does this checkout declare" spanning two layers: the Python
registries (commands, pins, boards, paradigms) and the firmware source that
mirrors them (``Commands.h``, ``Pins.h``, the sketch tree).

This module is the *only* place those firmware files are parsed. Everything that
needs them — ``tests/test_command_parity.py``, the cross-repo checks, the MCP
tools — goes through here, so a reformat of ``Commands.h`` breaks one parser
rather than three.

Run it against a working tree, not an installed wheel::

    PYTHONPATH=<checkout>/src python -m reacher.schema dump --json

``PYTHONPATH`` precedes site-packages, so the checkout's registries win over any
installed ``reacher2p``. That precedence is load-bearing: a consumer that
imported the installed package while the user edited a checkout would report
stale codes and pass checks against the wrong tree, silently.

Discipline: pure read and serialize. No network, no subprocess except ``git``
for provenance, no writes anywhere (in particular not to ``~/.reacher``). Same
contract as ``reacher.issues.prefill``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# Bump only on a breaking change to the emitted shape. Consumers pin a range and
# refuse to run outside it rather than misreading a newer dump.
SCHEMA_VERSION = 1

# --- Sanity floors -------------------------------------------------------
#
# Every parser asserts a minimum yield. A regex that silently stops matching
# after someone reformats a header must FAIL, not report "everything is in
# sync" — a check that cannot fail is not a check.
MIN_COMMANDS = 50
MIN_PIN_CONSTANTS = 8
MIN_COMPONENTS = 8
MIN_SKETCHES = 5


class SchemaError(RuntimeError):
    """A parser produced implausibly little output, or a tree is malformed."""


# --- Firmware source parsing ---------------------------------------------

_CONSTEXPR_INT_RE = re.compile(r"^\s*constexpr\s+int\s+(\w+)\s*=\s*(\d+)\s*;", re.MULTILINE)
_CONSTEXPR_PIN_RE = re.compile(r"^\s*constexpr\s+int8_t\s+(\w+)\s*=\s*(\d+)\s*;", re.MULTILINE)
_CMD_REF_RE = re.compile(r"\bCmd::(\w+)\b")

# Firmware ``PIN_*`` symbol -> the canonical component key used by
# ``pin_overrides.PIN_CONSTRAINTS``. The mapping is not derivable: the firmware
# spells the lick circuit ``PIN_LICK_CIRCUIT`` and the backend calls it ``lick``,
# and the two cue/pump pairs use ``_2`` where the backend uses ``2``.
#
# ``PIN_MICROSCOPE_TS`` is deliberately absent — the microscope timestamp pin is
# fixed at INT0 in firmware and must never be exposed as remappable.
PIN_SYMBOL_TO_COMPONENT: dict[str, str] = {
    "PIN_LEVER_RH": "lever_rh",
    "PIN_LEVER_LH": "lever_lh",
    "PIN_CUE": "cue",
    "PIN_CUE_2": "cue2",
    "PIN_PUMP": "pump",
    "PIN_PUMP_2": "pump2",
    "PIN_LICK_CIRCUIT": "lick",
    "PIN_LASER": "laser",
    "PIN_MICROSCOPE_TRIG": "microscope_trigger",
    "PIN_SLM_TS": "slm",
}

# Pins fixed in firmware, with the reason. Consumers surface these so a user is
# told *why* a pin is not offered rather than finding it silently missing.
FIXED_PINS: dict[str, str] = {
    "PIN_MICROSCOPE_TS": "microscope frame timestamp is wired to INT0; the ISR is not remappable",
}

#: How ``REACHER.update_behavioral_events`` rewrites firmware level-007 device
#: names before emitting them over the API. Everything downstream of the kernel
#: — the WebSocket stream, the frontend stores, the event timeline — sees the
#: *rewritten* name, never the firmware one.
#:
#: This distinction is load-bearing in both directions. Validating the frontend
#: against the raw 007 namespace would flag ``LICK`` and ``LEVER_RH`` as wrong
#: when they are right; validating the kernel against the post-kernel set would
#: miss a firmware name the kernel fails to handle. Two contracts:
#:
#:   raw namespaces  -> validate reacher against firmware
#:   post_kernel     -> validate anything downstream against reacher
POST_KERNEL_EVENT_REWRITES: dict[str, tuple[str, ...]] = {
    # Legacy pre-v2.4.x firmware sends one SWITCH_LEVER with an orientation
    # field; the kernel splits it into the two modern names.
    "SWITCH_LEVER": ("LEVER_RH", "LEVER_LH"),
    # Level 007 spells the lick circuit LICK_CIRCUIT; level 000 spells it LICK.
    # The kernel canonicalizes on the level-000 spelling.
    "LICK_CIRCUIT": ("LICK",),
}

#: Which firmware log levels feed the kernel's ``"event"`` WebSocket stream, and
#: how. Keyed by the level, mirroring ``REACHER.code_dict``.
#:
#: This exists because "the event namespace" is NOT "whatever firmware prints at
#: level 007". Level 009 (SLM timestamps) is handled by ``update_slm_events``,
#: which *synthesizes* an event with ``device: "SLM"`` — a name that appears at
#: no 007 print site anywhere in firmware. Deriving the downstream contract from
#: firmware print sites therefore misses it, and reports a correct frontend table
#: as drifted.
#:
#: The general rule, learned the hard way three times: model a contract from what
#: the *consumer's producer* emits, never from a partial view of the producer's
#: own source. ``tests/test_device_names.py`` asserts this covers every handler in
#: ``code_dict``, so a future level fails loudly until someone classifies it.
#:
#: ``"firmware"`` — takes the level's firmware names, with rewrites applied.
#: ``frozenset``  — the handler synthesizes these names itself.
#: ``None``       — the handler does not emit into the ``"event"`` stream.
EVENT_STREAM_CONTRIBUTORS: dict[str, Any] = {
    "000": None,  # update_firmware_information -> "config"
    "001": None,  # logged only
    "006": None,  # handle_firmware_error -> "error"
    "007": "firmware",  # update_behavioral_events, with POST_KERNEL_EVENT_REWRITES
    "008": None,  # update_frame_events -> "frame", a separate WS message type
    "009": frozenset({"SLM"}),  # update_slm_events synthesizes device "SLM"
}

#: Commands that COMMAND_REGISTRY declares for a paradigm whose sketch does not
#: handle them, split by *why*. Exported rather than kept in the test suite so a
#: downstream consumer can derive a UI gate from it instead of hand-writing one —
#: a hand-written gate is one more table to drift, and it would silently outlive
#: the gap the day firmware implements the command.
#:
#: INTENTIONAL: correct as-is; the declaration is broad but firmware has nothing
#: to do. GAPS: real defects — the command is offered and silently dropped.
INTENTIONALLY_UNHANDLED: dict[str, dict[str, Any]] = {
    "SET_PARADIGM": {
        "paradigms": None,  # None = every paradigm that declares it
        "reason": (
            "Each hex is a single-paradigm build, so there is no runtime paradigm "
            "to set. The backend uses this to record intent and choose the hex."
        ),
    },
    "TEST_CHAIN": {
        "paradigms": ["pavlovian"],
        "reason": "PavlovianScheduler is a trial state machine; there is no chain to test.",
    },
    "TEST_MODE": {
        "paradigms": ["pavlovian"],
        "reason": "Same — no operant chain to put into test mode.",
    },
    "LEVER_RH_SET_ACTIVE": {"paradigms": ["pavlovian"], "reason": "pavlovian is non-operant."},
    "LEVER_LH_SET_ACTIVE": {"paradigms": ["pavlovian"], "reason": "pavlovian is non-operant."},
    "LEVER_RH_SET_INACTIVE": {"paradigms": ["pavlovian"], "reason": "pavlovian is non-operant."},
    "LEVER_LH_SET_INACTIVE": {"paradigms": ["pavlovian"], "reason": "pavlovian is non-operant."},
    "LEVER_RH_SET_TIMEOUT": {
        "paradigms": ["omission", "omission_lite"],
        "reason": "omission gates on ABSENCE_TIMER, not a post-press timeout.",
    },
    "LEVER_LH_SET_TIMEOUT": {
        "paradigms": ["omission", "omission_lite"],
        "reason": "omission gates on ABSENCE_TIMER, not a post-press timeout.",
    },
}

KNOWN_FIRMWARE_GAPS: dict[str, dict[str, Any]] = {
    "LASER_TRIGGER_LH_ONLY": {
        "paradigms": ["vi", "vi_lite", "omission", "omission_lite"],
        "reason": (
            "vi and omission handle only LASER_TRIGGER_RH_ONLY and have no "
            "LASER_LEVER_FILTER global at all; ReconfigureChain hardcodes "
            "sourceFilter = LEVER_RH. Code 685 therefore reaches the default "
            "case, so LASER_RH_ONLY_MODE keeps its previous value and no "
            "reconfigure happens. Selecting 'LH only' does not disable the "
            "laser — it leaves the prior contingency in force, which can mean "
            "stimulation on RH presses while the protocol says LH, for a whole "
            "session, in data that looks normal. fr.ino and pr.ino show the "
            "intended shape. Firmware does emit a level-006 naming the code."
        ),
        "ui_guidance": (
            "Disable the 'LH lever' laser contingency on vi and omission and say "
            "why — do not hide it. A hidden control reads as absent hardware, and "
            "an 'lh' preset applied on vi would then no-op with nothing on screen "
            "to explain the mismatch. Derive the gate from this entry, never "
            "hand-write it, or the gate will outlive the gap."
        ),
        "persistence_note": (
            "buildPresetFromSession bakes contingency:'lh' into saved presets, "
            "re-dispatched on every apply, so a preset authored on an FR rig "
            "misbehaves when applied to a VI rig. Gating the control is not "
            "enough on its own; existing presets carry the setting."
        ),
        "remedy": (
            "Mirror fr.ino's LASER_LEVER_FILTER into vi/omission and their lite "
            "twins (4 sketches). Deferred pending bench verification: VI "
            "interposes an interval trigger where FR uses press-count, so "
            "sourceFilter behaviour under a VI schedule is not established by "
            "reading alone, and the lite twins sit at 91-94% of UNO flash."
        ),
    },
}


# Commands whose presence in a "_lite" twin's diff against its base is expected.
# A lite sketch is its base minus two-photon support, so MICROSCOPE_* and SLM_*
# divergence is the strip working as intended; anything else is drift.
TWO_PHOTON_CMD_PREFIXES = ("MICROSCOPE_", "SLM_")


def parse_commands_header(path: Path) -> dict[str, int]:
    """Parse ``Commands.h`` into ``{CONSTANT_NAME: value}``."""
    codes = {name: int(value) for name, value in _CONSTEXPR_INT_RE.findall(path.read_text())}
    if len(codes) < MIN_COMMANDS:
        raise SchemaError(
            f"{path} parse looks broken — found only {len(codes)} constants, expected >= {MIN_COMMANDS}"
        )
    return codes


def parse_pins_header(path: Path) -> dict[str, int]:
    """Parse ``Pins.h`` into ``{PIN_SYMBOL: pin_number}``."""
    pins = {name: int(value) for name, value in _CONSTEXPR_PIN_RE.findall(path.read_text())}
    if len(pins) < MIN_PIN_CONSTANTS:
        raise SchemaError(
            f"{path} parse looks broken — found only {len(pins)} pin constants, "
            f"expected >= {MIN_PIN_CONSTANTS}"
        )
    return pins


def command_refs(path: Path) -> set[str]:
    """Return every ``Cmd::NAME`` referenced in a source file."""
    return set(_CMD_REF_RE.findall(path.read_text()))


@dataclass(frozen=True)
class Sketch:
    """One Arduino sketch directory in the firmware tree."""

    name: str
    board: str
    lite: bool
    base: str
    path: Path

    def as_dict(self, root: Path) -> dict[str, Any]:
        return {
            "name": self.name,
            "board": self.board,
            "lite": self.lite,
            "base": self.base,
            "path": str(self.path.relative_to(root)),
            "cmd_refs": sorted(command_refs(self.path)),
        }


def discover_sketches(firmware_root: Path) -> list[Sketch]:
    """Enumerate sketches from the filesystem rather than a hardcoded list.

    Derived, not declared: a tenth sketch requires no edit here. ``_lite``
    variants target the UNO; everything else targets the Mega.
    """
    sketches: list[Sketch] = []
    for ino in sorted(firmware_root.glob("*/*.ino")):
        name = ino.stem
        if ino.parent.name != name:
            continue  # Arduino requires sketch dir and .ino to share a name
        lite = name.endswith("_lite")
        sketches.append(
            Sketch(
                name=name,
                board="uno" if lite else "mega",
                lite=lite,
                base=name[: -len("_lite")] if lite else name,
                path=ino,
            )
        )
    if len(sketches) < MIN_SKETCHES:
        raise SchemaError(
            f"{firmware_root} sketch discovery looks broken — found only {len(sketches)}, "
            f"expected >= {MIN_SKETCHES}"
        )
    return sketches


# --- Firmware device-name namespaces -------------------------------------
#
# Firmware emits device names in THREE distinct namespaces, and which one a
# consumer must key on depends on the message level it reads. Conflating them
# is how `_COMMAND_STATE_MAP` came to write "LICK_CIRCUIT" into the same list
# the level-000 path fills with "LICK", producing two entries for one device.
#
#   level 000 (config)  reportDeviceConfig / reportDeviceLever
#   level 001 (param)   logParamChange
#   level 007 (event)   Scheduler::LogDeviceActivation, LickCircuit::LogOutput
_CONFIG_NAME_RE = re.compile(r'reportDevice(?:Config|Lever)\(\s*F\("([A-Z_0-9]+)"')
_PARAM_NAME_RE = re.compile(r'logParamChange\(\s*F\("([A-Z_0-9]+)"')
_EVENT_NAME_RE = re.compile(r'device\s*=\s*F\("([A-Z_0-9]+)"\)')
# Not every device name goes through a helper. The controller identification
# line, the microscope/SLM config rows and the Pavlovian trial events are
# printed as inline JSON literals, so the helper-call patterns above miss them.
_INLINE_NAME_RE = re.compile(r'\\"level\\":\\"(\d{3})\\",\\"device\\":\\"([A-Z_0-9]+)\\"')
# Levers are emitted through a ternary into a `leverDevice` local rather than a
# `device = F(...)` assignment, so the pattern above cannot see them.
_EVENT_LEVER_RE = re.compile(r'leverDevice\s*=\s*[^;]*?F\("([A-Z_0-9]+)"\)[^;]*?F\("([A-Z_0-9]+)"\)', re.S)


def _scan(paths: Iterable[Path], pattern: re.Pattern[str]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        found.update(pattern.findall(path.read_text()))
    return found


def device_namespaces(firmware_root: Path) -> dict[str, list[str]]:
    """Return the device-name sets firmware emits, keyed by message level.

    See the comment above for why these are three sets and not one.
    """
    # Sketch-local .cpp files count too: the Pavlovian scheduler lives in
    # firmware/pavlovian/, not the shared library, and emits its own events.
    sources = (
        sorted(firmware_root.glob("*/*.ino"))
        + sorted(firmware_root.glob("*/*.cpp"))
        + sorted((firmware_root / "libraries" / "REACHERDevices" / "src").glob("*.cpp"))
    )
    events = _scan(sources, _EVENT_NAME_RE)
    for path in sources:
        for pair in _EVENT_LEVER_RE.findall(path.read_text()):
            events.update(pair)
    # LickCircuit passes the Device base's stored name rather than a literal, so
    # the regex above cannot see it. It is the one device whose 007 spelling
    # differs from its 000 spelling, which is exactly why it must be recorded.
    lick = firmware_root / "libraries" / "REACHERDevices" / "src" / "LickCircuit.cpp"
    if lick.is_file() and 'Device(pin, INPUT_PULLUP, "LICK_CIRCUIT")' in lick.read_text():
        events.add("LICK_CIRCUIT")
    config = _scan(sources, _CONFIG_NAME_RE)
    param = _scan(sources, _PARAM_NAME_RE)
    by_level = {"000": config, "001": param, "007": events}
    for path in sources:
        for level, device in _INLINE_NAME_RE.findall(path.read_text()):
            by_level.setdefault(level, set()).add(device)

    # Build the downstream contract from what the kernel emits into "event",
    # never from what firmware prints at one level. See EVENT_STREAM_CONTRIBUTORS.
    post_kernel: set[str] = set()
    for level, contribution in EVENT_STREAM_CONTRIBUTORS.items():
        if contribution is None:
            continue
        if contribution == "firmware":
            for name in by_level.get(level, set()):
                post_kernel.update(POST_KERNEL_EVENT_REWRITES.get(name, (name,)))
        else:
            post_kernel.update(contribution)

    return {
        "config": sorted(config),
        "param": sorted(param),
        "event": sorted(events),
        # The only set anything downstream of the kernel should be checked against.
        "post_kernel": sorted(post_kernel),
        "by_level": {level: sorted(names) for level, names in sorted(by_level.items())},
    }


# --- Tree discovery ------------------------------------------------------


def find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from *start* looking for a reacher checkout.

    Identified by ``src/reacher/kernel/commands.py`` — the registry that defines
    the repo — so an archived ``reacher-firmware`` sibling is never mistaken for
    one.
    """
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "src" / "reacher" / "kernel" / "commands.py").is_file():
            return candidate
    return None


def _git(root: Path, *args: str) -> Optional[str]:
    """Run a read-only git command, returning None when git or the repo is absent."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _provenance(root: Optional[Path]) -> dict[str, Any]:
    from . import __version__ as pkg_version

    if root is None:
        return {"root": None, "git_sha": None, "dirty": None, "version": pkg_version}
    return {
        "root": str(root),
        "git_sha": _git(root, "rev-parse", "HEAD"),
        "dirty": bool(_git(root, "status", "--porcelain")),
        "version": pkg_version,
    }


# --- Section builders ----------------------------------------------------


def python_section() -> dict[str, Any]:
    """Serialize the in-process Python registries.

    Imports are local so that a caller wanting only the firmware section (or a
    tree whose Python is mid-edit and unimportable) is not forced through them.
    """
    from .kernel import commands as cmd_mod
    from . import pin_overrides
    from .uploader import boards as boards_mod

    registry = cmd_mod.COMMAND_REGISTRY
    if len(registry) < MIN_COMMANDS:
        raise SchemaError(
            f"COMMAND_REGISTRY has only {len(registry)} entries, expected >= {MIN_COMMANDS}"
        )
    if len(pin_overrides.PIN_CONSTRAINTS) < MIN_COMPONENTS:
        raise SchemaError(
            f"PIN_CONSTRAINTS has only {len(pin_overrides.PIN_CONSTRAINTS)} entries, "
            f"expected >= {MIN_COMPONENTS}"
        )

    pin_sets = {}
    for board in boards_mod.SUPPORTED_BOARDS:
        digital, pwm, interrupt, pcint0 = pin_overrides.board_sets(board)
        pin_sets[board] = {
            "digital": sorted(digital),
            "pwm": sorted(pwm),
            "interrupt": sorted(interrupt),
            "pcint0": sorted(pcint0),
        }

    return {
        "commands": [
            {
                "code": int(spec.code),
                "name": spec.name,
                "description": spec.description,
                "payload_key": spec.payload_key,
                "payload_type": spec.payload_type,
                "paradigms": list(spec.paradigms),
                "deprecated": spec.deprecated,
            }
            for _, spec in sorted(registry.items())
        ],
        "command_code_enum": {member.name: int(member.value) for member in cmd_mod.CommandCode},
        "pin_constraints": [
            {
                "code": code,
                "component_key": c.component_key,
                "requires_pwm": c.requires_pwm,
                "requires_interrupt": c.requires_interrupt,
                "requires_pcint": c.requires_pcint,
            }
            for code, c in sorted(pin_overrides.PIN_CONSTRAINTS.items())
        ],
        "component_keys": list(pin_overrides.COMPONENT_KEYS),
        "board_profiles": [
            {
                "board_id": p.board_id,
                "display_name": p.display_name,
                "fqbn": p.fqbn,
                "avrdude_args": list(p.avrdude_args),
            }
            for _, p in sorted(boards_mod.BOARD_PROFILES.items())
        ],
        "default_board": boards_mod.DEFAULT_BOARD,
        "pin_sets": pin_sets,
        "paradigms": {
            "all": list(cmd_mod.ALL_PARADIGMS),
            "lite_capable": list(cmd_mod.LITE_CAPABLE_PARADIGMS),
            "lite": list(cmd_mod.LITE_PARADIGMS),
            "non_lite": list(cmd_mod.NON_LITE_PARADIGMS),
            "schedule_to_paradigm": dict(cmd_mod.SCHEDULE_TO_PARADIGM),
        },
        "command_state_map_devices": _command_state_map_devices(),
        "post_kernel_event_rewrites": {k: list(v) for k, v in POST_KERNEL_EVENT_REWRITES.items()},
        "intentionally_unhandled": INTENTIONALLY_UNHANDLED,
        "known_firmware_gaps": KNOWN_FIRMWARE_GAPS,
    }


def _command_state_map_devices() -> list[str]:
    """Device names the kernel writes into ``hardware_settings`` on command send.

    Exported so rule L8 can assert every one of them exists in the firmware
    level-000 config namespace. They must, because both this path and the
    firmware-config path append into the same list and dedup on plain string
    equality — a spelling that firmware never emits creates a second, permanent
    entry for a device that already has one.
    """
    from .kernel.reacher import _COMMAND_STATE_MAP

    return sorted({device for device, _field, _value in _COMMAND_STATE_MAP.values()})


def firmware_section(root: Optional[Path]) -> dict[str, Any]:
    """Serialize the firmware tree, or report its absence.

    Returns ``{"present": False, ...}`` rather than raising when run against an
    installed wheel, which ships only hex. Consumers branch on the flag; nothing
    is allowed to skip silently.
    """
    if root is None:
        return {"present": False, "reason": "no reacher checkout found"}
    firmware_root = root / "firmware"
    if not firmware_root.is_dir():
        return {
            "present": False,
            "reason": f"no firmware/ tree at {firmware_root} (installed-wheel layout)",
        }

    lib_src = firmware_root / "libraries" / "REACHERDevices" / "src"
    sketches = discover_sketches(firmware_root)
    hex_root = root / "src" / "reacher" / "hex"

    return {
        "present": True,
        "root": str(firmware_root.relative_to(root)),
        "commands_h": parse_commands_header(lib_src / "Commands.h"),
        "pins_h": parse_pins_header(lib_src / "Pins.h"),
        "pin_symbol_to_component": dict(PIN_SYMBOL_TO_COMPONENT),
        "fixed_pins": dict(FIXED_PINS),
        "sketches": [s.as_dict(root) for s in sketches],
        "common_dispatcher_cmd_refs": sorted(command_refs(lib_src / "ReacherHelpers.cpp")),
        "device_classes": sorted(
            p.stem for p in lib_src.glob("*.h")
            if p.stem not in {
                "Commands", "Pins", "Scheduler", "Trigger", "Action", "ReacherHelpers", "Device",
            }
        ),
        "device_names": device_namespaces(firmware_root),
        "hex": [
            {"board": d.name, "sketch": f.stem, "path": str(f.relative_to(root))}
            for d in sorted(hex_root.iterdir()) if d.is_dir()
            for f in sorted(d.glob("*.hex"))
        ] if hex_root.is_dir() else [],
    }


def dump(root: Optional[Path] = None) -> dict[str, Any]:
    """Build the complete schema document for a checkout."""
    root = root or find_repo_root()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        python = python_section()
    except Exception as exc:  # a mid-edit syntax error must degrade, not crash
        python = {}
        errors.append(f"python section failed: {type(exc).__name__}: {exc}")

    try:
        firmware = firmware_section(root)
    except Exception as exc:
        firmware = {"present": False, "reason": f"{type(exc).__name__}: {exc}"}
        errors.append(f"firmware section failed: {type(exc).__name__}: {exc}")

    if not firmware.get("present"):
        warnings.append(
            "firmware tree unavailable — firmware-dependent checks must report UNAVAILABLE, "
            "never pass: " + str(firmware.get("reason", "unknown"))
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": _provenance(root),
        "python": python,
        "firmware": firmware,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m reacher.schema",
        description="Dump REACHER's registries and firmware surface as JSON.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    dump_p = sub.add_parser("dump", help="emit the schema document")
    dump_p.add_argument("--json", action="store_true", help="accepted for clarity; JSON is the only format")
    dump_p.add_argument("--root", type=Path, default=None, help="reacher checkout to read (default: autodetect)")
    dump_p.add_argument("--indent", type=int, default=None, help="pretty-print with this indent")

    args = parser.parse_args(argv)
    doc = dump(args.root)
    json.dump(doc, sys.stdout, indent=args.indent, sort_keys=False)
    sys.stdout.write("\n")
    # Exit non-zero on a parse failure so a caller that ignores the body still
    # notices. A missing firmware tree is a warning, not an error.
    return 1 if doc["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
