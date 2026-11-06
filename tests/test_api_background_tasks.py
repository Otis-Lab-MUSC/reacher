"""Background tasks end on an unexpected error and the next connect restarts them.

The broadcast worker, the watchdog and the orphan reaper catch only the failures they can expect.
A bug inside one ends that task; ``_ensure_broadcast_worker`` and the WebSocket endpoint start it
again on the next client connect.  The connect replay in ``serial.py`` must not fail because a
client notification could not be queued.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

import reacher.api.routers.websocket as ws
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH = {"Authorization": f"Bearer {API_KEY}"}
EVENT = {"type": "log", "session_id": "s1", "data": {"message": "x"}}


@pytest.fixture(autouse=True)
def _restore_ws_globals():
    """Snapshot the websocket module globals these tests change, and restore them afterwards."""
    names = ("_loop", "_notify", "_broadcast_task", "_watchdog_task", "_orphan_task", "_app_ref")
    saved = {name: getattr(ws, name) for name in names}
    yield
    while not ws._event_queue.empty():
        ws._event_queue.get_nowait()
    ws._connections.clear()
    ws._session_disconnect_times.clear()
    for name, value in saved.items():
        setattr(ws, name, value)


async def _cancel(*tasks):
    """Cancel *tasks* and wait for them to finish."""
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    await asyncio.gather(*(t for t in tasks if t is not None), return_exceptions=True)


def test_broadcast_worker_ends_on_a_bug_and_restarts_on_the_next_connect():
    """A TypeError inside the fan-out ends the worker; _ensure_broadcast_worker starts a new one."""

    async def scenario():
        ws._broadcast_task = ws._watchdog_task = None
        ws._ensure_broadcast_worker()
        first = ws._broadcast_task
        await asyncio.sleep(0)
        with patch.object(ws, "_send_to_session", AsyncMock(side_effect=TypeError("bug"))):
            ws._event_queue.put_nowait(dict(EVENT))
            ws._notify.set()
            with pytest.raises(TypeError):
                await asyncio.wait_for(first, timeout=2)
        assert first.done()

        ws._ensure_broadcast_worker()
        second = ws._broadcast_task
        assert second is not first and not second.done()

        await asyncio.sleep(0)
        delivered = AsyncMock()
        with patch.object(ws, "_send_to_session", delivered):
            ws._event_queue.put_nowait(dict(EVENT))
            ws._notify.set()
            for _ in range(50):
                if delivered.await_count:
                    break
                await asyncio.sleep(0.01)
        await _cancel(second, ws._watchdog_task)
        return delivered

    delivered = asyncio.run(scenario())
    assert delivered.await_count == 1


def test_orphan_reaper_ends_on_a_bug_but_survives_an_expected_failure():
    """The reaper outlives a listed failure and stops on a TypeError, leaving the session unreaped."""

    async def scenario(error):
        ws._loop = asyncio.get_running_loop()
        ws._session_disconnect_times["gone"] = -1e9
        real_sleep = asyncio.sleep
        calls = {"n": 0}

        async def one_pass(_delay):
            calls["n"] += 1
            if calls["n"] > 2:
                raise asyncio.CancelledError
            await real_sleep(0)

        reaper_sessions = SimpleNamespace(
            state=SimpleNamespace(session_manager=Mock(destroy_session=Mock(side_effect=error)))
        )
        ws._app_ref = reaper_sessions
        with patch.object(ws.asyncio, "sleep", one_pass):
            task = asyncio.get_running_loop().create_task(ws._orphan_cleanup())
            outcome = await asyncio.gather(task, return_exceptions=True)
        return outcome[0], calls["n"]

    outcome, passes = asyncio.run(scenario(AttributeError("no manager")))
    assert isinstance(outcome, asyncio.CancelledError) and passes == 3, "reaper stopped on an expected failure"

    ws._session_disconnect_times.clear()
    outcome, passes = asyncio.run(scenario(TypeError("bug")))
    assert isinstance(outcome, TypeError) and passes == 1


def test_websocket_connect_restarts_dead_background_tasks():
    """A client connecting after the tasks ended gets live broadcast, watchdog and orphan tasks."""
    dead = SimpleNamespace(done=lambda: True)
    with TestClient(create_app()) as client:
        ws._broadcast_task = ws._watchdog_task = ws._orphan_task = dead
        with client.websocket_connect(f"/ws/ghost?token={API_KEY}"):
            tasks = (ws._broadcast_task, ws._watchdog_task, ws._orphan_task)
            assert all(t is not dead and not t.done() for t in tasks)


def test_pump_target_replay_survives_a_closed_event_loop():
    """RuntimeError from enqueue_event while queuing the pump-target replay line keeps the connect a 200."""
    with patch("reacher.session_manager.REACHER") as mock_class, patch("os.makedirs"):
        instance = Mock()
        instance.ser = Mock(is_open=False)
        instance.program_running = False
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_firmware_caps.return_value = set()
        instance._firmware_ready.wait.return_value = True
        instance.get_detected_paradigm.return_value = "fr"
        mock_class.return_value = instance
        with TestClient(create_app()) as client:
            made = client.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"}, headers=AUTH)
            sid = made.json()["session_id"]
            client.app.state.session_manager.set_state(sid, "idle")
            with (
                patch("reacher.api.routers.serial.pin_overrides.get", return_value={}),
                patch("reacher.api.routers.serial.pump_target.get", return_value=True),
                patch("reacher.api.routers.serial._ws.enqueue_event", side_effect=RuntimeError("Event loop is closed")),
            ):
                resp = client.post(f"/api/serial/{sid}/connect", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["replayed_pump_target"] is True
