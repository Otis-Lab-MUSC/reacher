"""Route and session-state guards for the session lifecycle (R3).

Connect/disconnect/start/stop/reset must refuse states where they would strand
unexported run data or reach a closed port, and a session the kernel recovered
on its own must get its pre-drop state back.
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi.testclient import TestClient

import reacher.api.routers.websocket as ws
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.session_manager import SessionManager

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def mock_reacher():
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        mock_instance = Mock()
        mock_instance.program_running = False
        mock_instance.ser = Mock()
        mock_instance.ser.is_open = False
        mock_instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        mock_instance.get_behavior_data.return_value = []
        mock_instance.get_program_running.return_value = False
        mock_instance.emit_failure_count = 0
        MockReacher.return_value = mock_instance
        yield MockReacher


@pytest.fixture
def client(mock_reacher):
    """TestClient with a mocked REACHER instance (mirrors tests/test_program_start_guard.py)."""
    with patch("os.makedirs"):
        app = create_app()
        with TestClient(app) as c:
            yield c


def _create_session(client, port="/dev/ttyUSB0"):
    resp = client.post("/api/sessions", json={"port": port}, headers=AUTH_HEADER)
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _instance(client, sid):
    return client.app.state.session_manager.get_session(sid).instance


class TestConnectGuard:
    @pytest.mark.parametrize("state", ["running", "paused"])
    def test_connect_while_running_or_paused_is_409(self, client, state):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, state)
        inst = _instance(client, sid)

        resp = client.post(f"/api/serial/{sid}/connect", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot connect while the program is running or paused"
        inst.open_serial.assert_not_called()
        assert client.app.state.session_manager.get_session(sid).state == state


class TestDisconnectGuard:
    @pytest.mark.parametrize("state", ["running", "paused"])
    def test_disconnect_while_running_or_paused_is_409(self, client, state):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, state)
        inst = _instance(client, sid)

        resp = client.post(f"/api/serial/{sid}/disconnect", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot disconnect while the program is running or paused. Stop it first"
        inst.close_serial.assert_not_called()
        assert client.app.state.session_manager.get_session(sid).state == state

    def test_disconnect_from_connected_sets_disconnected(self, client):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, "connected")

        resp = client.post(f"/api/serial/{sid}/disconnect", headers=AUTH_HEADER)

        assert resp.status_code == 200
        assert resp.json() == {"status": "disconnected"}
        assert client.app.state.session_manager.get_session(sid).state == "disconnected"


class TestReconnectRestoresState:
    @pytest.mark.parametrize("before", ["running", "connected"])
    def test_reconnected_event_restores_pre_disconnect_state(self, mock_reacher, before):
        cb = Mock()
        sm = SessionManager(event_callback=cb)
        sid = sm.create_session("/dev/ttyUSB0", "fr")
        wrapper = mock_reacher.call_args.kwargs["event_callback"]
        sm.set_state(sid, before)

        sm.handle_disconnect(sid, "cable pulled")
        assert sm.get_session(sid).state == "disconnected"

        wrapper(sid, "reconnected", {"attempt": 1})

        info = sm.get_session(sid)
        assert info.state == before
        assert info.state_before_disconnect is None
        cb.assert_any_call(sid, "session_state", {"state": before})

    def test_repeated_disconnect_keeps_the_original_state(self, mock_reacher):
        sm = SessionManager()
        sid = sm.create_session("/dev/ttyUSB0", "fr")
        wrapper = mock_reacher.call_args.kwargs["event_callback"]
        sm.set_state(sid, "running")

        sm.handle_disconnect(sid, "first retry")
        sm.handle_disconnect(sid, "second retry")
        wrapper(sid, "reconnected", {"attempt": 2})

        assert sm.get_session(sid).state == "running"

    def test_reconnected_is_a_no_op_once_the_user_has_reconnected(self, mock_reacher):
        sm = SessionManager()
        sid = sm.create_session("/dev/ttyUSB0", "fr")
        wrapper = mock_reacher.call_args.kwargs["event_callback"]
        sm.set_state(sid, "running")
        sm.handle_disconnect(sid, "lost")
        sm.set_state(sid, "connected")

        wrapper(sid, "reconnected", {"attempt": 1})

        assert sm.get_session(sid).state == "connected"


class TestOrphanTimeoutForLiveRun:
    @pytest.fixture(autouse=True)
    def _ws_globals(self):
        saved = (ws._app_ref, ws._loop)
        ws._session_disconnect_times.clear()
        yield
        ws._session_disconnect_times.clear()
        ws._app_ref, ws._loop = saved

    @pytest.mark.parametrize(
        "program_running, age_seconds, destroyed",
        [
            (True, 90, False),  # disconnected mid-run: 600s window, 90s is inside it
            (True, 700, True),  # ...but not past it
            (False, 90, True),  # disconnected and idle: the 60s default applies
        ],
    )
    async def test_disconnected_session_timeout_depends_on_live_run(self, program_running, age_seconds, destroyed):
        sid = "sess_disconnected"
        ws._session_disconnect_times[sid] = time.monotonic() - age_seconds

        mock_session = Mock()
        mock_session.state = "disconnected"
        mock_session.instance.program_running = program_running
        mock_sm = Mock()
        mock_sm._sessions = {sid: mock_session}
        mock_app = Mock()
        mock_app.state.session_manager = mock_sm
        ws._app_ref = mock_app
        ws._loop = asyncio.get_event_loop()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            with patch("reacher.api.routers.hardware.release_session"):
                with pytest.raises(asyncio.CancelledError):
                    await ws._orphan_cleanup()

        if destroyed:
            mock_sm.destroy_session.assert_called_once_with(sid)
        else:
            mock_sm.destroy_session.assert_not_called()


class TestStartRequiresOpenPort:
    @pytest.mark.parametrize("state", ["idle", "connected", "stopped"])
    def test_start_with_closed_port_is_409_before_the_kernel(self, client, state):
        sid = _create_session(client)
        sm = client.app.state.session_manager
        sm.set_state(sid, state)
        inst = _instance(client, sid)
        inst.ser.is_open = False

        resp = client.post(f"/api/program/{sid}/start", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Connect the board first"
        inst.start_program.assert_not_called()


class TestStopWhenNotRunning:
    @pytest.mark.parametrize("state", ["idle", "connected", "stopped"])
    def test_stop_with_nothing_running_is_not_running(self, client, state):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, state)
        inst = _instance(client, sid)

        resp = client.post(f"/api/program/{sid}/stop", headers=AUTH_HEADER)

        assert resp.status_code == 200
        assert resp.json() == {"status": "not_running"}
        inst.stop_program.assert_not_called()


class TestResetGuard:
    def test_reset_while_paused_is_409(self, client):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, "paused")
        inst = _instance(client, sid)

        resp = client.post(f"/api/sessions/{sid}/reset", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot reset while paused. Resume or stop first"
        inst.reset.assert_not_called()

    def test_reset_while_disconnected_is_409(self, client):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, "disconnected")
        inst = _instance(client, sid)

        resp = client.post(f"/api/sessions/{sid}/reset", headers=AUTH_HEADER)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot reset while disconnected. Reconnect first"
        inst.reset.assert_not_called()

    def test_reset_while_running_is_still_allowed(self, client):
        sid = _create_session(client)
        client.app.state.session_manager.set_state(sid, "running")
        inst = _instance(client, sid)

        resp = client.post(f"/api/sessions/{sid}/reset", headers=AUTH_HEADER)

        assert resp.status_code == 200
        inst.reset.assert_called_once()
