"""Tests for the pin_overrides persistence + validation module."""

import json
import os
import stat

import pytest

from reacher import pin_overrides


@pytest.fixture
def tmp_overrides(tmp_path, monkeypatch):
    """Redirect pin_overrides storage to a tmp dir and reset the in-memory cache."""
    monkeypatch.setattr(pin_overrides, "_DIR", str(tmp_path))
    monkeypatch.setattr(pin_overrides, "_FILE", str(tmp_path / "pin_overrides.json"))
    monkeypatch.setattr(pin_overrides, "_cache", {})
    yield tmp_path


class TestPersistence:
    def test_save_and_get_round_trip(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11, "pump": 4})
        assert pin_overrides.get("/dev/ttyUSB0") == {"cue": 11, "pump": 4}

    def test_get_unknown_port_returns_empty(self, tmp_overrides):
        assert pin_overrides.get("/dev/ttyNONE") == {}

    def test_save_persists_to_disk(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11})
        with open(tmp_overrides / "pin_overrides.json") as f:
            data = json.load(f)
        assert data == {"/dev/ttyUSB0": {"board": None, "pins": {"cue": 11}}}

    def test_save_with_board_persists_board(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11}, board="uno")
        with open(tmp_overrides / "pin_overrides.json") as f:
            data = json.load(f)
        assert data == {"/dev/ttyUSB0": {"board": "uno", "pins": {"cue": 11}}}

    def test_save_replaces_existing(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11, "pump": 4})
        pin_overrides.save("/dev/ttyUSB0", {"cue": 5})
        assert pin_overrides.get("/dev/ttyUSB0") == {"cue": 5}

    def test_clear_removes_port(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11})
        pin_overrides.clear("/dev/ttyUSB0")
        assert pin_overrides.get("/dev/ttyUSB0") == {}

    def test_save_empty_assignments_clears(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11})
        pin_overrides.save("/dev/ttyUSB0", {})
        assert pin_overrides.get("/dev/ttyUSB0") == {}

    def test_file_mode_0o600(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11})
        mode = stat.S_IMODE(os.stat(tmp_overrides / "pin_overrides.json").st_mode)
        assert mode == 0o600

    def test_load_round_trip(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11})
        # Reset cache and reload from disk
        pin_overrides._cache = {}
        pin_overrides.load()
        assert pin_overrides.get("/dev/ttyUSB0") == {"cue": 11}

    def test_load_missing_file_starts_empty(self, tmp_overrides):
        pin_overrides._cache = {"stale": {"cue": 99}}
        pin_overrides.load()
        assert pin_overrides.get_all() == {}

    def test_load_handles_malformed_json(self, tmp_overrides):
        (tmp_overrides / "pin_overrides.json").write_text("{not valid json")
        pin_overrides._cache = {"stale": {"cue": 99}}
        pin_overrides.load()
        # Falls back to empty rather than crashing
        assert pin_overrides.get_all() == {}

    def test_load_filters_non_dict_entries(self, tmp_overrides):
        (tmp_overrides / "pin_overrides.json").write_text(
            json.dumps({"/dev/ttyUSB0": {"cue": 11}, "garbage": "string"})
        )
        pin_overrides.load()
        assert pin_overrides.get_all() == {"/dev/ttyUSB0": {"board": None, "pins": {"cue": 11}}}

    def test_load_old_flat_format_migrates_as_wildcard(self, tmp_overrides):
        (tmp_overrides / "pin_overrides.json").write_text(
            json.dumps({"/dev/ttyUSB0": {"lever_rh": 12, "cue": 3}})
        )
        pin_overrides.load()
        # Old flat format migrated: board=None (wildcard), pins preserved
        assert pin_overrides.get("/dev/ttyUSB0") == {"lever_rh": 12, "cue": 3}
        assert pin_overrides.get_all()["/dev/ttyUSB0"]["board"] is None


class TestBoardFiltering:
    def test_board_match_returns_pins(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11}, board="uno")
        assert pin_overrides.get("/dev/ttyUSB0", current_board="uno") == {"cue": 11}

    def test_board_mismatch_returns_empty(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 44}, board="mega")
        assert pin_overrides.get("/dev/ttyUSB0", current_board="uno") == {}

    def test_wildcard_board_applies_to_any(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11}, board=None)
        assert pin_overrides.get("/dev/ttyUSB0", current_board="mega") == {"cue": 11}
        assert pin_overrides.get("/dev/ttyUSB0", current_board="uno") == {"cue": 11}

    def test_saved_board_applies_when_detection_fails(self, tmp_overrides):
        # When current_board is None (detection failed), apply regardless of saved board
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11}, board="uno")
        assert pin_overrides.get("/dev/ttyUSB0", current_board=None) == {"cue": 11}

    def test_board_comparison_is_case_insensitive(self, tmp_overrides):
        pin_overrides.save("/dev/ttyUSB0", {"cue": 11}, board="UNO")
        assert pin_overrides.get("/dev/ttyUSB0", current_board="uno") == {"cue": 11}


class TestValidator:
    def test_uno_pwm_pin_ok(self):
        # Cue requires PWM; UNO PWM pins are 3,5,6,9,10,11
        assert pin_overrides.validate_pin(376, 11, "uno") is None

    def test_uno_non_pwm_pin_role_violation(self):
        v = pin_overrides.validate_pin(376, 4, "uno")
        assert v is not None
        assert v["error"] == "pin_role_violation"
        assert v["required"] == "pwm"
        assert v["got"] == 4

    def test_out_of_range_pin(self):
        v = pin_overrides.validate_pin(376, 50, "uno")
        assert v is not None
        assert v["error"] == "pin_out_of_range"
        assert v["board"] == "uno"

    def test_mega_pin_44_pwm_ok_for_cue(self):
        assert pin_overrides.validate_pin(376, 44, "mega") is None

    def test_mega_digital_only_command_allows_high_pin(self):
        # Pump doesn't require PWM; pin 30 is fine on Mega
        assert pin_overrides.validate_pin(476, 30, "mega") is None

    def test_mega_high_pin_rejected_on_uno(self):
        v = pin_overrides.validate_pin(476, 30, "uno")
        assert v is not None
        assert v["error"] == "pin_out_of_range"

    def test_pin_2_excluded_for_pwm(self):
        # Pin 2 is digital + INT0 but NOT PWM on UNO
        v = pin_overrides.validate_pin(376, 2, "uno")
        assert v is not None
        assert v["error"] == "pin_role_violation"

    def test_unknown_code_passes_through(self):
        # Non-pin command codes return None (not our concern)
        assert pin_overrides.validate_pin(101, 99, "uno") is None

    def test_default_board_is_uno(self):
        # None board falls back to UNO constraints
        v = pin_overrides.validate_pin(376, 50, None)
        assert v is not None and v["error"] == "pin_out_of_range"


class TestComponentMap:
    def test_all_components_have_codes(self):
        expected = {"cue", "cue2", "pump", "pump2", "lick", "laser",
                    "microscope_trigger", "lever_rh", "lever_lh"}
        assert set(pin_overrides.SET_PIN_CODE_FOR.keys()) == expected

    def test_no_microscope_timestamp_in_map(self):
        # Timestamp pin is intentionally fixed in firmware (INT0); not remappable.
        assert "microscope_timestamp" not in pin_overrides.SET_PIN_CODE_FOR

    def test_codes_match_command_codes(self):
        from reacher.kernel.commands import CommandCode
        assert pin_overrides.SET_PIN_CODE_FOR["cue"] == int(CommandCode.CUE_SET_PIN)
        assert pin_overrides.SET_PIN_CODE_FOR["lever_rh"] == int(CommandCode.LEVER_RH_SET_PIN)
        assert pin_overrides.SET_PIN_CODE_FOR["microscope_trigger"] == int(CommandCode.MICROSCOPE_SET_TRIG_PIN)
