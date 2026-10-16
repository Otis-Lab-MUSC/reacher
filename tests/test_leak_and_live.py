"""Resource leaks across session cycles, a serial drop mid-run, and a live server.

``tests/test_session_shutdown.py`` checks thread/fd counts after bare
``create -> destroy`` cycles. This file goes further, through the real HTTP
stack and a running program:

1. 10 full session cycles (with and without stopping before delete) return
   threads and fds to the post-warm-up baseline.
2. A serial read failure mid-run is surfaced as a ``disconnect`` with
   ``reconnecting: true`` and then either recovers (blip) or gives up with a
   final ``disconnect`` and ``program_running`` false (hold); the session stays
   deletable in both.
3. A real uvicorn server on a loopback port, a real WebSocket client, and a
   running simulator session: the only test here that uses a socket.
"""

# ruff: noqa: F811

import json
import logging
import socket
import threading
import time

import httpx
import pytest
import serial
import uvicorn
import websockets.sync.client

import reacher.discovery as app_discovery
import reacher.api.routers.websocket as ws_router
import reacher.kernel.reacher as kernel_reacher
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.simulator import SimulatedSerial
from reacher.uploader.boards import SIMULATOR_PORT
from tests.sim_harness import (
    AUTH,
    assert_no_leak,
    connect,
    create_session,
    delete_session,
    disconnect,
    fast_sim_env,
    sim_client,  # noqa: F401
    simulator_session,
    snapshot_resources,
    start,
    stop,
    wait_until,
)


class _CappedSleepTime:
    """Stands in for the ``time`` module inside the kernel only: ``sleep`` is capped.

    ``open_serial()`` sleeps 2 s on every connect, which would put 10 cycles
    past the test budget. Other modules keep the real ``time``.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def sleep(self, seconds):
        self._real.sleep(min(seconds, 0.01))


@pytest.fixture
def fast_kernel(monkeypatch):
    monkeypatch.setattr(kernel_reacher, "time", _CappedSleepTime(time))


def _settle_resources(before, timeout=5.0):
    """Give worker threads a moment to finish joining before the strict comparison."""
    try:
        wait_until(lambda: snapshot_resources()["threads"] <= before["threads"] + 1, timeout=timeout)
    except AssertionError:
        pass
    return snapshot_resources()


def _cycle(client, stop_before_delete):
    """One full lifecycle on a SIMULATOR session; every step must succeed."""
    resp = create_session(client, "fr")
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    try:
        assert connect(client, sid).status_code == 200
        assert start(client, sid).status_code == 200
        if stop_before_delete:
            assert stop(client, sid).status_code == 200
            assert disconnect(client, sid).status_code == 200
        assert delete_session(client, sid).status_code == 200
    finally:
        for call in (stop, disconnect, delete_session):
            try:
                call(client, sid)
            except Exception:
                pass


@pytest.mark.parametrize("stop_before_delete", [True, False], ids=["stop_then_delete", "delete_while_running"])
def test_leak_ten_cycles_return_threads_and_fds_to_baseline(sim_client, fast_kernel, stop_before_delete):
    _cycle(sim_client, stop_before_delete)  # warm-up: lazily created pools do not count

    before = snapshot_resources()
    for _ in range(10):
        _cycle(sim_client, stop_before_delete)

    after = _settle_resources(before)
    assert_no_leak(before, after, thread_slack=1, fd_slack=3)


def _drop_events(instance):
    """Record every kernel event (``event_type``, ``data``) while still forwarding it."""
    seen = []
    forward = instance.event_callback

    def record(sid, event_type, data):
        seen.append((event_type, dict(data)))
        forward(sid, event_type, data)

    instance.event_callback = record
    return seen


def _start_then_fail_reads(client, sid, monkeypatch, *, reopen_fails):
    """Start the program, then make the simulated port raise on its next read.

    The kernel only calls ``readline`` when ``in_waiting`` is set, and a
    simulator that has gone quiet never sets it, so ``in_waiting`` reports data
    while the fault is armed. With ``reopen_fails`` the reconnect attempts raise
    too (the cable stays out).
    """
    instance = client.app.state.session_manager.get_instance(sid)
    ser = instance.ser
    assert start(client, sid).status_code == 200

    real_readline = ser.readline
    real_in_waiting = SimulatedSerial.in_waiting.fget
    fault = {"armed": True}

    def failing_readline():
        if fault["armed"]:
            fault["armed"] = False
            raise serial.SerialException("simulated cable drop")
        return real_readline()

    def in_waiting(self):
        if self is ser and fault["armed"]:
            return 1
        return real_in_waiting(self)

    monkeypatch.setattr(ser, "readline", failing_readline)
    monkeypatch.setattr(SimulatedSerial, "in_waiting", property(in_waiting))
    if reopen_fails:

        def refuse_open():
            raise serial.SerialException("port still unplugged")

        monkeypatch.setattr(ser, "open", refuse_open)
    return instance


def _fast_reconnect(instance, monkeypatch):
    monkeypatch.setattr(instance, "_SERIAL_RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(instance, "_SERIAL_RECONNECT_RETRIES", 3)


def test_serial_drop_blip_reconnects_and_session_recovers(sim_client, monkeypatch):
    with simulator_session(sim_client, "fr") as sid:
        instance = sim_client.app.state.session_manager.get_instance(sid)
        _fast_reconnect(instance, monkeypatch)
        seen = _drop_events(instance)
        _start_then_fail_reads(sim_client, sid, monkeypatch, reopen_fails=False)

        wait_until(lambda: any(t == "reconnected" for t, _ in seen), timeout=5.0)

        drops = [d for t, d in seen if t == "disconnect"]
        assert drops and drops[0]["reconnecting"] is True, seen
        assert drops[0]["reason"] == "simulated cable drop"
        assert not any(t == "disconnect" and d["reconnecting"] is False for t, d in seen), seen
        state = sim_client.get(f"/api/sessions/{sid}", headers=AUTH).json()["state"]
        assert state == "running"
        # Closing the port stopped the simulator and no CONTROLLER END will come back, so
        # teardown would wait the firmware's 8 s fallback. Mark the END as received.
        instance._controller_end_received.set()


def test_serial_drop_hold_gives_up_and_session_stays_deletable(sim_client, monkeypatch):
    with simulator_session(sim_client, "fr") as sid:
        instance = sim_client.app.state.session_manager.get_instance(sid)
        _fast_reconnect(instance, monkeypatch)
        seen = _drop_events(instance)
        _start_then_fail_reads(sim_client, sid, monkeypatch, reopen_fails=True)

        wait_until(
            lambda: any(t == "disconnect" and d["reconnecting"] is False for t, d in seen),
            timeout=5.0,
        )

        assert not any(t == "reconnected" for t, _ in seen), seen
        first_drop = next(d for t, d in seen if t == "disconnect")
        assert first_drop["reconnecting"] is True
        assert instance.program_running is False
        assert sim_client.get(f"/api/sessions/{sid}", headers=AUTH).status_code == 200

        resp = delete_session(sim_client, sid)
        assert resp.status_code == 200, resp.text
        assert sim_client.get(f"/api/sessions/{sid}", headers=AUTH).status_code == 404


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.timeout(60)
def test_live_uvicorn_websocket_streams_running_session(monkeypatch, fast_kernel):
    fast_sim_env(monkeypatch)
    monkeypatch.setenv("REACHER_NO_BROWSER", "1")
    # The app lifespan would advertise over mDNS and probe the LAN; this test stays on loopback.
    monkeypatch.setattr(app_discovery, "start", lambda *args, **kwargs: None)

    async def no_scan(*args, **kwargs):
        return None

    monkeypatch.setattr(app_discovery, "run_scan_loop", no_scan)
    # uvicorn reconfigures its loggers (level, handlers, propagate) when it starts.
    uvicorn_loggers = {
        n: (lg.level, lg.propagate, list(lg.handlers))
        for n in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
        for lg in [logging.getLogger(n)]
    }
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning", log_config=None))
    thread = threading.Thread(target=server.run, name="live-uvicorn", daemon=True)
    thread.start()
    sid = None
    try:
        wait_until(lambda: server.started or not thread.is_alive(), timeout=10.0)
        assert server.started, "uvicorn did not start"

        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, headers=AUTH, timeout=10.0) as http:
            sid = http.post("/api/sessions", json={"port": SIMULATOR_PORT, "paradigm": "fr"}).json()["session_id"]
            try:
                assert (
                    http.post(
                        f"/api/serial/{sid}/connect", json={"port": SIMULATOR_PORT, "baudrate": 115200}
                    ).status_code
                    == 200
                )

                received = []
                with websockets.sync.client.connect(
                    f"ws://127.0.0.1:{port}/ws/{sid}?token={API_KEY}", open_timeout=5, close_timeout=1
                ) as ws:
                    first = json.loads(ws.recv(timeout=5))
                    assert first["type"] == "session_state" and first["session_id"] == sid
                    received.append(first)

                    assert http.post(f"/api/program/{sid}/start").status_code == 200
                    deadline = time.monotonic() + 15.0
                    while len(received) < 3 and time.monotonic() < deadline:
                        received.append(json.loads(ws.recv(timeout=5)))
                    assert len(received) >= 3, f"only {len(received)} messages over the live socket"
                    assert all(m["session_id"] == sid for m in received)

                    assert http.post(f"/api/program/{sid}/stop").status_code == 200
                    stopped = http.get(f"/api/sessions/{sid}").json()
                    assert stopped["state"] == "stopped"
            finally:
                http.post(f"/api/program/{sid}/stop")
                http.post(f"/api/serial/{sid}/disconnect")
                assert http.delete(f"/api/sessions/{sid}").status_code == 200
                sid = None
    finally:
        if sid is not None:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=AUTH, timeout=5.0) as http:
                http.delete(f"/api/sessions/{sid}")
        server.should_exit = True
        thread.join(timeout=20.0)
        # The server's loop is closed now; the websocket module caches it globally and a
        # stale reference makes every later enqueue_event raise "Event loop is closed".
        for name in ("_loop", "_notify", "_broadcast_task", "_watchdog_task"):
            setattr(ws_router, name, None)
        for n, (level, propagate, handlers) in uvicorn_loggers.items():
            lg = logging.getLogger(n)
            lg.setLevel(level)
            lg.propagate = propagate
            lg.handlers[:] = handlers
        assert not thread.is_alive(), "uvicorn server thread outlived the test"
