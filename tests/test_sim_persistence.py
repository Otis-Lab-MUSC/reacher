"""Simulator slots do not persist pump target or pin overrides.

SIM1, SIM2, ... are handed to whichever session is created next, so a value
saved "for SIM1" was replayed into an unrelated later session (pressure test
#6: a PR session delivered 0 infusions because a deleted session had left
pump2=true on SIM1). Simulator values now live in memory for the life of the
session, are cleared when it is destroyed, and never reach the JSON files.
Real serial ports keep their per-rig persistence unchanged.
"""

import json

import pytest
from fastapi.testclient import TestClient

from reacher import pin_overrides, pump_target
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH = {"Authorization": f"Bearer {API_KEY}", "X-Reacher-App": "1"}
REAL = "/dev/ttyACM0"


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Both stores redirected to a tmp dir, with empty caches."""
    monkeypatch.setattr(pump_target, "_DIR", str(tmp_path))
    monkeypatch.setattr(pump_target, "_FILE", str(tmp_path / "pump_target.json"))
    monkeypatch.setattr(pump_target, "_cache", {})
    monkeypatch.setattr(pump_target, "_volatile", {})
    monkeypatch.setattr(pin_overrides, "_DIR", str(tmp_path))
    monkeypatch.setattr(pin_overrides, "_FILE", str(tmp_path / "pin_overrides.json"))
    monkeypatch.setattr(pin_overrides, "_cache", {})
    monkeypatch.setattr(pin_overrides, "_volatile", {})
    return tmp_path


def _on_disk(path):
    return json.loads(path.read_text()) if path.exists() else {}


class TestStores:
    @pytest.mark.parametrize("port", ["SIMULATOR", "SIM1", "SIM12"])
    def test_simulator_values_are_held_but_never_written(self, stores, port):
        pump_target.save(port, True)
        pin_overrides.save(port, {"cue": 9}, board="mega")
        assert pump_target.get(port) is True
        assert pin_overrides.get(port, "mega") == {"cue": 9}
        assert not (stores / "pump_target.json").exists()
        assert not (stores / "pin_overrides.json").exists()
        assert pump_target.get_all() == {}
        assert pin_overrides.get_all() == {}

    def test_clear_drops_the_simulator_value(self, stores):
        pump_target.save("SIM1", True)
        pin_overrides.save("SIM1", {"cue": 9})
        pump_target.clear("SIM1")
        pin_overrides.clear("SIM1")
        assert pump_target.get("SIM1") is None
        assert pin_overrides.get("SIM1") == {}

    def test_real_ports_still_persist(self, stores):
        pump_target.save(REAL, True)
        pin_overrides.save(REAL, {"cue": 11}, board="mega")
        assert _on_disk(stores / "pump_target.json") == {REAL: True}
        assert _on_disk(stores / "pin_overrides.json") == {REAL: {"board": "mega", "pins": {"cue": 11}}}

    def test_load_drops_simulator_entries_saved_by_older_builds(self, stores):
        (stores / "pump_target.json").write_text(json.dumps({"SIMULATOR": True, "SIM1": True, REAL: False}))
        (stores / "pin_overrides.json").write_text(json.dumps({
            "SIMULATOR": {"board": None, "pins": {"cue": 9}},
            REAL: {"board": "mega", "pins": {"cue": 11}},
        }))
        pump_target.load()
        pin_overrides.load()
        assert pump_target.get_all() == {REAL: False}
        assert list(pin_overrides.get_all()) == [REAL]
        # the next write prunes the legacy keys from disk
        pump_target.save(REAL, True)
        pin_overrides.save(REAL, {"cue": 12})
        assert _on_disk(stores / "pump_target.json") == {REAL: True}
        assert list(_on_disk(stores / "pin_overrides.json")) == [REAL]

    @pytest.mark.parametrize("port", ["SIM1\n", "sim1", "SIM0", "SIMULATOR2"])
    def test_lookalike_names_are_not_simulator_slots(self, stores, port):
        pump_target.save(port, True)
        assert _on_disk(stores / "pump_target.json") == {port: True}


class TestOverHttp:
    def _new(self, client):
        body = client.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": "fr"}, headers=AUTH).json()
        connect = client.post(f"/api/serial/{body['session_id']}/connect", json={}, headers=AUTH)
        assert connect.status_code == 200
        return body["session_id"], body["port"], connect.json()

    def test_a_deleted_sessions_settings_never_reach_the_next_session_on_its_slot(self, stores):
        with TestClient(create_app()) as client:
            sid, port, _ = self._new(client)
            assert port == "SIM1"
            assert client.post(f"/api/hardware/{sid}/command", json={"code": 221, "value": 1}, headers=AUTH).status_code == 200
            pins = client.put(f"/api/hardware/{sid}/pins", json={"assignments": {"cue": 9}}, headers=AUTH)
            assert pins.status_code == 200, pins.text
            assert client.get(f"/api/hardware/{sid}/config", headers=AUTH).json()["pump_target"] == "PUMP2"
            client.delete(f"/api/sessions/{sid}", headers=AUTH)

            sid2, port2, connect = self._new(client)
            assert port2 == "SIM1"
            assert connect["replayed_pump_target"] is None
            assert connect["replayed_pins"] == {}
            assert connect["pump_target"] == "PUMP"
            assert client.get(f"/api/hardware/{sid2}/config", headers=AUTH).json()["pump_target"] is None
            client.delete(f"/api/sessions/{sid2}", headers=AUTH)

        assert not (stores / "pump_target.json").exists()
        assert not (stores / "pin_overrides.json").exists()

    def test_concurrent_slots_keep_their_own_values(self, stores):
        with TestClient(create_app()) as client:
            a, pa, _ = self._new(client)
            b, pb, _ = self._new(client)
            assert (pa, pb) == ("SIM1", "SIM2")
            client.post(f"/api/hardware/{a}/command", json={"code": 221, "value": 1}, headers=AUTH)
            assert client.get(f"/api/hardware/{a}/config", headers=AUTH).json()["pump_target"] == "PUMP2"
            assert client.get(f"/api/hardware/{b}/config", headers=AUTH).json()["pump_target"] is None
            for sid in (a, b):
                client.delete(f"/api/sessions/{sid}", headers=AUTH)
