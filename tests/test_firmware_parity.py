"""Firmware-side invariants that the existing command-parity test cannot see.

`test_command_parity.py` proves Commands.h and CommandCode agree on names and
values. That is necessary but far from sufficient: it says nothing about whether
any sketch actually *handles* a command, or whether a change made to a paradigm
reached its "_lite" twin. With nine hand-maintained sketches and no central
dispatcher, those are precisely the omissions to expect from someone adding a
command — and today nothing catches them.

C13 — a "_lite" twin diverges from its base only by the two-photon strip.
C14 — a command declaring support for a paradigm is handled by that paradigm.
C10 — the seven places that independently register paradigm identity agree.
C11 — every paradigm has a committed hex artifact for its board.

Skipped when the firmware source tree is absent (installed-wheel run).
"""

import re
from pathlib import Path

import pytest

from reacher import schema
from reacher.kernel.commands import (
    ALL_PARADIGMS,
    COMMAND_REGISTRY,
    LITE_CAPABLE_PARADIGMS,
    SCHEDULE_TO_PARADIGM,
)
from reacher.kernel.simulator import PARADIGM_TO_SCHEDULE, SCHEDULE_TO_SKETCH

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = REPO_ROOT / "firmware"
HEX_ROOT = REPO_ROOT / "src" / "reacher" / "hex"

pytestmark = pytest.mark.skipif(
    not FIRMWARE.is_dir(), reason="firmware source not present (installed-wheel run)"
)


@pytest.fixture(scope="module")
def doc():
    return schema.dump(REPO_ROOT)


@pytest.fixture(scope="module")
def sketches(doc):
    return {s["name"]: s for s in doc["firmware"]["sketches"]}


# --- C13: lite-twin divergence -------------------------------------------

# A "_lite" build is its base minus two-photon support. Any *other* difference
# means the twin missed a change — the failure mode of maintaining nine sketches
# by hand.
#
# A line-level textual diff cannot express this: the two-photon strip removes
# whole blocks whose interior lines (`int req = inputJson["pin"] | 11;`) carry no
# two-photon token, so matching line by line produces noise that has to be
# silenced with an ever-growing regex allowlist. A check that needs tuning to
# stay green is a check that gets deleted the first time it is inconvenient.
#
# These two invariants say the same thing without the noise, and are strictly
# sharper — a reordering defeats a diff but not a set comparison.


@pytest.mark.parametrize("base_name", LITE_CAPABLE_PARADIGMS)
def test_lite_twin_config_is_byte_identical_to_base(base_name):
    """C13a: paradigm chain configuration has nothing to do with two-photon hardware.

    Config.h holds the trigger/chain wiring that *defines* the paradigm. The lite
    strip removes devices, never contingencies, so any difference here means a
    timing or ratio change landed in one twin and not the other.
    """
    base = (FIRMWARE / base_name / "Config.h").read_text()
    lite = (FIRMWARE / f"{base_name}_lite" / "Config.h").read_text()
    assert base == lite, (
        f"{base_name}_lite/Config.h has drifted from {base_name}/Config.h — "
        f"a paradigm-defining change reached only one twin"
    )


@pytest.mark.parametrize("base_name", LITE_CAPABLE_PARADIGMS)
def test_lite_twin_shares_every_non_two_photon_command(base_name, sketches):
    """C13, the sharper half: the twin must handle every non-2P command its base does.

    The textual diff can be defeated by a reordering; comparing the Cmd:: sets
    cannot.
    """
    base_refs = set(sketches[base_name]["cmd_refs"])
    lite_refs = set(sketches[f"{base_name}_lite"]["cmd_refs"])
    two_photon = {c for c in base_refs if c.startswith(schema.TWO_PHOTON_CMD_PREFIXES)}
    missing = (base_refs - two_photon) - lite_refs
    assert not missing, (
        f"{base_name}_lite is missing commands handled by {base_name}: {sorted(missing)}"
    )


@pytest.mark.parametrize("base_name", LITE_CAPABLE_PARADIGMS)
def test_lite_twin_handles_no_two_photon_commands(base_name, sketches):
    """The strip must be complete — a lite build has no two-photon hardware at all."""
    lite_refs = set(sketches[f"{base_name}_lite"]["cmd_refs"])
    leaked = {c for c in lite_refs if c.startswith(schema.TWO_PHOTON_CMD_PREFIXES)}
    assert not leaked, f"{base_name}_lite references two-photon commands: {sorted(leaked)}"


# --- C14: declaration <-> handler parity ---------------------------------

# The exemption registries live in reacher.schema, not here, so a consumer can
# derive a UI gate from them instead of hand-writing one. See
# schema.INTENTIONALLY_UNHANDLED and schema.KNOWN_FIRMWARE_GAPS for the
# justification of each entry and why the two categories must not be merged.


def _is_exempt(command_name: str, paradigm: str) -> bool:
    for registry in (schema.INTENTIONALLY_UNHANDLED, schema.KNOWN_FIRMWARE_GAPS):
        entry = registry.get(command_name)
        if entry is None:
            continue
        allowed = entry["paradigms"]
        if allowed is None or paradigm in allowed:
            return True
    return False


def test_c14_exemptions_are_all_still_needed(sketches):
    """An exemption that no longer applies must be deleted, not left to rot.

    Two ways an entry goes stale: the command disappears, or firmware grows the
    handler it was excused for. Both must fail here, or the registry becomes a
    place where fixed problems accumulate and new ones hide — and, since a
    downstream UI gate is derived from KNOWN_FIRMWARE_GAPS, a stale gap entry
    means the UI keeps disabling a control that now works.
    """
    names = {spec.name for spec in COMMAND_REGISTRY.values()}
    registries = {**schema.INTENTIONALLY_UNHANDLED, **schema.KNOWN_FIRMWARE_GAPS}

    stale = set(registries) - names
    assert not stale, f"exemptions naming commands that no longer exist: {sorted(stale)}"

    now_handled = []
    for command, entry in schema.KNOWN_FIRMWARE_GAPS.items():
        for paradigm in entry["paradigms"] or []:
            sketch = sketches.get(paradigm)
            if sketch and command in set(sketch["cmd_refs"]):
                now_handled.append(f"{command} on {paradigm}")
    assert not now_handled, (
        "firmware now handles commands still listed in KNOWN_FIRMWARE_GAPS — remove "
        f"them so downstream UI gates stop disabling working controls: {sorted(now_handled)}"
    )


def test_gap_registries_are_well_formed():
    """A consumer derives a UI gate from these, so the shape is a contract."""
    for name, registry in (
        ("INTENTIONALLY_UNHANDLED", schema.INTENTIONALLY_UNHANDLED),
        ("KNOWN_FIRMWARE_GAPS", schema.KNOWN_FIRMWARE_GAPS),
    ):
        for command, entry in registry.items():
            assert entry.get("reason"), f"{name}[{command}] has no justification"
            paradigms = entry["paradigms"]
            assert paradigms is None or (paradigms and isinstance(paradigms, list)), (
                f"{name}[{command}].paradigms must be None (all) or a non-empty list"
            )
            for paradigm in paradigms or []:
                assert paradigm in ALL_PARADIGMS, f"{name}[{command}] names unknown {paradigm!r}"


def test_gap_and_intentional_registries_are_disjoint():
    """Merging the two categories would let a real defect hide as a design decision."""
    overlap = set(schema.INTENTIONALLY_UNHANDLED) & set(schema.KNOWN_FIRMWARE_GAPS)
    assert not overlap, f"a command cannot be both by-design and a gap: {sorted(overlap)}"



def test_every_command_is_handled_by_the_paradigms_it_claims(doc, sketches):
    """C14: if a spec lists a paradigm, that paradigm's sketch must handle the command.

    A command can be handled either in the paradigm's own ParseCommands() switch
    or in the shared handleCommonDeviceCommand() dispatcher.
    """
    common = set(doc["firmware"]["common_dispatcher_cmd_refs"])
    violations = []
    for spec in COMMAND_REGISTRY.values():
        if spec.deprecated:
            continue
        for paradigm in spec.paradigms:
            sketch = sketches.get(paradigm)
            if sketch is None:
                continue  # covered by C10, which owns paradigm/sketch agreement
            if spec.name in common or spec.name in set(sketch["cmd_refs"]):
                continue
            if _is_exempt(spec.name, paradigm):
                continue
            violations.append(f"{spec.name} ({spec.code}) claims {paradigm}, but no handler")
    assert not violations, (
        "COMMAND_REGISTRY declares paradigm support that firmware does not implement.\n"
        "Either add the handler, or add the command to schema.INTENTIONALLY_UNHANDLED "
        "/ schema.KNOWN_FIRMWARE_GAPS with a justification:\n"
        + "\n".join(sorted(violations))
    )


def test_no_sketch_handles_a_command_it_is_not_declared_for(doc, sketches):
    """The converse of C14 — a handler with no declaration is just as much drift."""
    common = set(doc["firmware"]["common_dispatcher_cmd_refs"])
    by_name = {spec.name: spec for spec in COMMAND_REGISTRY.values()}
    violations = []
    for name, sketch in sketches.items():
        for ref in sketch["cmd_refs"]:
            spec = by_name.get(ref)
            if spec is None or ref in common or _is_exempt(ref, name):
                continue
            if name not in spec.paradigms:
                violations.append(f"{name}.ino handles {ref}, not declared in its spec")
    assert not violations, (
        "firmware handles commands COMMAND_REGISTRY does not declare for that paradigm:\n"
        + "\n".join(sorted(violations))
    )


# --- C10: paradigm identity across its seven registries ------------------


def test_paradigm_identity_agrees_across_every_registry(sketches):
    """C10: the same fact is declared in seven places; they must not disagree."""
    from_commands = set(ALL_PARADIGMS)
    from_simulator = set(PARADIGM_TO_SCHEDULE)
    from_sketches = set(sketches)

    assert from_commands == from_simulator, (
        f"commands.ALL_PARADIGMS vs simulator.PARADIGM_TO_SCHEDULE: "
        f"{from_commands ^ from_simulator}"
    )
    assert from_commands == from_sketches, (
        f"commands.ALL_PARADIGMS vs firmware sketch dirs: {from_commands ^ from_sketches}"
    )


def test_schedule_maps_are_mutual_inverses():
    for schedule, paradigm in SCHEDULE_TO_PARADIGM.items():
        assert PARADIGM_TO_SCHEDULE[paradigm] == schedule
    assert set(SCHEDULE_TO_SKETCH) == set(SCHEDULE_TO_PARADIGM)
    for schedule, sketch in SCHEDULE_TO_SKETCH.items():
        assert sketch == f"{SCHEDULE_TO_PARADIGM[schedule]}.ino"


def test_compile_script_builds_exactly_the_discovered_sketches(sketches):
    """compile.sh hardcodes two sketch loops; they must match the tree."""
    text = (FIRMWARE / "compile.sh").read_text()
    listed = set()
    for match in re.finditer(r"for sketch in ([a-z_ ]+);", text):
        listed.update(match.group(1).split())
    assert listed == set(sketches), (
        f"compile.sh sketch loops vs firmware/ dirs: {listed ^ set(sketches)}"
    )


# --- C11: hex artifact coverage ------------------------------------------


def test_every_sketch_has_a_committed_hex(sketches):
    missing = [
        f"{s['board']}/{name}.hex"
        for name, s in sketches.items()
        if not (HEX_ROOT / s["board"] / f"{name}.hex").is_file()
    ]
    assert not missing, f"sketches with no committed hex artifact: {missing}"


# --- C15: unrecognised commands name themselves ---------------------------

# Paradigms deliberately implement different command subsets, so a command that
# reaches a sketch's default case is normal and expected. What is not acceptable
# is reporting it anonymously: "Command not found" with no code leaves an
# operator unable to tell which control silently did nothing, which is how
# KNOWN_FIRMWARE_GAPS["LASER_TRIGGER_LH_ONLY"] survived unnoticed. Every sketch
# must route its default through the shared helper that names the code.


def test_every_sketch_reports_the_code_of_an_unrecognised_command(sketches):
    """C15: no sketch may fall through to an anonymous error."""
    offenders = []
    for name in sketches:
        source = (FIRMWARE / name / f"{name}.ino").read_text()
        if "logUnknownCommand(command)" not in source:
            offenders.append(name)
    assert not offenders, (
        "sketches whose unknown-command path does not name the code: "
        f"{sorted(offenders)}. Use logUnknownCommand(command) so an operator "
        "can tell which control did nothing."
    )


def test_no_sketch_emits_an_anonymous_command_not_found(sketches):
    """C15 converse: the old inline form must not creep back in.

    Written separately because a sketch could satisfy the check above and still
    carry a second, anonymous site.
    """
    offenders = [
        name for name in sketches
        if "Command not found" in (FIRMWARE / name / f"{name}.ino").read_text()
    ]
    assert not offenders, (
        f"sketches with an inline anonymous error: {sorted(offenders)} — "
        "the message belongs in logUnknownCommand so every sketch reports alike."
    )


def test_helper_reports_the_command_field(sketches):
    """The kernel resolves event['command']; the helper must actually emit it."""
    helper = (FIRMWARE / "libraries" / "REACHERDevices" / "src" / "ReacherHelpers.cpp").read_text()
    body = helper.split("void logUnknownCommand(int command)")[1]
    assert '\\"command\\":' in body, "logUnknownCommand must emit a 'command' field"
    assert "Serial.print(command)" in body, "logUnknownCommand must emit the code itself"
