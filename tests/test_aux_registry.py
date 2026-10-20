"""AUX TTL output (14xx): registry, header parity and the Mega-only exemption."""

from pathlib import Path

import pytest

from reacher import schema
from reacher.kernel.commands import COMMAND_REGISTRY, CommandCode, build_command_payload, get_commands_for_paradigm

REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = REPO_ROOT / "firmware"

# Build plan section 3: code -> (name, payload key)
AUX_CONTRACT = {
    1400: ("AUX_DISARM", None),
    1401: ("AUX_ARM", None),
    1403: ("AUX_TEST", None),
    1472: ("AUX_SET_DURATION", "duration"),
    1477: ("AUX_SET_ONSET_DELAY", "delay"),
    1480: ("AUX_SET_MODE", "mode"),
    1481: ("AUX_SET_PROBABILITY", "probability"),
    1482: ("AUX_SET_BLOCK_K", "k"),
    1483: ("AUX_SET_BLOCK_N", "n"),
    1484: ("AUX_SET_SEED", "seed"),
    1485: ("AUX_SET_TEST_FIRE", "enable"),
}

needs_firmware = pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware/ tree absent (wheel-only install)")


@pytest.mark.parametrize("code", sorted(AUX_CONTRACT))
def test_spec_matches_contract(code):
    name, key = AUX_CONTRACT[code]
    spec = COMMAND_REGISTRY[code]
    assert spec.name == name
    assert spec.code == CommandCode[name] == code
    assert spec.payload_key == key
    assert spec.paradigms == ["fr"]
    assert spec.requires_cap == "aux"


def test_no_other_command_requires_a_cap():
    gated = {code for code, spec in COMMAND_REGISTRY.items() if spec.requires_cap}
    assert gated == set(AUX_CONTRACT)


@pytest.mark.parametrize("paradigm", ["pr", "vi", "omission", "pavlovian", "fr_lite", "pr_lite", "vi_lite", "omission_lite"])
def test_aux_only_on_fr(paradigm):
    assert not set(AUX_CONTRACT) & set(get_commands_for_paradigm(paradigm))


def test_aux_offered_to_fr_by_paradigm():
    assert set(AUX_CONTRACT) <= set(get_commands_for_paradigm("fr"))


def test_test_fire_payload_is_bool():
    assert build_command_payload(1485, 1) == {"cmd": 1485, "enable": True}


def test_pin_aux_is_fixed_not_remappable():
    assert "PIN_AUX" in schema.FIXED_PINS
    assert "PIN_AUX" not in schema.PIN_SYMBOL_TO_COMPONENT


def test_mega_only_prefix_is_not_two_photon():
    assert schema.MEGA_ONLY_CMD_PREFIXES == ("AUX_",)
    assert not set(schema.MEGA_ONLY_CMD_PREFIXES) & set(schema.TWO_PHOTON_CMD_PREFIXES)


@needs_firmware
def test_commands_header_holds_all_eleven_codes():
    codes = schema.parse_commands_header(FIRMWARE / "libraries" / "REACHERDevices" / "src" / "Commands.h")
    assert {name: codes.get(name) for name, _ in AUX_CONTRACT.values()} == {
        name: code for code, (name, _) in AUX_CONTRACT.items()
    }


@needs_firmware
def test_pins_header_declares_pin_aux_22():
    assert schema.parse_pins_header(FIRMWARE / "libraries" / "REACHERDevices" / "src" / "Pins.h")["PIN_AUX"] == 22


@needs_firmware
def test_fr_handles_every_aux_command_and_fr_lite_none():
    doc = schema.dump(REPO_ROOT)
    sketches = {s["name"]: s for s in doc["firmware"]["sketches"]}
    names = {name for name, _ in AUX_CONTRACT.values()}
    assert names <= set(sketches["fr"]["cmd_refs"])
    for other in ("pr", "vi", "omission", "pavlovian", "fr_lite", "pr_lite", "vi_lite", "omission_lite"):
        assert not names & set(sketches[other]["cmd_refs"]), other


@needs_firmware
def test_fr_sketch_contract_strings():
    ino = (FIRMWARE / "fr" / "fr.ino").read_text()
    assert '\\"caps\\":\\"aux\\"' in ino
    assert "numSteps < MAX_CHAIN_STEPS" in ino
    # Disarm must drop a pulse in flight, not only clear the armed flag.
    assert "case Cmd::AUX_DISARM: aux.ForceOff();" in ino
