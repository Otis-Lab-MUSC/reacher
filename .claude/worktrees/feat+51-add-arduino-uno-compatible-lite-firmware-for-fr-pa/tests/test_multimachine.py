"""Multi-machine IoT control validation (labrynth issue #16).

Automated coverage for the backend half of the multi-host feature: pairing
(rotating code + brute-force rate limit), discovery (three-source merge +
unicast/``REACHER_BROKER_URL`` fallback), and the transparent proxy (per-machine
credential routing + WebSocket token relay).

The acceptance criteria that require 2+ live remote hosts (concurrent sessions,
real mDNS on a managed switch, per-host hardware panels) cannot be asserted in
CI — those are covered by docs/multi-host-validation.md in the labrynth repo.
What *is* asserted here is the property that makes cross-host isolation possible:
every routed request carries the credentials/URL of its own machine and nothing
bleeds between machines.
"""

import time

import pytest
from unittest.mock import patch, Mock, AsyncMock
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.api.routers import pairing as pairing_router
from reacher import pairing as pairing_core
from reacher import discovery

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    """TestClient with REACHER mocked. Lifespan starts pairing rotation and a
    background subnet-scan task (cancelled on teardown) — same as test_api.py."""
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        MockReacher.return_value = Mock()
        app = create_app()
        with TestClient(app) as c:
            yield c


# --------------------------------------------------------------------------- #
# Pairing: rotating code + sliding-window rate limit
# --------------------------------------------------------------------------- #


@pytest.fixture
def fresh_pairing(tmp_path, monkeypatch):
    """Reset the per-IP rate-limit buckets and pin a known code on a tmp paired file."""
    pairing_router._attempt_timestamps.clear()
    monkeypatch.setattr(pairing_core, "_PAIRED_DIR", str(tmp_path))
    monkeypatch.setattr(pairing_core, "_PAIRED_FILE", str(tmp_path / "paired"))
    monkeypatch.setattr(pairing_core, "_current_code", "123456")
    monkeypatch.setattr(pairing_core, "_rotation_start", time.monotonic())
    monkeypatch.setattr(pairing_core, "_paired", False)
    yield
    pairing_router._attempt_timestamps.clear()


class TestPairingRateLimit:
    def test_five_bad_attempts_then_429(self, client, fresh_pairing):
        for i in range(pairing_router._RATE_LIMIT):
            r = client.post("/api/pairing/claim", json={"code": "000000"})
            assert r.status_code == 401, f"attempt {i} should be 401 (wrong code)"
        # 6th attempt is blocked by the limiter before the code is even checked.
        r = client.post("/api/pairing/claim", json={"code": "000000"})
        assert r.status_code == 429

    def test_window_evicts_old_attempts(self, client, fresh_pairing):
        # Pre-load the bucket with a full set of *expired* timestamps; they must be
        # evicted so the next attempt is allowed through to code validation (401),
        # not rate-limited (429).
        old = time.monotonic() - (pairing_router._RATE_WINDOW + 1.0)
        pairing_router._attempt_timestamps["testclient"].extend([old] * pairing_router._RATE_LIMIT)
        r = client.post("/api/pairing/claim", json={"code": "000000"})
        assert r.status_code == 401

    def test_valid_code_returns_api_key_and_pairs(self, client, fresh_pairing):
        r = client.post("/api/pairing/claim", json={"code": "123456"})
        assert r.status_code == 200
        assert r.json()["api_key"] == API_KEY
        assert pairing_core.is_paired() is True


class TestPairingCode:
    def test_verify_empty_code_rejected(self, monkeypatch):
        monkeypatch.setattr(pairing_core, "_current_code", "")
        assert pairing_core.verify_code("123456") is False

    def test_verify_matches_and_strips_whitespace(self, monkeypatch):
        monkeypatch.setattr(pairing_core, "_current_code", "654321")
        assert pairing_core.verify_code("654321") is True
        assert pairing_core.verify_code("  654321 ") is True
        assert pairing_core.verify_code("000000") is False

    def test_seconds_until_rotation_bounded(self, monkeypatch):
        monkeypatch.setattr(pairing_core, "_rotation_start", time.monotonic())
        s = pairing_core.seconds_until_rotation()
        assert 0.0 <= s <= pairing_core._CODE_INTERVAL

    def test_pair_unpair_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pairing_core, "_PAIRED_DIR", str(tmp_path))
        monkeypatch.setattr(pairing_core, "_PAIRED_FILE", str(tmp_path / "paired"))
        monkeypatch.setattr(pairing_core, "_paired", False)
        assert pairing_core.is_paired() is False
        pairing_core.set_paired()
        assert pairing_core.is_paired() is True
        assert (tmp_path / "paired").is_file()
        pairing_core.set_unpaired()
        assert pairing_core.is_paired() is False


# --------------------------------------------------------------------------- #
# Discovery: three-source merge + unicast fallback
# --------------------------------------------------------------------------- #


@pytest.fixture
def fresh_discovery():
    """Clear all three discovery source dicts before and after the test."""
    def _clear():
        with discovery._peers_lock:
            discovery._peers.clear()
        with discovery._scanned_lock:
            discovery._scanned_peers.clear()
        with discovery._registered_lock:
            discovery._registered_peers.clear()
    _clear()
    yield discovery
    _clear()


class TestDiscoveryMerge:
    def test_registered_peer_visible(self, fresh_discovery):
        d = fresh_discovery
        d.register_peer("devR", "10.0.0.2", 6229, "pi-r")
        peers = d.get_peers()
        assert peers["devR"] == {"host": "10.0.0.2", "port": 6229, "hostname": "pi-r"}

    def test_mdns_wins_over_scan_and_registered(self, fresh_discovery):
        d = fresh_discovery
        d.register_peer("dev1", "10.0.0.2", 6229, "registered")
        with d._scanned_lock:
            d._scanned_peers["dev1"] = {"host": "10.0.0.3", "port": 6229, "hostname": "scan"}
        with d._peers_lock:
            d._peers["dev1"] = {"host": "10.0.0.4", "port": 6229, "hostname": "mdns"}
        assert d.get_peers()["dev1"]["host"] == "10.0.0.4"

    def test_scan_wins_over_registered(self, fresh_discovery):
        d = fresh_discovery
        d.register_peer("dev2", "10.0.0.2", 6229, "registered")
        with d._scanned_lock:
            d._scanned_peers["dev2"] = {"host": "10.0.0.3", "port": 6229, "hostname": "scan"}
        assert d.get_peers()["dev2"]["host"] == "10.0.0.3"

    def test_distinct_devices_coexist(self, fresh_discovery):
        d = fresh_discovery
        d.register_peer("a", "10.0.0.2", 6229, "a")
        with d._peers_lock:
            d._peers["b"] = {"host": "10.0.0.3", "port": 6229, "hostname": "b"}
        assert set(d.get_peers()) == {"a", "b"}

    def test_unicast_register_endpoint_stores_peer(self, client, fresh_discovery):
        """REACHER_BROKER_URL fallback path: POST /register validates the remote's
        /health, then stores it so it surfaces in get_peers()."""
        health = Mock()
        health.json.return_value = {"service": "reacher", "device_id": "remote-xyz", "hostname": "pi-x"}
        client.app.state.http_client.get = AsyncMock(return_value=health)
        r = client.post(
            "/api/discovery/register",
            json={"device_id": "remote-xyz", "url": "http://10.0.0.7:6229", "hostname": "pi-x"},
        )
        assert r.status_code == 200
        assert r.json()["device_id"] == "remote-xyz"
        assert fresh_discovery.get_peers()["remote-xyz"]["host"] == "10.0.0.7"


# --------------------------------------------------------------------------- #
# Proxy: per-machine credential routing + ws-token relay
# --------------------------------------------------------------------------- #


MACHINE_A = {"url": "http://10.0.0.5:6229", "api_key": "KEY_A", "hostname": "pi-a", "name": "A"}
MACHINE_B = {"url": "http://10.0.0.9:6229", "api_key": "KEY_B", "hostname": "pi-b", "name": "B"}


def _fake_upstream():
    resp = Mock()
    resp.status_code = 200
    resp.content = b'{"ok": true}'
    resp.headers = {"content-type": "application/json"}
    return resp


class TestProxyIsolation:
    def test_unpaired_device_404(self, client):
        with patch("reacher.api.routers.proxy.machines.get", return_value=None):
            r = client.get("/api/proxy/devX/api/sessions", headers=AUTH_HEADER)
        assert r.status_code == 404

    def test_request_carries_machine_credentials(self, client):
        calls = []

        async def fake_request(**kw):
            calls.append(kw)
            return _fake_upstream()

        with patch("reacher.api.routers.proxy.machines.get", return_value=MACHINE_A):
            client.app.state.http_client.request = fake_request
            r = client.get("/api/proxy/devA/api/sessions", headers=AUTH_HEADER)

        assert r.status_code == 200
        assert calls[0]["url"] == "http://10.0.0.5:6229/api/sessions"
        assert calls[0]["headers"]["Authorization"] == "Bearer KEY_A"

    def test_no_credential_bleed_between_machines(self, client):
        calls = []

        async def fake_request(**kw):
            calls.append(kw)
            return _fake_upstream()

        machine_map = {"devA": MACHINE_A, "devB": MACHINE_B}
        with patch("reacher.api.routers.proxy.machines.get", side_effect=machine_map.get):
            client.app.state.http_client.request = fake_request
            client.get("/api/proxy/devA/api/sessions", headers=AUTH_HEADER)
            client.get("/api/proxy/devB/api/sessions", headers=AUTH_HEADER)

        assert calls[0]["headers"]["Authorization"] == "Bearer KEY_A"
        assert calls[0]["url"].startswith("http://10.0.0.5:6229")
        assert calls[1]["headers"]["Authorization"] == "Bearer KEY_B"
        assert calls[1]["url"].startswith("http://10.0.0.9:6229")

    def test_ws_token_unpaired_404(self, client):
        with patch("reacher.api.routers.proxy.machines.get", return_value=None):
            r = client.get("/api/proxy/devX/ws-token", headers=AUTH_HEADER)
        assert r.status_code == 404

    def test_ws_token_returns_local_key_not_remote(self, client):
        with patch("reacher.api.routers.proxy.machines.get", return_value=MACHINE_A):
            r = client.get("/api/proxy/devA/ws-token", headers=AUTH_HEADER)
        assert r.status_code == 200
        body = r.json()
        # The browser authenticates against the LOCAL server, never the Pi's key.
        assert body["token"] == API_KEY
        assert body["token"] != MACHINE_A["api_key"]
        assert "devA" in body["ws_url"]
