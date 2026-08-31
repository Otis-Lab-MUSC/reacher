"""Device-name coherence between the kernel and the firmware it mirrors.

Firmware emits device names in three namespaces, keyed by message level:

    000 config   reportDeviceConfig / reportDeviceLever
    001 param    logParamChange
    007 event    Scheduler::LogDeviceActivation, LickCircuit::LogOutput

They are not interchangeable — the lick circuit is ``LICK`` at level 000 and
``LICK_CIRCUIT`` at level 007, and the cues and pumps carry an index suffix at
007 that they do not carry at 000.

This matters because ``REACHER.hardware_settings`` has **two writers**:

    A. the firmware level-000 config path (reacher.py:759) appends the event verbatim
    B. the optimistic command-send path (reacher.py:771, driven by _COMMAND_STATE_MAP)

Both dedup on ``entry.get("device") == device`` — plain string equality. A name
in _COMMAND_STATE_MAP that firmware never emits at level 000 therefore does not
collide with the firmware entry; it creates a *second, permanent* entry for a
device that already has one, with disjoint fields, and both reach the browser.

That is rule L8, below. It is a precondition for any cross-repo name checking:
a frontend table can be individually consistent with the namespace feeding it
while the kernel's own two writers disagree, so checking the frontend alone
yields a confident wrong answer.
"""

from pathlib import Path

import pytest

from reacher import schema
from reacher.kernel.reacher import _COMMAND_STATE_MAP

SIMULATOR = Path(__file__).resolve().parent.parent / "src" / "reacher" / "kernel" / "simulator.py"

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = REPO_ROOT / "firmware"

pytestmark = pytest.mark.skipif(
    not FIRMWARE.is_dir(), reason="firmware source not present (installed-wheel run)"
)


@pytest.fixture(scope="module")
def namespaces():
    return schema.device_namespaces(FIRMWARE)


def _state_map_devices() -> set[str]:
    return {device for device, _field, _value in _COMMAND_STATE_MAP.values()}


def test_l8_command_state_map_uses_only_config_namespace_names(namespaces):
    """L8: every _COMMAND_STATE_MAP device must exist in the level-000 namespace.

    A name firmware never emits at level 000 cannot dedup against the firmware's
    own entry, so it silently doubles the device in hardware_settings.
    """
    config_names = set(namespaces["config"])
    unknown = sorted(_state_map_devices() - config_names)
    assert not unknown, (
        "_COMMAND_STATE_MAP writes device names the firmware never emits at level 000, "
        "so they will not dedup against the firmware config entry and will produce a "
        f"duplicate hardware_settings row per device: {unknown}. "
        f"Level-000 namespace is: {sorted(config_names)}"
    )


def test_l8_covers_the_devices_that_accept_commands(namespaces):
    """The converse sanity floor: the map must not have quietly emptied out."""
    devices = _state_map_devices()
    assert len(devices) >= 8, f"_COMMAND_STATE_MAP covers only {len(devices)} devices"
    assert devices <= set(namespaces["config"])


def test_event_path_normalizes_lick_to_the_config_spelling():
    """The 007 handler already canonicalizes LICK_CIRCUIT -> LICK.

    This is what establishes LICK as the kernel's canonical spelling, and is why
    L8 resolves in favour of LICK rather than LICK_CIRCUIT.
    """
    source = (REPO_ROOT / "src" / "reacher" / "kernel" / "reacher.py").read_text()
    assert 'case "LICK_CIRCUIT":' in source
    lick_case = source.split('case "LICK_CIRCUIT":', 1)[1][:200]
    assert "'device'] = \"LICK\"" in lick_case or '"device"] = "LICK"' in lick_case, (
        "the 007 path should normalize the firmware's LICK_CIRCUIT to LICK"
    )


def test_the_three_namespaces_are_not_interchangeable(namespaces):
    """Guard the assumption L8 rests on: these really are distinct sets.

    If firmware were ever unified onto one namespace this test fails, and L8's
    justification should be revisited rather than silently kept.
    """
    config, event = set(namespaces["config"]), set(namespaces["event"])
    assert config != event, "namespaces converged — revisit L8's premise"
    assert "LICK" in config and "LICK_CIRCUIT" not in config
    assert "LICK_CIRCUIT" in event and "LICK" not in event


# --- L8 extended to the simulator ----------------------------------------
#
# The simulator stands in for firmware in every hardware-free test, so a device
# name it emits that firmware never emits means the whole suite exercises a
# namespace that cannot occur in production. It had the same LICK_CIRCUIT defect
# as _COMMAND_STATE_MAP, from the same cause.

_SIM_LEVEL_RE = __import__("re").compile(
    r'"level":\s*"(\d{3})",\s*"device":\s*(?:f?)"([A-Z_0-9{}a-z]+)"'
)

#: Simulator device names that intentionally do not match current firmware.
#:
#: The bar for an entry here is a spelling some real firmware version actually
#: emitted AND that the kernel still handles. A name no firmware ever sent is a
#: simulator bug, not legacy coverage — it exercises a path that never existed.
#: The level-000 SWITCH_LEVER_<orientation> form failed that bar and was fixed
#: rather than allowlisted.
SIMULATOR_LEGACY_NAMES = {
    ("007", "SWITCH_LEVER"): (
        "the genuine pre-v2.4.x lever event spelling, with a live handler at "
        "reacher.py's update_behavioral_events. Emitting it keeps that legacy "
        "path covered."
    ),
}


def test_l8_simulator_emits_names_firmware_actually_emits(namespaces):
    """The simulator must not invent device names, or tests exercise fiction."""
    by_level = {"000": set(namespaces["config"]), "007": set(namespaces["event"])}
    violations = []
    for level, device in _SIM_LEVEL_RE.findall(SIMULATOR.read_text()):
        if (level, device) in SIMULATOR_LEGACY_NAMES or level not in by_level:
            continue
        if "{" in device:
            # An f-string template such as f"LEVER_{active_orientation}". Accept
            # it when some real firmware name starts with the literal prefix, so
            # the check works without modelling the interpolated variable.
            prefix = device.split("{", 1)[0]
            if any(name.startswith(prefix) for name in by_level[level]):
                continue
            violations.append(
                f"level {level}: no firmware device name starts with {prefix!r} "
                f"(from template {device!r})"
            )
            continue
        if device not in by_level[level]:
            violations.append(f"level {level}: {device!r} is not in the firmware namespace")
    assert not violations, (
        "simulator.py emits device names firmware never produces, so hardware-free "
        "tests cover a namespace that cannot occur in production:\n"
        + "\n".join(sorted(set(violations)))
    )


# --- The event stream contract -------------------------------------------
#
# Three times now, a contract has been modelled from a partial view of its
# producer: the LICK config/event confusion, the near-miss of validating the
# frontend against raw 007 names, and post_kernel initially missing SLM because
# level 009 synthesizes it. The general fix is to derive the downstream contract
# from what the *kernel* emits into a stream, not from what firmware prints at
# one level. These tests keep that derivation honest.


def test_every_code_dict_handler_is_classified():
    """A new firmware level must fail here until someone says how it contributes.

    Without this, adding a level-010 handler that emits into "event" silently
    leaves its device names out of post_kernel, and every downstream table using
    them gets reported as drifted.
    """
    source = (REPO_ROOT / "src" / "reacher" / "kernel" / "reacher.py").read_text()
    block = source.split("self.code_dict", 1)[1].split("}", 1)[0]
    levels = set(__import__("re").findall(r'"(\d{3})"\s*:', block))
    assert levels, "could not parse code_dict — update this test"
    unclassified = levels - set(schema.EVENT_STREAM_CONTRIBUTORS)
    assert not unclassified, (
        f"code_dict handles levels {sorted(unclassified)} that EVENT_STREAM_CONTRIBUTORS "
        "does not classify. Say whether each emits into the 'event' stream, or "
        "post_kernel will silently omit its device names."
    )


def test_slm_reaches_the_event_stream_from_level_009(namespaces):
    """The concrete case the generalization was needed for.

    SLM appears at no level-007 print site anywhere in firmware; the kernel's
    update_slm_events synthesizes it from a level-009 timestamp. A frontend table
    listing SLM as a behavior event is correct, and a namespace built only from
    007 print sites would report it as wrong.
    """
    assert "SLM" not in namespaces["event"], "SLM is not a firmware 007 name"
    assert "SLM" in namespaces["post_kernel"], "SLM must still reach the event stream"
    assert "SLM" in namespaces["by_level"]["009"]


def test_non_event_levels_do_not_leak_into_post_kernel(namespaces):
    """Level 008 surfaces as a separate 'frame' message, not an event."""
    assert schema.EVENT_STREAM_CONTRIBUTORS["008"] is None
    # MICROSCOPE is emitted at 008 but contributes no behavior-event device name.
    assert "MICROSCOPE" in namespaces["by_level"]["008"]
    assert "MICROSCOPE" not in namespaces["post_kernel"]


def test_synthesized_contributors_name_real_devices(namespaces):
    """A synthesized name that no firmware level mentions would be a typo."""
    known = set().union(*namespaces["by_level"].values())
    for level, contribution in schema.EVENT_STREAM_CONTRIBUTORS.items():
        if contribution is None or contribution == "firmware":
            continue
        for name in contribution:
            assert name in known, (
                f"level {level} claims to synthesize {name!r}, which appears nowhere in firmware"
            )
