"""Tests for the command registry module."""

import pytest
from reacher.kernel.commands import (
    COMMAND_REGISTRY,
    CommandCode,
    CommandSpec,
    PARADIGMS,
    build_command_payload,
    get_commands_for_paradigm,
)


class TestCommandCode:
    def test_session_end(self):
        assert CommandCode.SESSION_END == 100

    def test_session_start(self):
        assert CommandCode.SESSION_START == 101

    def test_laser_set_frequency(self):
        assert CommandCode.LASER_SET_FREQUENCY == 671

    def test_lever_lh_set_active(self):
        assert CommandCode.LEVER_LH_SET_ACTIVE == 1381

    def test_all_pavlovian_params_in_range(self):
        pav_codes = [c for c in CommandCode if 206 <= c <= 219]
        assert len(pav_codes) == 14


class TestCommandRegistry:
    def test_all_codes_present(self):
        """Every CommandCode enum value should be in the registry."""
        for code in CommandCode:
            assert code.value in COMMAND_REGISTRY, f"{code.name} ({code.value}) missing from registry"

    def test_registry_spec_types(self):
        for code, spec in COMMAND_REGISTRY.items():
            assert isinstance(spec, CommandSpec)
            assert spec.code == code

    def test_deprecated_commands_marked(self):
        deprecated = [s for s in COMMAND_REGISTRY.values() if s.deprecated]
        deprecated_names = {s.name for s in deprecated}
        assert "CUE_SET_TRACE" in deprecated_names
        assert "PUMP_SET_TRACE" in deprecated_names
        assert "LASER_SET_TRACE" in deprecated_names

    def test_laser_included_in_pavlovian(self):
        """Base laser commands (arm, disarm, test, freq, dur, mode) include pavlovian."""
        base_laser = [600, 601, 603, 671, 672, 681, 682]
        for code in base_laser:
            spec = COMMAND_REGISTRY[code]
            assert "pavlovian" in spec.paradigms, f"{spec.name} missing pavlovian"


class TestGetCommandsForParadigm:
    def test_fr_includes_set_ratio(self):
        cmds = get_commands_for_paradigm("fr")
        assert 201 in cmds

    def test_pavlovian_includes_pav_params(self):
        cmds = get_commands_for_paradigm("pavlovian")
        for code in range(206, 220):
            assert code in cmds, f"PAV command {code} missing for pavlovian"

    def test_pavlovian_includes_laser(self):
        """Pavlovian paradigm includes base laser commands and pav-specific codes."""
        cmds = get_commands_for_paradigm("pavlovian")
        for code in [600, 601, 603, 671, 672, 681, 682, 691, 692, 693, 694, 695]:
            assert code in cmds, f"Code {code} missing from pavlovian commands"

    def test_deprecated_excluded(self):
        for paradigm in PARADIGMS:
            cmds = get_commands_for_paradigm(paradigm)
            for spec in cmds.values():
                assert not spec.deprecated

    def test_case_insensitive(self):
        assert get_commands_for_paradigm("FR") == get_commands_for_paradigm("fr")


class TestBuildCommandPayload:
    def test_simple_command(self):
        payload = build_command_payload(101)
        assert payload == {"cmd": 101}

    def test_command_with_value(self):
        payload = build_command_payload(371, 8000)
        assert payload == {"cmd": 371, "frequency": 8000}

    def test_ratio_command(self):
        payload = build_command_payload(201, 5)
        assert payload == {"cmd": 201, "ratio": 5}

    def test_unknown_code_raises(self):
        with pytest.raises(ValueError, match="Unknown command code"):
            build_command_payload(99999)

    def test_value_ignored_when_no_payload_key(self):
        # SESSION_START has no payload_key, so value is silently ignored
        payload = build_command_payload(101, 42)
        assert payload == {"cmd": 101}
