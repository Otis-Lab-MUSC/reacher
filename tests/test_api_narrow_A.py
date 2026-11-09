"""Exception narrowing in the websocket, proxy, app and logging-middleware modules.

Each handler that used to catch ``Exception`` now lists the failures its guarded
call can really raise.  For every site there are two kinds of case: a listed
failure is still handled, and an unrelated exception (a bug) now surfaces.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import websockets.exceptions as wsexc
from fastapi import WebSocketDisconnect

import reacher.api.routers.proxy as proxy
import reacher.api.routers.websocket as ws
from reacher.api import app as app_module
from reacher.api.middleware import logging as logging_mw
from reacher.api.middleware.auth import API_KEY


@pytest.fixture(autouse=True)
def _restore_ws_globals():
    """Snapshot and restore the websocket module globals each test touches."""
    saved = (
        ws._app_ref,
        ws._loop,
        ws._notify,
        ws._broadcast_task,
        ws._watchdog_task,
        ws._orphan_task,
        ws._had_connections,
        ws._dropped_events,
    )
    yield
    for task in (ws._broadcast_task, ws._watchdog_task, ws._orphan_task):
        if task is not None and task is not saved[3] and not task.done():
            task.cancel()
    while not ws._event_queue.empty():
        ws._event_queue.get_nowait()
    ws._connections.clear()
    ws._session_disconnect_times.clear()
    (
        ws._app_ref,
        ws._loop,
        ws._notify,
        ws._broadcast_task,
        ws._watchdog_task,
        ws._orphan_task,
        ws._had_connections,
        ws._dropped_events,
    ) = saved


def _app_with_sessions(sessions):
    """Return an app stand-in whose session manager owns ``sessions``."""
    return SimpleNamespace(state=SimpleNamespace(session_manager=SimpleNamespace(_sessions=sessions)))


# --- websocket._any_session_active ---------------------------------------------------------------------


class TestAnySessionActive:
    @pytest.mark.parametrize("exc", [AttributeError("no session_manager"), RuntimeError("dict changed size")])
    def test_listed_failures_mean_not_active(self, exc):
        sessions = Mock()
        sessions.values.side_effect = exc
        ws._app_ref = _app_with_sessions(sessions)
        assert ws._any_session_active() is False

    def test_missing_session_manager_means_not_active(self):
        ws._app_ref = SimpleNamespace(state=SimpleNamespace())
        assert ws._any_session_active() is False

    def test_unrelated_exception_surfaces(self):
        sessions = Mock()
        sessions.values.side_effect = TypeError("bug")
        ws._app_ref = _app_with_sessions(sessions)
        with pytest.raises(TypeError):
            ws._any_session_active()


# --- websocket._broadcast_worker -----------------------------------------------------------------------


async def _start_worker_with_failing_client(exc, event_session):
    """Run the broadcast worker with one client on ``s1`` whose send raises ``exc``."""
    client = Mock()
    client.send_text = AsyncMock(side_effect=exc)
    ws._connections["s1"].add(client)
    ws._loop = asyncio.get_running_loop()
    with ws._dropped_events_lock:
        ws._dropped_events = 5  # forces the overflow-warning branch after the first drain
    task = asyncio.create_task(ws._broadcast_worker())
    await asyncio.sleep(0)
    ws.enqueue_event(event_session, "log", {"message": "x"})
    await asyncio.sleep(0.05)
    return task, client


class TestBroadcastWorkerSendFailures:
    @pytest.mark.parametrize("event_session", ["s1", "other"], ids=["event-fanout", "overflow-warning"])
    @pytest.mark.parametrize("exc", [WebSocketDisconnect(code=1006), RuntimeError("closed"), OSError("reset")])
    async def test_gone_client_is_dropped_and_worker_survives(self, exc, event_session):
        task, client = await _start_worker_with_failing_client(exc, event_session)
        try:
            assert not task.done()
            assert client not in ws._connections["s1"]
        finally:
            task.cancel()

    @pytest.mark.parametrize("event_session", ["s1", "other"], ids=["event-fanout", "overflow-warning"])
    async def test_unrelated_exception_surfaces(self, event_session):
        task, _client = await _start_worker_with_failing_client(TypeError("bug"), event_session)
        try:
            assert task.done()
            assert isinstance(task.exception(), TypeError)
        finally:
            task.cancel()


# --- websocket._orphan_cleanup -------------------------------------------------------------------------


async def _run_one_orphan_pass(app, sid="s1", age=90):
    """Run a single orphan-cleanup pass with ``sid`` disconnected ``age`` seconds ago."""
    import time

    ws._session_disconnect_times[sid] = time.monotonic() - age
    ws._app_ref = app
    ws._loop = asyncio.get_running_loop()
    with (
        patch("asyncio.sleep", new_callable=AsyncMock) as sleeper,
        patch("reacher.api.routers.hardware.release_session"),
    ):
        sleeper.side_effect = [None, asyncio.CancelledError()]
        with pytest.raises(asyncio.CancelledError):
            await ws._orphan_cleanup()


class TestOrphanCleanup:
    async def test_missing_session_manager_is_tolerated(self):
        await _run_one_orphan_pass(SimpleNamespace(state=SimpleNamespace()))

    async def test_unrelated_lookup_exception_surfaces(self):
        sessions = Mock()
        sessions.get.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            await _run_one_orphan_pass(_app_with_sessions(sessions))

    @pytest.mark.parametrize("exc", [OSError("port busy"), RuntimeError("join"), ValueError("bad")])
    async def test_destroy_failure_is_tolerated(self, exc):
        app = _app_with_sessions({})
        app.state.session_manager.destroy_session = Mock(side_effect=exc)
        await _run_one_orphan_pass(app)
        app.state.session_manager.destroy_session.assert_called_once_with("s1")

    async def test_unrelated_destroy_exception_surfaces(self):
        app = _app_with_sessions({})
        app.state.session_manager.destroy_session = Mock(side_effect=TypeError("bug"))
        with pytest.raises(TypeError):
            await _run_one_orphan_pass(app)


# --- websocket.websocket_endpoint: snapshot on connect -------------------------------------------------


def _fake_websocket(sessions, send_exc=None):
    """Build a WebSocket stand-in that disconnects as soon as the receive loop starts."""
    sock = Mock()
    sock.query_params = {"token": API_KEY}
    sock.accept = AsyncMock()
    sock.send_text = AsyncMock(side_effect=send_exc)
    sock.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
    sock.app = _app_with_sessions(sessions)
    return sock


async def _connect(sock):
    """Drive the endpoint for session ``s1`` without starting its background tasks."""
    with patch.object(ws, "_ensure_broadcast_worker"), patch.object(ws, "_orphan_cleanup", new=AsyncMock()):
        await ws.websocket_endpoint(sock, "s1")


class TestSnapshotOnConnect:
    @pytest.mark.parametrize("exc", [WebSocketDisconnect(code=1006), RuntimeError("closed"), OSError("reset")])
    async def test_client_gone_during_snapshot_is_tolerated(self, exc):
        await _connect(_fake_websocket({"s1": SimpleNamespace(state="idle")}, send_exc=exc))
        assert "s1" not in ws._connections

    async def test_missing_session_manager_is_tolerated(self):
        sock = _fake_websocket({})
        sock.app = SimpleNamespace(state=SimpleNamespace())
        await _connect(sock)

    async def test_unrelated_exception_surfaces_and_connection_is_released(self):
        sessions = Mock()
        sessions.get.side_effect = TypeError("bug")
        with pytest.raises(TypeError):
            await _connect(_fake_websocket(sessions))
        assert "s1" not in ws._connections


# --- proxy.ws_relay ------------------------------------------------------------------------------------

MACHINE = {"url": "http://pi.local:6229", "api_key": "pi-key"}


async def _relay(connect, ws_stub):
    """Run the relay endpoint with upstream ``connect`` patched in."""
    with (
        patch.object(proxy, "verify_ws_token", return_value=True),
        patch.object(proxy.machines, "get", return_value=MACHINE),
        patch.object(proxy.websockets, "connect", connect),
    ):
        await proxy.ws_relay(ws_stub, "dev12345", "s1")


def _browser(close_exc=None):
    """Build a browser-side WebSocket stand-in that disconnects immediately."""
    sock = Mock()
    sock.accept = AsyncMock()
    sock.close = AsyncMock(side_effect=close_exc)
    sock.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
    sock.send_text = AsyncMock()
    sock.send_bytes = AsyncMock()
    return sock


class _Upstream:
    """An upstream connection that yields no frames and fails on close as told."""

    def __init__(self, close_exc=None):
        self.send = AsyncMock()
        self.close = AsyncMock(side_effect=close_exc)

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        if False:
            yield ""


class TestRelayUpstreamConnect:
    @pytest.mark.parametrize(
        "exc",
        [
            OSError("refused"),
            asyncio.TimeoutError(),
            ValueError("Port could not be cast"),
            wsexc.InvalidURI("ws://h/x", "bad"),
            wsexc.InvalidHandshake("nope"),
        ],
        ids=["oserror", "timeout", "valueerror", "invalid-uri", "invalid-handshake"],
    )
    async def test_connect_failure_closes_browser_with_1011(self, exc):
        browser = _browser()
        await _relay(AsyncMock(side_effect=exc), browser)
        browser.close.assert_awaited_once()
        assert browser.close.await_args.kwargs["code"] == 1011

    async def test_unrelated_exception_surfaces(self):
        with pytest.raises(TypeError):
            await _relay(AsyncMock(side_effect=TypeError("bug")), _browser())


class TestRelayTeardown:
    @pytest.mark.parametrize("exc", [OSError("reset"), RuntimeError("closed"), wsexc.ConnectionClosedError(None, None)])
    async def test_upstream_close_failure_is_tolerated(self, exc):
        browser = _browser()
        await _relay(AsyncMock(return_value=_Upstream(close_exc=exc)), browser)
        browser.close.assert_awaited_once()

    @pytest.mark.parametrize("exc", [WebSocketDisconnect(code=1006), RuntimeError("closed"), OSError("reset")])
    async def test_browser_close_failure_is_tolerated(self, exc):
        await _relay(AsyncMock(return_value=_Upstream()), _browser(close_exc=exc))

    async def test_unrelated_upstream_close_exception_surfaces(self):
        with pytest.raises(TypeError):
            await _relay(AsyncMock(return_value=_Upstream(close_exc=TypeError("bug"))), _browser())

    async def test_unrelated_browser_close_exception_surfaces(self):
        with pytest.raises(TypeError):
            await _relay(AsyncMock(return_value=_Upstream()), _browser(close_exc=TypeError("bug")))


# --- app._post_broker_registration ---------------------------------------------------------------------


async def _register(post_exc):
    """Register with a broker whose POST raises ``post_exc``."""
    client = Mock()
    client.post = AsyncMock(side_effect=post_exc)
    with patch.object(app_module.discovery, "_get_local_ip", return_value="10.0.0.2"):
        await app_module._post_broker_registration("http://broker", client, "dev", 6229, "1.0")


class TestBrokerRegistration:
    @pytest.mark.parametrize(
        "exc",
        [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), httpx.InvalidURL("bad"), ValueError("not json")],
        ids=["connect", "timeout", "invalid-url", "valueerror"],
    )
    async def test_unreachable_broker_is_logged_not_raised(self, exc):
        await _register(exc)

    async def test_unrelated_exception_surfaces(self):
        with pytest.raises(TypeError):
            await _register(TypeError("bug"))


# --- middleware.logging._decode_body -------------------------------------------------------------------


class TestDecodeBody:
    def test_json_body_is_redacted_structure(self):
        assert logging_mw._decode_body(json.dumps({"a": 1}).encode()) == {"a": 1}

    def test_non_json_body_falls_back_to_text(self):
        assert logging_mw._decode_body(b"not json") == "not json"

    def test_deeply_nested_json_falls_back_to_text(self):
        assert isinstance(logging_mw._decode_body(b"[" * 4000), str)

    def test_unrelated_parser_exception_surfaces(self):
        with patch("json.loads", side_effect=TypeError("bug")), pytest.raises(TypeError):
            logging_mw._decode_body(b"{}")


# --- middleware.logging.RequestLoggingMiddleware: observe a failure, never swallow it --------------------


async def _call_middleware(inner):
    """Run ``inner`` behind the middleware for ``GET /x``; return the logged ``_record`` calls."""
    mw = logging_mw.RequestLoggingMiddleware(inner)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        return None

    with patch.object(mw, "_record") as record:
        try:
            await mw({"type": "http", "path": "/x", "method": "GET"}, receive, send)
        finally:
            calls = record.call_args_list
    return calls


class TestMiddlewareFailureObservation:
    @pytest.mark.parametrize("exc", [RuntimeError("boom"), TypeError("bug"), KeyError("k")])
    async def test_any_exception_is_recorded_as_500_and_reraised(self, exc):
        async def inner(scope, receive, send):
            raise exc

        mw = logging_mw.RequestLoggingMiddleware(inner)
        with patch.object(mw, "_record") as record, pytest.raises(type(exc)):
            await mw({"type": "http", "path": "/x", "method": "GET"}, AsyncMock(), AsyncMock())
        assert record.call_args.args[3] == 500
        assert type(exc).__name__ in record.call_args.kwargs["error"]

    async def test_cancellation_is_not_recorded(self):
        async def inner(scope, receive, send):
            raise asyncio.CancelledError

        mw = logging_mw.RequestLoggingMiddleware(inner)
        with patch.object(mw, "_record") as record, pytest.raises(asyncio.CancelledError):
            await mw({"type": "http", "path": "/x", "method": "GET"}, AsyncMock(), AsyncMock())
        record.assert_not_called()

    async def test_success_is_recorded_once_with_the_response_status(self):
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 204})

        calls = await _call_middleware(inner)
        assert len(calls) == 1
        assert calls[0].args[3] == 204
