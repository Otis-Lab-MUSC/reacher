"""Tests for WebSocket event broadcasting and watchdog behavior."""

import asyncio
import json
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi.testclient import TestClient
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

import reacher.api.routers.websocket as ws


@pytest.fixture(autouse=True)
def reset_ws_globals():
    """Save and restore websocket module globals between tests."""
    saved = (
        ws._had_connections,
        ws._last_connection_time,
        ws._dropped_events,
        ws._app_ref,
        ws._shutdown_scheduled,
        ws._server_suspended,
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


class TestWatchdogF001:
    """F-001: Watchdog defers shutdown while sessions are active."""

    async def test_watchdog_defers_when_session_active(self):
        # Set up: had connections, idle past WATCHDOG_TIMEOUT, but session active
        ws._had_connections = True
        ws._last_connection_time = time.monotonic() - 150
        ws._connections.clear()  # 0 connections

        # Mock app_ref with an active session
        mock_session = Mock()
        mock_session.state = "running"
        mock_sm = Mock()
        mock_sm._sessions = {"sess1": mock_session}
        mock_app = Mock()
        mock_app.state.session_manager = mock_sm
        ws._app_ref = mock_app

        call_count = [0]

        async def mock_sleep(duration):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()

        with patch.object(ws, "_trigger_shutdown") as mock_shutdown, patch("asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await ws._watchdog()

            mock_shutdown.assert_not_called()

    async def test_watchdog_shuts_down_when_no_active_sessions(self):
        # Idle well past WATCHDOG_TIMEOUT + WATCHDOG_HARD_KILL_TIMEOUT (120 + 1800 = 1920s)
        # so the watchdog reaches the hard-kill branch in a single pass.
        ws._had_connections = True
        ws._last_connection_time = time.monotonic() - (ws._WATCHDOG_TIMEOUT + ws._WATCHDOG_HARD_KILL_TIMEOUT + 10)
        ws._connections.clear()
        ws._server_suspended = True  # pre-set suspended so hard-kill branch fires immediately

        # Mock app_ref with no sessions
        mock_sm = Mock()
        mock_sm._sessions = {}
        mock_app = Mock()
        mock_app.state.session_manager = mock_sm
        ws._app_ref = mock_app

        with patch.object(ws, "_trigger_shutdown") as mock_shutdown, patch("asyncio.sleep", new_callable=AsyncMock):
            await ws._watchdog()
            mock_shutdown.assert_called_once()

    async def test_watchdog_suspends_before_hard_kill(self):
        """First threshold (WATCHDOG_TIMEOUT) triggers soft-suspend, not hard kill."""
        ws._had_connections = True
        ws._last_connection_time = time.monotonic() - (ws._WATCHDOG_TIMEOUT + 10)
        ws._connections.clear()
        ws._server_suspended = False

        mock_sm = Mock()
        mock_sm._sessions = {}
        mock_app = Mock()
        mock_app.state.session_manager = mock_sm
        ws._app_ref = mock_app

        call_count = [0]

        async def mock_sleep(duration):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()

        with patch.object(ws, "_trigger_shutdown") as mock_shutdown, \
             patch.object(ws, "_trigger_suspend") as mock_suspend, \
             patch("asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await ws._watchdog()

        mock_suspend.assert_called_once()
        mock_shutdown.assert_not_called()


class TestBroadcastWorkerF004:
    """F-004: Broadcast worker warns clients when events are dropped."""

    async def test_broadcast_worker_warns_on_drops(self):
        # Set up a mock WebSocket client
        mock_ws_client = AsyncMock()
        ws._connections["test-session"].add(mock_ws_client)

        # Enqueue a normal event
        ws.enqueue_event("test-session", "event", {"foo": "bar"})

        # Simulate dropped events
        with ws._dropped_events_lock:
            ws._dropped_events = 5

        # Run broadcast_worker — it will process the queue then check drops
        # We need _notify to be set and then cancel after one iteration
        iteration = [0]

        async def mock_wait(self_event):
            iteration[0] += 1
            if iteration[0] > 1:
                raise asyncio.CancelledError()

        with patch.object(asyncio.Event, "wait", mock_wait), patch.object(asyncio.Event, "clear", lambda self: None):
            with pytest.raises(asyncio.CancelledError):
                await ws._broadcast_worker()

        # Verify the warning message was sent
        sent_payloads = [json.loads(c.args[0]) for c in mock_ws_client.send_text.call_args_list]
        warning_msgs = [p for p in sent_payloads if p.get("type") == "warning"]
        assert len(warning_msgs) == 1
        assert warning_msgs[0]["data"]["dropped_count"] == 5


AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


class TestBehaviorSinceEndpoint:
    """Verify GET /behavior?since=N returns correct slices for event recovery."""

    @pytest.fixture
    def api_client(self):
        with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
            mock_instance = Mock()
            mock_instance.program_running = False
            mock_instance.ser = Mock()
            mock_instance.ser.is_open = False
            mock_instance.get_firmware_information.return_value = {}
            mock_instance.get_behavior_data.return_value = []
            mock_instance.get_frame_data.return_value = []
            mock_instance.get_frame_timestamps_count.return_value = 0
            mock_instance.get_hardware_settings.return_value = []
            mock_instance.get_program_running.return_value = False
            mock_instance.get_filename.return_value = None
            mock_instance.get_data_destination.return_value = None
            mock_instance.get_detected_paradigm.return_value = None
            mock_instance.make_destination_folder.return_value = "/tmp/reacher_test"
            MockReacher.return_value = mock_instance
            app = create_app()
            with TestClient(app) as c:
                yield c

    def _create_session(self, api_client):
        resp = api_client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        return resp.json()["session_id"]

    def test_since_returns_slice(self, api_client):
        """since=N returns behavior_data[N:] with correct total."""
        sid = self._create_session(api_client)
        instance = api_client.app.state.session_manager.get_instance(sid)
        events = [
            {"device": "RH_LEVER", "event": "ACTIVE_PRESS", "start_timestamp": i, "end_timestamp": i}
            for i in range(5)
        ]
        instance.get_behavior_data.return_value = list(events)

        resp = api_client.get(f"/api/data/{sid}/behavior?since=3", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["total"] == 5

    def test_since_beyond_length_returns_empty(self, api_client):
        """since beyond list length returns empty data with correct total."""
        sid = self._create_session(api_client)
        instance = api_client.app.state.session_manager.get_instance(sid)
        events = [
            {"device": "PUMP", "event": "INFUSION", "start_timestamp": 0, "end_timestamp": 0}
        ]
        instance.get_behavior_data.return_value = list(events)

        resp = api_client.get(f"/api/data/{sid}/behavior?since=99", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 0
        assert body["total"] == 1


class TestSessionStateSnapshotOnConnect:
    """Fix #15: a client connecting to /ws/{sid} immediately receives the current
    session_state, so a late-connecting proxy relay learns that the session is
    already running instead of staying stuck at the browser's default 'idle'.
    """

    @pytest.fixture
    def api_client(self):
        with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
            mock_instance = Mock()
            mock_instance.program_running = False
            mock_instance.ser = Mock()
            mock_instance.ser.is_open = False
            mock_instance.get_firmware_information.return_value = {}
            mock_instance.get_behavior_data.return_value = []
            mock_instance.get_frame_data.return_value = []
            mock_instance.get_frame_timestamps_count.return_value = 0
            mock_instance.get_hardware_settings.return_value = []
            mock_instance.get_program_running.return_value = False
            mock_instance.get_filename.return_value = None
            mock_instance.get_data_destination.return_value = None
            mock_instance.get_detected_paradigm.return_value = None
            mock_instance.make_destination_folder.return_value = "/tmp/reacher_test"
            MockReacher.return_value = mock_instance
            app = create_app()
            with TestClient(app) as c:
                yield c

    def _create_session(self, api_client):
        resp = api_client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH_HEADER)
        return resp.json()["session_id"]

    def test_running_state_sent_on_connect(self, api_client):
        """A session promoted to 'running' before the WS connects still reports
        'running' as the first message — the core of the proxy late-connect fix."""
        sid = self._create_session(api_client)
        api_client.app.state.session_manager.set_state(sid, "running")

        with api_client.websocket_connect(f"/ws/{sid}?token={API_KEY}") as wsconn:
            msg = json.loads(wsconn.receive_text())

        assert msg["type"] == "session_state"
        assert msg["session_id"] == sid
        assert msg["data"]["state"] == "running"

    def test_idle_default_state_sent_on_connect(self, api_client):
        """A freshly created session reports its 'idle' default on connect."""
        sid = self._create_session(api_client)

        with api_client.websocket_connect(f"/ws/{sid}?token={API_KEY}") as wsconn:
            msg = json.loads(wsconn.receive_text())

        assert msg["type"] == "session_state"
        assert msg["data"]["state"] == "idle"
