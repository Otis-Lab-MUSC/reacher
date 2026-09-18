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

import json
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


class TestDiscoveryStart:
    """labrynth#117: mDNS registration can legitimately fail (stale name from a
    previous run that didn't shut down cleanly, no multicast route, restricted
    network). It must log as a warning, not an unhandled ERROR traceback, and
    must not prevent the ServiceBrowser from starting — browsing for *other*
    peers is unaffected by this device's own registration failing."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self, monkeypatch):
        monkeypatch.setattr(discovery, "_zeroconf", None)
        monkeypatch.setattr(discovery, "_browser", None)
        monkeypatch.setattr(discovery, "_info", None)

    def test_registration_conflict_logs_warning_and_still_browses(self, monkeypatch, caplog):
        import zeroconf as zc_module

        mock_instance = Mock()
        mock_instance.register_service.side_effect = zc_module.NonUniqueNameException("name in use")
        monkeypatch.setattr(zc_module, "Zeroconf", Mock(return_value=mock_instance))
        mock_browser_cls = Mock()
        monkeypatch.setattr(zc_module, "ServiceBrowser", mock_browser_cls)

        with caplog.at_level("DEBUG", logger="reacher.discovery"):
            discovery.start("test-device-id", 6229, "0.0.0-test")

        assert not [r for r in caplog.records if r.levelname == "ERROR"], "expected registration failure must not log at ERROR"
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("registration failed" in m for m in warnings)

        mock_browser_cls.assert_called_once()  # browsing for peers must still start
        assert discovery._info is None  # nothing to unregister on shutdown

    def test_unexpected_failure_still_logs_exception(self, monkeypatch, caplog):
        import zeroconf as zc_module

        monkeypatch.setattr(zc_module, "Zeroconf", Mock(side_effect=RuntimeError("boom")))

        with caplog.at_level("DEBUG", logger="reacher.discovery"):
            discovery.start("test-device-id", 6229, "0.0.0-test")

        assert [r for r in caplog.records if r.levelname == "ERROR"], "genuinely unexpected failures must still be logged loudly"


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


# --------------------------------------------------------------------------- #
# Proxy: WS relay upstream-connect-failure logging (Fix: F5)
#
# proxy.py's ws_relay logs the upstream connect failure with exc_info=True.
# A malformed remote URL makes websockets raise InvalidURI, whose str() and
# traceback both embed the *full* connect URI — query string, and therefore
# the machine's api_key, included. Layer A (proxy.py) must never put the
# credentialed URL in the message; Layer B (redact.py/bridge.py) must scrub
# any credential that still reaches str(exc)/the traceback.
# --------------------------------------------------------------------------- #


SENTINEL_KEY = "SENTINELF5KEY0xCAFEBABE"
MACHINE_MALFORMED = {
    "url": "ftp://badhost.invalid:1234",  # bad scheme -> websockets.InvalidURI
    "api_key": SENTINEL_KEY,
    "hostname": "badhost.invalid",
    "name": "malformed",
}


def _drain_ndjson():
    from reacher import diagnostics

    sink = diagnostics.get_sink()
    sink.flush_now()

    with open(sink.path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture
def logging_client():
    """Like `client`, but without patching os.makedirs — these tests read
    the real diagnostic sink (isolated to tmp_path by the autouse fixture in
    conftest.py), and a blanket os.makedirs patch silently no-ops the sink's
    own run-directory creation along with everything else."""
    with patch("reacher.session_manager.REACHER") as MockReacher:
        MockReacher.return_value = Mock()
        app = create_app()
        with TestClient(app) as c:
            yield c


class TestProxyRelayLoggingDoesNotLeakCredentials:
    def test_connect_failure_message_never_carries_the_raw_credential(self, logging_client):
        """Layer A: a generic connect failure (e.g. connection refused) must
        not put machine['api_key'] in the log message via url=%s."""
        with patch("reacher.api.routers.proxy.machines.get", return_value=MACHINE_A), \
             patch("reacher.api.routers.proxy.websockets.connect", side_effect=OSError("refused")):
            try:
                with logging_client.websocket_connect(f"/api/proxy/devA/ws/s1?token={API_KEY}"):
                    pass
            except Exception:
                pass  # server closes before accept on connect failure — expected

        records = _drain_ndjson()
        hits = [r for r in records if r["src"] == "reacher.api.routers.proxy" and r["lvl"] == "error"]
        assert hits, "expected a WS relay connect-failure record"
        assert MACHINE_A["api_key"] not in hits[-1]["msg"]

    def test_invalid_uri_backstop_scrubs_both_message_and_exception_text(self, logging_client):
        """Layer B: websockets.InvalidURI embeds the full credentialed URI in
        both str(exc) and the traceback — Layer A alone cannot catch this,
        because proxy.py never puts the raw exception text together itself."""
        import websockets.exceptions as wsexc

        credentialed_uri = f"ftp://badhost.invalid:1234/ws/s1?token={SENTINEL_KEY}"
        exc = wsexc.InvalidURI(credentialed_uri, "scheme isn't ws or wss")

        with patch("reacher.api.routers.proxy.machines.get", return_value=MACHINE_MALFORMED), \
             patch("reacher.api.routers.proxy.websockets.connect", side_effect=exc):
            try:
                with logging_client.websocket_connect(f"/api/proxy/devM/ws/s1?token={API_KEY}"):
                    pass
            except Exception:
                pass

        records = _drain_ndjson()
        hits = [r for r in records if r["src"] == "reacher.api.routers.proxy" and r["lvl"] == "error"]
        assert hits, "expected a WS relay connect-failure record"
        rec = hits[-1]

        # No raw sentinel anywhere in the record.
        raw = json.dumps(rec)
        assert SENTINEL_KEY not in raw

        # Still useful for debugging: exception class, reason, and the
        # sanitized target are all present.
        assert "InvalidURI" in rec["data"]["exc"]
        assert "scheme isn't ws or wss" in rec["msg"]
        assert "badhost.invalid" in rec["msg"]

        # The scrub must not double-apply and corrupt the text (regression:
        # bridge.py used to scrub data["exc"] directly *and* via redact(),
        # producing "token=[redacted]]" — a stray extra bracket).
        assert "[redacted]]" not in rec["data"]["exc"]
        assert "[redacted]]" not in rec["msg"]
