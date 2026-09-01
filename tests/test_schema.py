"""Tests for the machine-readable registry export in reacher.schema.

Two things matter here beyond "the dump has the right keys":

1. The sanity floors must actually fire. Every parser in schema.py asserts a
   minimum yield precisely so that a reformat of Commands.h degrades to a loud
   failure rather than a confident "everything is in sync". A floor that is
   never exercised is not a floor, so each one gets a golden-negative.
2. The installed-wheel path must report ``present: False`` and warn, never
   crash and never silently pass.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from reacher import schema

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = REPO_ROOT / "firmware"
LIB_SRC = FIRMWARE / "libraries" / "REACHERDevices" / "src"

needs_firmware = pytest.mark.skipif(
    not FIRMWARE.is_dir(), reason="firmware source not present (installed-wheel run)"
)


# --- Document shape ------------------------------------------------------


def test_dump_has_stable_envelope():
    doc = schema.dump(REPO_ROOT)
    assert doc["schema_version"] == schema.SCHEMA_VERSION
    assert set(doc) == {"schema_version", "generated_from", "python", "firmware", "errors", "warnings"}
    assert doc["errors"] == []
    assert doc["generated_from"]["root"] == str(REPO_ROOT)


def test_python_section_matches_live_registries():
    from reacher.kernel.commands import ALL_PARADIGMS, COMMAND_REGISTRY, CommandCode
    from reacher.pin_overrides import COMPONENT_KEYS, PIN_CONSTRAINTS
    from reacher.uploader.boards import BOARD_PROFILES

    p = schema.dump(REPO_ROOT)["python"]
    assert len(p["commands"]) == len(COMMAND_REGISTRY)
    assert len(p["command_code_enum"]) == len(list(CommandCode))
    assert p["component_keys"] == list(COMPONENT_KEYS)
    assert len(p["pin_constraints"]) == len(PIN_CONSTRAINTS)
    assert {b["board_id"] for b in p["board_profiles"]} == set(BOARD_PROFILES)
    assert p["paradigms"]["all"] == list(ALL_PARADIGMS)


def test_pin_sets_are_exported_per_board():
    p = schema.dump(REPO_ROOT)["python"]
    for board, sets in p["pin_sets"].items():
        assert set(sets) == {"digital", "pwm", "interrupt", "pcint0"}, board
        # PWM and PCINT0 are subsets of the digital range on every real board.
        assert set(sets["pwm"]) <= set(sets["digital"]), board


# --- Firmware parsing ----------------------------------------------------


@needs_firmware
def test_firmware_section_is_present_and_complete():
    f = schema.dump(REPO_ROOT)["firmware"]
    assert f["present"] is True
    assert len(f["commands_h"]) >= schema.MIN_COMMANDS
    assert len(f["pins_h"]) >= schema.MIN_PIN_CONSTANTS
    assert len(f["sketches"]) >= schema.MIN_SKETCHES
    assert f["common_dispatcher_cmd_refs"], "ReacherHelpers.cpp should reference Cmd:: constants"


@needs_firmware
def test_sketches_are_discovered_not_hardcoded():
    """Sketch identity is derived from the filesystem, so a new one needs no edit here."""
    sketches = {s["name"]: s for s in schema.dump(REPO_ROOT)["firmware"]["sketches"]}
    assert {"fr", "pr", "vi", "omission", "pavlovian"} <= set(sketches)
    for name, s in sketches.items():
        assert s["lite"] == name.endswith("_lite")
        assert s["board"] == ("uno" if s["lite"] else "mega")
        assert s["base"] == (name[: -len("_lite")] if s["lite"] else name)
    # Pavlovian overflows UNO flash even stripped, so it must have no lite twin.
    assert "pavlovian_lite" not in sketches


@needs_firmware
def test_pin_symbol_map_covers_every_component():
    """Every backend component must map back to a firmware Pins.h symbol.

    The microscope *timestamp* pin is deliberately excluded: it is fixed at INT0
    in firmware and must never be offered as remappable.
    """
    doc = schema.dump(REPO_ROOT)
    components = set(doc["python"]["component_keys"])
    mapped = set(schema.PIN_SYMBOL_TO_COMPONENT.values())
    assert components == mapped, f"unmapped components: {components ^ mapped}"
    assert "PIN_MICROSCOPE_TS" in schema.FIXED_PINS
    assert "PIN_MICROSCOPE_TS" not in schema.PIN_SYMBOL_TO_COMPONENT


@needs_firmware
def test_pin_symbol_map_targets_exist_in_pins_header():
    pins = schema.dump(REPO_ROOT)["firmware"]["pins_h"]
    for symbol in schema.PIN_SYMBOL_TO_COMPONENT:
        assert symbol in pins, f"{symbol} not found in Pins.h"


# --- Device-name namespaces ----------------------------------------------


@needs_firmware
def test_three_device_namespaces_are_distinct():
    """Firmware emits device names in three namespaces keyed by message level.

    Conflating them is what produced the LICK/LICK_CIRCUIT duplicate entry, so
    the export must keep them separate rather than merging into one set.
    """
    names = schema.dump(REPO_ROOT)["firmware"]["device_names"]
    assert set(names) == {"config", "param", "event", "post_kernel", "by_level"}
    assert "LICK" in names["config"]
    assert "LICK_CIRCUIT" in names["event"]
    assert "LICK_CIRCUIT" not in names["config"]
    # The 007 path spells the two cues and pumps with an index suffix; 000 does not.
    assert {"CUE_1", "CUE_2", "PUMP_1", "PUMP_2"} <= set(names["event"])
    assert {"CUE", "CUE2", "PUMP", "PUMP2"} <= set(names["config"])
    # Levers are emitted through a ternary; the parser must still find them.
    assert {"LEVER_RH", "LEVER_LH"} <= set(names["event"])


# --- Sanity floors: the golden-negatives ---------------------------------


def test_commands_header_floor_fires_on_truncated_header(tmp_path):
    header = tmp_path / "Commands.h"
    header.write_text("namespace Cmd {\nconstexpr int SESSION_END = 100;\n}\n")
    with pytest.raises(schema.SchemaError, match="only 1 constants"):
        schema.parse_commands_header(header)


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda i: f"constexpr int CMD_{i} {{{i}}};", id="brace-init"),
        pytest.param(lambda i: f"constexpr int CMD_{i} = 0x{i:02X};", id="hex-literal"),
        pytest.param(lambda i: f"#define CMD_{i} {i}", id="define"),
        pytest.param(lambda i: f"constexpr uint16_t CMD_{i} = {i};", id="widened-type"),
    ],
)
def test_commands_header_floor_fires_on_restyled_header(tmp_path, render):
    """A style the regex cannot read must fail loudly, not report zero drift.

    Extra whitespace is tolerated by design (`\\s+`); these are the forms that
    genuinely slip past it, and each is a plausible future edit to Commands.h.
    """
    header = tmp_path / "Commands.h"
    header.write_text("\n".join(render(i) for i in range(60)))
    with pytest.raises(schema.SchemaError):
        schema.parse_commands_header(header)


def test_commands_header_tolerates_extra_whitespace(tmp_path):
    """The converse: reformatting that only changes spacing must still parse."""
    header = tmp_path / "Commands.h"
    header.write_text("\n".join(f"constexpr  int  CMD_{i}  =  {i} ;" for i in range(60)))
    assert len(schema.parse_commands_header(header)) == 60


def test_pins_header_floor_fires(tmp_path):
    header = tmp_path / "Pins.h"
    header.write_text("constexpr int8_t PIN_CUE = 3;\n")
    with pytest.raises(schema.SchemaError, match="only 1 pin constants"):
        schema.parse_pins_header(header)


def test_sketch_discovery_floor_fires(tmp_path):
    (tmp_path / "fr").mkdir()
    (tmp_path / "fr" / "fr.ino").write_text("// only one\n")
    with pytest.raises(schema.SchemaError, match="only 1"):
        schema.discover_sketches(tmp_path)


def test_sketch_discovery_ignores_mismatched_dir_and_ino(tmp_path):
    """Arduino requires the sketch dir and .ino to share a name; stray files are not sketches."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "scratch.ino").write_text("// not a sketch\n")
    for name in ("fr", "pr", "vi", "omission", "pavlovian"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.ino").write_text("// sketch\n")
    found = {s.name for s in schema.discover_sketches(tmp_path)}
    assert found == {"fr", "pr", "vi", "omission", "pavlovian"}


# --- Installed-wheel degradation -----------------------------------------


def test_firmware_absent_reports_present_false_and_warns(tmp_path):
    """A wheel-shaped tree must degrade loudly, never crash and never pass silently."""
    (tmp_path / "src" / "reacher" / "kernel").mkdir(parents=True)
    (tmp_path / "src" / "reacher" / "kernel" / "commands.py").write_text("")

    doc = schema.dump(tmp_path)
    assert doc["firmware"]["present"] is False
    assert "firmware" in doc["firmware"]["reason"]
    assert any("UNAVAILABLE" in w for w in doc["warnings"]), (
        "consumers must be told that firmware checks cannot pass, not merely that firmware is missing"
    )


def test_find_repo_root_ignores_a_bare_firmware_checkout(tmp_path):
    """The archived reacher-firmware sibling must never be mistaken for the repo."""
    fw = tmp_path / "reacher-firmware" / "libraries" / "REACHERDevices" / "src"
    fw.mkdir(parents=True)
    (fw / "Commands.h").write_text("constexpr int SESSION_END = 100;\n")
    assert schema.find_repo_root(fw) is None


# --- CLI -----------------------------------------------------------------


def test_cli_emits_valid_json_and_exits_zero():
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "reacher.schema", "dump", "--json"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["schema_version"] == schema.SCHEMA_VERSION
    assert doc["python"]["commands"]


def test_cli_reads_the_checkout_not_the_installed_wheel():
    """PYTHONPATH precedence is load-bearing: the working tree must win.

    Without this, a user with reacher2p installed and a source checkout would
    get ground truth from the wheel while their agent edits the checkout —
    stale codes, checks passing against the wrong tree, no error anywhere.
    """
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-c",
         "import reacher, sys; sys.stdout.write(reacher.__file__)"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith(str(REPO_ROOT / "src")), (
        f"checkout did not win over site-packages: imported {proc.stdout}"
    )


# --- The post-kernel namespace -------------------------------------------


@needs_firmware
def test_post_kernel_namespace_applies_the_kernel_rewrites():
    """Anything downstream of the kernel sees rewritten names, never firmware's.

    `update_behavioral_events` rewrites SWITCH_LEVER -> LEVER_RH/LEVER_LH and
    LICK_CIRCUIT -> LICK before emitting. Checking a frontend table against the
    raw 007 set would therefore flag correct names as wrong — the same class of
    mistake as reading a level-000 name against the 007 namespace.
    """
    names = schema.dump(REPO_ROOT)["firmware"]["device_names"]
    raw, post = set(names["event"]), set(names["post_kernel"])

    for firmware_name in schema.POST_KERNEL_EVENT_REWRITES:
        assert firmware_name in raw, f"{firmware_name} is not emitted; the rewrite is dead"
        assert firmware_name not in post, f"{firmware_name} should be rewritten before emit"

    assert "LICK" in post and "LICK_CIRCUIT" not in post
    assert {"LEVER_RH", "LEVER_LH"} <= post and "SWITCH_LEVER" not in post
    # Everything without a rewrite passes through unchanged.
    assert (raw - set(schema.POST_KERNEL_EVENT_REWRITES)) <= post


@needs_firmware
def test_every_rewrite_target_is_a_name_the_kernel_can_emit():
    """A rewrite pointing at a name nothing recognizes would be worse than none."""
    names = schema.dump(REPO_ROOT)["firmware"]["device_names"]
    known = set(names["config"]) | set(names["event"])
    for source, targets in schema.POST_KERNEL_EVENT_REWRITES.items():
        for target in targets:
            assert target in known, f"{source} rewrites to unknown device {target!r}"
