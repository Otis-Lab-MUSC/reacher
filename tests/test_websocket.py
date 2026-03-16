"""Tests for WebSocket event broadcasting and watchdog behavior."""

import asyncio
import json
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch

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
    )
    yield
    ws._connections.clear()
    ws._had_connections = saved[0]
    ws._last_connection_time = saved[1]
    with ws._dropped_events_lock:
        ws._dropped_events = saved[2]
    ws._app_ref = saved[3]
    ws._shutdown_scheduled = saved[4]


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
        ws._had_connections = True
        ws._last_connection_time = time.monotonic() - 150
        ws._connections.clear()

        # Mock app_ref with no sessions
        mock_sm = Mock()
        mock_sm._sessions = {}
        mock_app = Mock()
        mock_app.state.session_manager = mock_sm
        ws._app_ref = mock_app

        with patch.object(ws, "_trigger_shutdown") as mock_shutdown, patch("asyncio.sleep", new_callable=AsyncMock):
            await ws._watchdog()
            mock_shutdown.assert_called_once()


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
