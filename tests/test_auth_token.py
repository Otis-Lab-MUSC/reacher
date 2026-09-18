"""GET /api/auth/token — Fix: F1 (2026-09-18 pressure test).

Covers the two closed legs (X-Reacher-App header, loopback-only) plus the
legs that must keep working (same-origin frontend, the /api/sessions 401
control, and that a hostile cross-origin preflight can never learn to send
the header).
"""

from contextlib import contextmanager
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

APP_HEADER = {"X-Reacher-App": "1"}


@contextmanager
def _client(peer=("testclient", 50000)):
    """A TestClient whose simulated peer address is *peer* (host, port)."""
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        MockReacher.return_value = Mock()
        app = create_app()
        with TestClient(app, client=peer) as c:
            yield c


class TestAuthTokenLoopbackAndHeaderGate:
    def test_no_origin_no_header_403_no_key(self):
        # Leg (a)
        with _client(peer=("127.0.0.1", 54321)) as c:
            resp = c.get("/api/auth/token")
            assert resp.status_code == 403
            assert "token" not in resp.text

    def test_loopback_with_header_returns_token(self):
        # Leg (a') — documented residual: same-host callers with the header pass.
        with _client(peer=("127.0.0.1", 54321)) as c:
            resp = c.get("/api/auth/token", headers=APP_HEADER)
            assert resp.status_code == 200
            assert resp.json()["token"] == API_KEY

    def test_ipv6_loopback_with_header_returns_token(self):
        with _client(peer=("::1", 54321)) as c:
            resp = c.get("/api/auth/token", headers=APP_HEADER)
            assert resp.status_code == 200
            assert resp.json()["token"] == API_KEY

    def test_non_loopback_peer_with_header_still_403(self):
        # Leg (a'') — Layer B: header alone is not enough from a LAN peer.
        with _client(peer=("10.37.1.223", 54321)) as c:
            resp = c.get("/api/auth/token", headers=APP_HEADER)
            assert resp.status_code == 403
            assert "token" not in resp.text

    def test_non_loopback_peer_allowed_when_remote_ok_env_set(self, monkeypatch):
        monkeypatch.setenv("REACHER_TOKEN_REMOTE_OK", "1")
        with _client(peer=("10.37.1.223", 54321)) as c:
            resp = c.get("/api/auth/token", headers=APP_HEADER)
            assert resp.status_code == 200
            assert resp.json()["token"] == API_KEY

    def test_same_origin_frontend_shape_still_works(self):
        # Leg (c): Origin present-and-allowed + X-Reacher-App, from loopback.
        with _client(peer=("127.0.0.1", 54321)) as c:
            resp = c.get(
                "/api/auth/token",
                headers={**APP_HEADER, "Origin": "http://127.0.0.1:6229"},
            )
            assert resp.status_code == 200
            token = resp.json()["token"]
            sessions = c.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
            assert sessions.status_code == 200

    def test_disallowed_origin_still_403_even_with_header(self):
        with _client(peer=("127.0.0.1", 54321)) as c:
            resp = c.get(
                "/api/auth/token",
                headers={**APP_HEADER, "Origin": "http://evil.example.com"},
            )
            assert resp.status_code == 403

    def test_sessions_no_auth_still_401_control(self):
        # Leg (d)
        with _client(peer=("127.0.0.1", 54321)) as c:
            resp = c.get("/api/sessions")
            assert resp.status_code == 401


class TestAuthTokenCorsPreflight:
    def test_hostile_cross_origin_preflight_does_not_allow_custom_header(self):
        # Leg (b): X-Reacher-App must never be handed to a preflight, or
        # Layer A is a no-op for exactly the attacker this closes.
        with _client(peer=("127.0.0.1", 54321)) as c:
            resp = c.options(
                "/api/auth/token",
                headers={
                    "Origin": "http://evil.example.com",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-reacher-app",
                },
            )
            allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
            assert "x-reacher-app" not in allow_headers
