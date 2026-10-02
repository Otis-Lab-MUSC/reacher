"""The reward-chain pump target must be reachable by the UI.

A stale persisted 221 (pump2=true) is replayed on every connect and silently
retargets the reward chain to PUMP_2. The UI can only warn about it if the
host-held target is on the wire: the connect response, the replay's `log`
event, the 221 response, and GET /config. These run the real app against the
simulated port (no board), so a board reset on open is assumed, not observed.
"""

import pytest
from fastapi.testclient import TestClient

from reacher import pump_target
from reacher.api.app import app
from reacher.api.middleware.auth import API_KEY
from reacher.api.routers import websocket as ws

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}", "X-Reacher-App": "1"}
PORT = "SIMULATOR"


@pytest.fixture
def stale_pump2(tmp_path, monkeypatch):
    """Persisted pump2=true for the simulator port, in an isolated store."""
    monkeypatch.setattr(pump_target, "_DIR", str(tmp_path))
    monkeypatch.setattr(pump_target, "_FILE", str(tmp_path / "pump_target.json"))
    monkeypatch.setattr(pump_target, "_cache", {})
    pump_target.save(PORT, True)
    # TestClient's lifespan calls pump_target.load(); it reads the file just written.


@pytest.fixture
def log_events(monkeypatch):
    seen = []
    real = ws.enqueue_event

    def spy(session_id, event_type, data):
        seen.append((event_type, data))
        return real(session_id, event_type, data)

    monkeypatch.setattr(ws, "enqueue_event", spy)
    return seen


def _connect(client):
    sid = client.post("/api/sessions", json={"port": PORT, "paradigm": "fr"}, headers=AUTH_HEADER).json()["session_id"]
    resp = client.post(f"/api/serial/{sid}/connect", headers=AUTH_HEADER)
    assert resp.status_code == 200
    return sid, resp.json()


def test_connect_surfaces_replayed_pump2(stale_pump2, log_events):
    with TestClient(app) as client:
        _, body = _connect(client)

    assert body["replayed_pump_target"] is True
    assert body["pump_target"] == "PUMP2"
    replay = [d for t, d in log_events if t == "log" and d.get("kind") == "pump_target_replay"]
    assert len(replay) == 1
    assert replay[0]["pump2"] is True
    assert replay[0]["message"]  # still renders as a plain log line


def test_221_response_and_config_follow_the_last_send(stale_pump2):
    with TestClient(app) as client:
        sid, _ = _connect(client)
        before = client.get(f"/api/hardware/{sid}/config", headers=AUTH_HEADER).json()
        assert before["pump_target"] == "PUMP2"

        resp = client.post(f"/api/hardware/{sid}/command", json={"code": 221, "value": 0}, headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["pump_target"] == "PUMP"
        after = client.get(f"/api/hardware/{sid}/config", headers=AUTH_HEADER).json()
        assert after["pump_target"] == "PUMP"

        resp = client.post(f"/api/hardware/{sid}/command", json={"code": 221, "value": 1}, headers=AUTH_HEADER)
        assert resp.json()["pump_target"] == "PUMP2"
        assert client.get(f"/api/hardware/{sid}/config", headers=AUTH_HEADER).json()["pump_target"] == "PUMP2"


def test_no_replay_reports_primary_and_unknown_config(tmp_path, monkeypatch):
    monkeypatch.setattr(pump_target, "_DIR", str(tmp_path))
    monkeypatch.setattr(pump_target, "_FILE", str(tmp_path / "pump_target.json"))
    monkeypatch.setattr(pump_target, "_cache", {})
    with TestClient(app) as client:
        sid, body = _connect(client)
        cfg = client.get(f"/api/hardware/{sid}/config", headers=AUTH_HEADER).json()

    assert body["replayed_pump_target"] is None
    # Assumes the board reset on open (DTR); that part is NOT COVERED without hardware.
    assert body["pump_target"] == "PUMP"
    assert cfg["pump_target"] is None
