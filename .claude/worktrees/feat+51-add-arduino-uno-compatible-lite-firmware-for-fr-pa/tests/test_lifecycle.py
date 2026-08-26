"""Tests for the lifecycle shutdown beacon (issue #30).

The ``POST /api/lifecycle/shutdown`` beacon is intentionally auth-free
(``navigator.sendBeacon`` cannot set an Authorization header). These tests lock in
the mid-acquisition guard that prevents an unauthenticated beacon from terminating
the process while a session is recording, while preserving the legitimate idle
tab-close path.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi.testclient import TestClient
from reacher.api.app import create_app

import reacher.api.routers.lifecycle as lifecycle
import reacher.api.routers.websocket as ws


@pytest.fixture(autouse=True)
def reset_ws_globals():
    """Save and restore websocket module globals between tests.

    Mirrors the fixture in test_websocket.py so this file stays isolation-safe as it
    grows, even though the current tests only read a subset of these globals.
    """
    saved = (
        ws._had_connections,
        ws._last_connection_time,
        ws._dropped_events,
        ws._app_ref,
        ws._shutdown_scheduled,
        ws._server_suspended,
        ws._session_orphaned,
    )
    yield
    ws._connections.clear()
    ws._had_connections = saved[0]
    ws._last_connection_time = saved[1]
    with ws._dropped_events_lock:
        ws._dropped_events = saved[2]
    ws._app_ref = saved[3]
    ws._shutdown_scheduled = saved[4]
    ws._server_suspended = saved[5]
    ws._session_orphaned = saved[6]


def _app_ref_with_session(state):
    """Build a mock app_ref whose session manager holds one session in ``state``."""
    mock_session = Mock()
    mock_session.state = state
    mock_sm = Mock()
    mock_sm._sessions = {"sess1": mock_session}
    mock_app = Mock()
    mock_app.state.session_manager = mock_sm
    return mock_app


class TestBeaconShutdownGuard:
    """Issue #30: beacon must not kill the process mid-acquisition."""

    @pytest.mark.parametrize("state", ["running", "paused", "uploading"])
    async def test_refuses_shutdown_while_session_active(self, state):
        # Beacon's preconditions met (a client connected then dropped), 0 connections,
        # but a session is recording/paused — the orphaned-but-active attack path.
        ws._had_connections = True
        ws._server_suspended = False
        ws._connections.clear()  # total_connections() == 0
        ws._app_ref = _app_ref_with_session(state)

        with patch.object(lifecycle, "_trigger_shutdown") as mock_shutdown, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await lifecycle._delayed_shutdown()

        mock_shutdown.assert_not_called()

    async def test_shuts_down_when_idle(self):
        # Legitimate idle tab-close: connections existed, none active now, no session.
        ws._had_connections = True
        ws._server_suspended = False
        ws._connections.clear()
        ws._app_ref = _app_ref_with_session("stopped")

        with patch.object(lifecycle, "_trigger_shutdown") as mock_shutdown, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await lifecycle._delayed_shutdown()

        mock_shutdown.assert_called_once()

    async def test_no_shutdown_when_never_connected(self):
        # Pre-serial state: no WS client ever connected → beacon is ignored.
        ws._had_connections = False
        ws._server_suspended = False
        ws._connections.clear()
        ws._app_ref = _app_ref_with_session("idle")

        with patch.object(lifecycle, "_trigger_shutdown") as mock_shutdown, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await lifecycle._delayed_shutdown()

        mock_shutdown.assert_not_called()

    async def test_no_shutdown_when_connections_reestablished(self):
        # A client reconnected during the grace period → cancel shutdown.
        ws._had_connections = True
        ws._server_suspended = False
        ws._connections.clear()
        ws._connections["sess1"].add(Mock())  # total_connections() == 1
        ws._app_ref = _app_ref_with_session("stopped")

        with patch.object(lifecycle, "_trigger_shutdown") as mock_shutdown, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await lifecycle._delayed_shutdown()

        mock_shutdown.assert_not_called()


class TestBeaconEndpoint:
    """The endpoint stays auth-free and accepts the headerless beacon."""

    def test_shutdown_endpoint_no_auth_required(self):
        with patch("reacher.session_manager.REACHER"), patch("os.makedirs"):
            app = create_app()
            # Stub the delayed task so the test doesn't wait the real grace period;
            # AsyncMock() returns an awaitable, satisfying create_task().
            with patch.object(lifecycle, "_delayed_shutdown", new_callable=AsyncMock) as mock_delayed:
                with TestClient(app) as client:
                    # No Authorization header, no body — the sendBeacon contract.
                    resp = client.post("/api/lifecycle/shutdown")

        # Auth-free: accepted (202), not rejected (401).
        assert resp.status_code == 202
        assert resp.json() == {"status": "shutdown_scheduled"}
        mock_delayed.assert_called_once()
