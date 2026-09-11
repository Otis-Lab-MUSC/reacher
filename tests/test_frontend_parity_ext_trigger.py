"""Cross-repo parity for the external TTL start-trigger frontend surface.

Companion to ``test_frontend_parity.py``, split out rather than added there
because it covers different labrynth source files (``useFirmwareCommands.ts``,
``types/index.ts``'s ``SessionState``) than that file's ``pinMeta.ts`` focus,
and a different backend fact (command codes + session-state strings) than its
pin-role tables.

Kept deliberately shallow — regex over source text, no TypeScript toolchain —
for the same reason ``mcp/sources/ts.py`` is. Two things this file does NOT
duplicate from ``test_frontend_parity.py``, because they are already covered
there: ``ext_trigger``'s ``COMPONENT_REQUIRES_INT`` flag (``test_c5c_...``) and
the microscope timestamp pin's non-remappability (``test_c6_...``).

Skipped, loudly, when no labrynth checkout is present — same convention as
``test_frontend_parity.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from reacher import pin_overrides
from reacher.kernel.commands import CommandCode
from reacher.mcp.sources import ts

REPO_ROOT = Path(__file__).resolve().parent.parent
LABRYNTH_ROOT = ts.find_labrynth_root()
FIRMWARE = REPO_ROOT / "firmware"

pytestmark = pytest.mark.skipif(
    LABRYNTH_ROOT is None,
    reason=(
        "labrynth checkout not found — frontend parity is UNVERIFIED, not passing. "
        "Clone it as a sibling of this repo to run these checks."
    ),
)

# The full session-state vocabulary reacher's session_manager.py can broadcast
# to a client over the "session_state" WS message (session_manager.py:212's
# set_state -> _broadcast_state, plus the "idle"/"disconnected" call sites at
# lines 105 and 261). "destroying" (line 127) is deliberately excluded: it is
# set directly on SessionInfo without going through set_state/_broadcast_state,
# so it is never observable by a client and is not part of this contract.
# There is no single Python type for this set to import — session_manager.py's
# `state` field is a bare `str` — so it is hand-collected here, the same way
# pin_overrides facts are hand-collected on the TS side of the mirror.
BACKEND_SESSION_STATES = {
    "idle", "uploading", "connected", "armed", "running", "paused",
    "stopped", "disconnected",
}


def _bare_const(source: str, name: str) -> int:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(\d+)\s*;", source)
    if match is None:
        raise AssertionError(f"{name} not found in useFirmwareCommands.ts")
    return int(match.group(1))


@pytest.fixture(scope="module")
def use_firmware_commands_source() -> str:
    path = LABRYNTH_ROOT / "web" / "src" / "hooks" / "useFirmwareCommands.ts"
    return path.read_text()


@pytest.fixture(scope="module")
def pin_meta_source() -> str:
    path = LABRYNTH_ROOT / "web" / "src" / "components" / "hardware" / "pinMeta.ts"
    return path.read_text()


# --- Capability-sniff command codes ---------------------------------------
#
# useFirmwareCommands.ts hand-copies these two codes to decide whether to show
# the two-photon and external-trigger UI at all, by checking whether the
# board's advertised command list contains them. If either drifts, the UI
# either vanishes on capable firmware or offers a control the board rejects.


def test_ext_trigger_arm_constant_matches_command_code(use_firmware_commands_source):
    assert _bare_const(use_firmware_commands_source, "EXT_TRIGGER_ARM") == int(
        CommandCode.EXT_TRIGGER_ARM
    )


def test_microscope_arm_constant_matches_command_code(use_firmware_commands_source):
    assert _bare_const(use_firmware_commands_source, "MICROSCOPE_ARM") == int(
        CommandCode.MICROSCOPE_ARM
    )


# --- Assignable trigger pins -----------------------------------------------
#
# NOT covered by test_frontend_parity.py: that file's `meta` fixture parses
# COMPONENT_REQUIRES_INT (which pins *require* interrupt capability) but never
# parses MEGA_INT_ASSIGNABLE / UNO_INT_ASSIGNABLE (which pins are actually
# *offered*). Those are two different backend facts — role vs. allow-list, see
# pin_overrides.py's own PinConstraint.allowed_pins docstring — and only the
# first was under test. This closes that gap for the trigger's allow-list.


def test_mega_assignable_pins_match_backend_allow_list(pin_meta_source):
    frontend = set(ts.parse_pin_set(pin_meta_source, "MEGA_INT_ASSIGNABLE"))
    assert frontend == set(pin_overrides.EXT_TRIGGER_PINS), (
        f"pinMeta.ts MEGA_INT_ASSIGNABLE vs pin_overrides.EXT_TRIGGER_PINS: "
        f"{frontend ^ set(pin_overrides.EXT_TRIGGER_PINS)}"
    )


def test_mega_assignable_pins_exclude_microscope_and_primary_cue(pin_meta_source):
    """Pin 2 (microscope timestamp ISR) and pin 3 (primary cue) must never be
    offered — see docs/external-trigger.md's "load-bearing restriction"."""
    frontend = set(ts.parse_pin_set(pin_meta_source, "MEGA_INT_ASSIGNABLE"))
    assert 2 not in frontend
    assert 3 not in frontend


@pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware source not present")
def test_mega_assignable_pins_match_firmware_is_assignable_pin(pin_meta_source):
    """Cross-check against ExternalTrigger::IsAssignablePin directly, not just
    pin_overrides — the two are hand-kept in sync with nothing enforcing it."""
    cpp = (FIRMWARE / "libraries" / "REACHERDevices" / "src" / "ExternalTrigger.cpp").read_text()
    match = re.search(r"IsAssignablePin\(int8_t pin\)\s*\{\s*return\s*(.*?);\s*\}", cpp, re.S)
    assert match is not None, "ExternalTrigger::IsAssignablePin body not found"
    firmware_pins = {int(p) for p in re.findall(r"pin == (\d+)", match.group(1))}
    frontend = set(ts.parse_pin_set(pin_meta_source, "MEGA_INT_ASSIGNABLE"))
    assert frontend == firmware_pins, (
        f"pinMeta.ts MEGA_INT_ASSIGNABLE vs ExternalTrigger::IsAssignablePin: "
        f"{frontend ^ firmware_pins}"
    )


def test_uno_offers_no_assignable_trigger_pin(pin_meta_source):
    """UNO_INT_ASSIGNABLE must stay empty — the UNO's only interrupt pins (2, 3)
    are both already spoken for, so the feature is Mega-only."""
    block = ts._block(pin_meta_source, "UNO_INT_ASSIGNABLE")
    assert re.search(r"\[\s*\]", block), f"expected an empty array literal, got: {block!r}"


# --- SessionState union -----------------------------------------------------


def _parse_session_state_union(source: str) -> set[str]:
    match = re.search(r"export\s+type\s+SessionState\s*=\s*(.*?);", source, re.S)
    assert match is not None, "SessionState union not found in types/index.ts"
    members = set(re.findall(r'"([a-z]+)"', match.group(1)))
    assert len(members) >= len(BACKEND_SESSION_STATES) - 1, (
        f"SessionState union parse looks broken — found only {members}"
    )
    return members


def test_session_state_union_covers_every_backend_broadcastable_state():
    source = (LABRYNTH_ROOT / "web" / "src" / "types" / "index.ts").read_text()
    frontend = _parse_session_state_union(source)
    assert frontend == BACKEND_SESSION_STATES, (
        f"types/index.ts SessionState vs backend-broadcastable states: "
        f"{frontend ^ BACKEND_SESSION_STATES}"
    )


# --- ConfigLock coverage of the armed-freeze surface ------------------------
#
# reacher's own contract: the *entire* hardware command surface is frozen by a
# single gate (routers/hardware.py's `send_command`, one dispatch endpoint for
# every device/pin command) plus one more gate on routers/program.py's `/limit`
# route. There is no per-command 409 list to enumerate — it's a blanket freeze
# ahead of the command-registry lookup. So the frontend-side property worth
# asserting isn't "does every 409 site have a matching lock" (there is exactly
# one real gate to match) but "is there a control surface that reaches the
# backend while bypassing ConfigLock" — checked by hand for this package (see
# report), not practically expressible as a source-text regex here.


def test_hardware_command_endpoint_is_the_single_armed_gate():
    """Documents the shape of the backend gate this package's ConfigLock is
    meant to mirror, so a future split of the dispatch endpoint is caught."""
    hardware_py = (REPO_ROOT / "src" / "reacher" / "api" / "routers" / "hardware.py").read_text()
    assert hardware_py.count('if info.state == "armed":') == 1, (
        "expected exactly one armed-freeze gate in hardware.py's command dispatch; "
        "if this now fires more than once, ConfigLock's single-fieldset model may "
        "no longer cover every command path — recheck by hand"
    )
