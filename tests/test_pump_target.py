"""Tests for the pump_target persistence module."""

import json
import os
import stat

import pytest

from reacher import pump_target


@pytest.fixture
def tmp_pump_target(tmp_path, monkeypatch):
    """Redirect pump_target storage to a tmp dir and reset the in-memory cache."""
    monkeypatch.setattr(pump_target, "_DIR", str(tmp_path))
    monkeypatch.setattr(pump_target, "_FILE", str(tmp_path / "pump_target.json"))
    monkeypatch.setattr(pump_target, "_cache", {})
    yield tmp_path


class TestPersistence:
    def test_save_and_get_round_trip(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        assert pump_target.get("/dev/ttyUSB0") is True

    def test_get_unknown_port_returns_none(self, tmp_pump_target):
        assert pump_target.get("/dev/ttyNONE") is None

    def test_save_persists_to_disk(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        with open(tmp_pump_target / "pump_target.json") as f:
            data = json.load(f)
        assert data == {"/dev/ttyUSB0": True}

    def test_save_replaces_existing(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        pump_target.save("/dev/ttyUSB0", False)
        assert pump_target.get("/dev/ttyUSB0") is False

    def test_clear_removes_port(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        pump_target.clear("/dev/ttyUSB0")
        assert pump_target.get("/dev/ttyUSB0") is None

    def test_get_all_returns_every_port(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        pump_target.save("/dev/ttyACM0", False)
        assert pump_target.get_all() == {"/dev/ttyUSB0": True, "/dev/ttyACM0": False}

    def test_file_mode_0o600(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        mode = stat.S_IMODE(os.stat(tmp_pump_target / "pump_target.json").st_mode)
        assert mode == 0o600

    def test_load_missing_file_yields_empty_cache(self, tmp_pump_target):
        pump_target.load()
        assert pump_target.get_all() == {}

    def test_load_round_trips_through_disk(self, tmp_pump_target):
        pump_target.save("/dev/ttyUSB0", True)
        pump_target._cache = {}
        pump_target.load()
        assert pump_target.get("/dev/ttyUSB0") is True

    def test_load_corrupt_file_yields_empty_cache(self, tmp_pump_target):
        with open(tmp_pump_target / "pump_target.json", "w") as f:
            f.write("not json")
        pump_target.load()
        assert pump_target.get_all() == {}
