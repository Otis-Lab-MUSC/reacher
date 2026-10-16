"""Shared harness for simulator-driven end-to-end tests.

Real stack, no mocks: ``TestClient(create_app())`` -> SessionManager -> REACHER
kernel -> ``FirmwareSimulator`` on port ``SIMULATOR``. Import from other test
modules as ``from tests.sim_harness import sim_client, simulator_session, ...``.

HTTP wrappers return the raw ``Response`` so callers can assert status codes.
"""

import json
import os
import queue
import threading
import time
from collections import Counter
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.simulator import FirmwareSimulator

AUTH = {"Authorization": f"Bearer {API_KEY}"}

# Same default arming as tests/test_simulator_follows_config.py (RH lever, cues, pumps).
DEFAULT_ARM = (1001, 301, 311, 401, 411)


def fast_sim_env(monkeypatch, seed=1):
    """Fast, reproducible simulator: 400x clock, fixed seed, instant simulated flash.

    Call before the app/session is created.
    """
    monkeypatch.setenv("REACHER_SIM_SPEED", "400")
    monkeypatch.setenv("REACHER_SIM_SEED", str(seed))
    monkeypatch.setenv("REACHER_SIM_UPLOAD_SECONDS", "0")
    cap_kernel_sleeps(monkeypatch)
    stub_discovery(monkeypatch)


class _CappedTime:
    """Stand-in for the ``time`` module whose ``sleep`` is capped (everything else delegates)."""

    def __init__(self, cap):
        self._cap = cap

    def sleep(self, seconds):
        time.sleep(min(seconds, self._cap))

    def __getattr__(self, name):
        return getattr(time, name)


def cap_kernel_sleeps(monkeypatch, cap=0.05):
    """Cap the kernel's hard-coded ``time.sleep`` calls (1-2 s in connect, restart, teardown).

    The kernel sleeps to let real boards reboot; the simulator needs none of it. Only the kernel
    module's ``time`` reference is replaced, so the rest of the process keeps real sleeps.
    """
    import reacher.kernel.reacher as kernel

    monkeypatch.setattr(kernel, "time", _CappedTime(cap))


@pytest.fixture
def sim_client(monkeypatch):
    """``TestClient`` over an unmocked app, with ``fast_sim_env`` already applied."""
    fast_sim_env(monkeypatch)
    with TestClient(create_app()) as client:
        yield client


def wait_until(predicate, timeout=5.0, interval=0.02):
    """Poll *predicate* until truthy; return its last value or raise AssertionError on timeout."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value:
        if time.monotonic() >= deadline:
            name = getattr(predicate, "__name__", repr(predicate))
            raise AssertionError(f"wait_until timed out after {timeout}s waiting for {name} (last value: {value!r})")
        time.sleep(interval)
        value = predicate()
    return value


# ---------------------------------------------------------------------------
# HTTP wrappers (raw Response)
# ---------------------------------------------------------------------------


def create_session(client, paradigm, port="SIMULATOR"):
    return client.post("/api/sessions", json={"port": port, "paradigm": paradigm}, headers=AUTH)


def connect(client, sid, port="SIMULATOR"):
    return client.post(f"/api/serial/{sid}/connect", json={"port": port, "baudrate": 115200}, headers=AUTH)


def disconnect(client, sid):
    return client.post(f"/api/serial/{sid}/disconnect", headers=AUTH)


def delete_session(client, sid):
    return client.delete(f"/api/sessions/{sid}", headers=AUTH)


def send(client, sid, code, value=None):
    return client.post(f"/api/hardware/{sid}/command", json={"code": int(code), "value": value}, headers=AUTH)


def start(client, sid):
    return client.post(f"/api/program/{sid}/start", headers=AUTH)


def stop(client, sid):
    return client.post(f"/api/program/{sid}/stop", headers=AUTH)


def pause(client, sid):
    return client.post(f"/api/program/{sid}/pause", headers=AUTH)


def resume(client, sid):
    """There is no resume route: ``/pause`` toggles, so this is the same POST as :func:`pause`."""
    return client.post(f"/api/program/{sid}/pause", headers=AUTH)


def split(client, sid):
    return client.post(f"/api/program/{sid}/split", headers=AUTH)


def restart(client, sid):
    return client.post(f"/api/program/{sid}/restart", headers=AUTH)


def reset(client, sid):
    """``POST /api/sessions/{sid}/reset``. The route takes no options (nothing to keep)."""
    return client.post(f"/api/sessions/{sid}/reset", headers=AUTH)


def set_limit(client, sid, type="Time", time_limit=None, infusion_limit=None, delay=None, **extra):
    """``POST /api/program/{sid}/limit``; ``extra`` passes e.g. ``counted_pumps=[...]`` through."""
    body = {"type": type, **extra}
    for key, val in (("time_limit", time_limit), ("infusion_limit", infusion_limit), ("delay", delay)):
        if val is not None:
            body[key] = val
    return client.post(f"/api/program/{sid}/limit", json=body, headers=AUTH)


def config(client, sid):
    """Parsed JSON of ``GET /api/hardware/{sid}/config``."""
    return client.get(f"/api/hardware/{sid}/config", headers=AUTH).json()


def behavior(client, sid):
    """The behavior data list (``GET /api/data/{sid}/behavior`` -> ``data``)."""
    return client.get(f"/api/data/{sid}/behavior", headers=AUTH).json()["data"]


def upload(client, sid, paradigm, board="mega"):
    """Simulated firmware flash. Blocks ~2 s: the route sleeps while the 'board' reboots."""
    return client.post(f"/api/firmware/upload/{sid}", json={"paradigm": paradigm, "board": board}, headers=AUTH)


@contextmanager
def simulator_session(client, paradigm, board="mega"):
    """Create + connect a SIMULATOR session, yield its id, always tear down.

    A SIMULATOR session starts with ``board=None``; the only way onto ``uno`` is
    the firmware upload route (it calls ``set_board``), so for ``board != "mega"``
    this uploads that board's hex after connecting. Mega sessions skip the upload.
    Teardown (stop, disconnect, delete) swallows errors.
    """
    resp = create_session(client, paradigm)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    try:
        conn = connect(client, sid)
        assert conn.status_code == 200, conn.text
        if board != "mega":
            up = upload(client, sid, paradigm, board)
            assert up.status_code == 200, up.text
        yield sid
    finally:
        for call in (stop, disconnect, delete_session):
            try:
                call(client, sid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Stepped-clock simulator
# ---------------------------------------------------------------------------


def stepped_sim(paradigm, commands=(), arm=None, horizon_ms=60_000, seed=1):
    """Run a stepped-clock ``FirmwareSimulator`` and return every emitted message (parsed dicts).

    *arm* and *commands* are sequences of command codes, ``(code, key, value)``
    tuples or ready ``{"cmd": ...}`` dicts; *arm* defaults to ``DEFAULT_ARM``
    and is sent first. Same seed -> same output.
    """
    q = queue.Queue()
    sim = FirmwareSimulator(q, paradigm=paradigm, seed=seed, realtime=False)
    for c in _cmds(DEFAULT_ARM if arm is None else arm) + _cmds(commands):
        sim.handle_command(c)
    sim.start()
    sim.run_until(horizon_ms)
    msgs = []
    while not q.empty():
        msgs.append(json.loads(q.get()))
    return msgs


def _cmds(spec):
    out = []
    for item in spec:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, tuple):
            code, key, value = item
            out.append({"cmd": code, key: value})
        else:
            out.append({"cmd": item})
    return out


def events(msgs, device=None, event=None, cls=None):
    """Level-007 behavioral messages, optionally filtered by device / event / class (str or tuple)."""

    def match(want, got):
        return want is None or got in ((want,) if isinstance(want, str) else want)

    return [
        m for m in msgs
        if m.get("level") == "007"
        and match(device, m.get("device"))
        and match(event, m.get("event"))
        and match(cls, m.get("class"))
    ]


# ---------------------------------------------------------------------------
# Leak detection
# ---------------------------------------------------------------------------


def snapshot_resources():
    """``{"threads": active thread count, "fds": open fd count or None without /proc}``."""
    try:
        fds = len(os.listdir("/proc/self/fd"))
    except OSError:
        fds = None
    return {"threads": threading.active_count(), "fds": fds}


def assert_no_leak(before, after, thread_slack=1, fd_slack=3):
    """Fail if *after* holds more threads/fds than *before* plus the slack."""
    extra_threads = after["threads"] - before["threads"]
    assert extra_threads <= thread_slack, (
        f"thread leak: {before['threads']} -> {after['threads']} (slack {thread_slack})"
    )
    if before["fds"] is not None and after["fds"] is not None:
        extra_fds = after["fds"] - before["fds"]
        assert extra_fds <= fd_slack, f"fd leak: {before['fds']} -> {after['fds']} (slack {fd_slack})"


# ---------------------------------------------------------------------------
# Reconciliation invariant
# ---------------------------------------------------------------------------


def assert_reconciled(client, sid):
    """Behavior data must agree with the durable event log, and be time-ordered.

    Call after the session has run. Stops it first (idempotent) so the kernel has
    finished writing. Checks (a) per ``(device, event)`` counts of the API data
    equal the ``type: behavior`` lines of the session's ``event_log.jsonl`` and
    (b) timestamps are >= 0 and non-decreasing. The ZIP export is not checked: it
    writes under ``~/Downloads`` unless a destination is set, and the download
    route only serves paths under ``$HOME``, so it cannot stay inside ``tmp_path``.
    """
    stop(client, sid)
    sm = client.app.state.session_manager
    instance = sm.get_session(sid).instance
    # Reader/queue threads may still be draining the last events after stop.
    log_path = instance._event_log_path

    def read_log():
        if not os.path.exists(log_path):
            return []
        with open(log_path) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        return [r for r in rows if r.get("type") == "behavior"]

    def counts(rows):
        return Counter((r.get("device"), r.get("event")) for r in rows)

    try:
        wait_until(lambda: counts(read_log()) == counts(behavior(client, sid)), timeout=2.0)
    except AssertionError:
        pass  # the assert below reports the mismatch with both counters
    data = behavior(client, sid)
    logged = read_log()
    assert counts(data) == counts(logged), (
        f"behavior data and event_log.jsonl disagree: api={dict(counts(data))} log={dict(counts(logged))}"
    )

    stamps = [r["start_timestamp"] for r in data if r.get("start_timestamp") is not None]
    assert all(t >= 0 for t in stamps), f"negative timestamp in behavior data: {min(stamps)}"
    assert stamps == sorted(stamps), "behavior timestamps are not monotonic non-decreasing"


def stub_discovery(monkeypatch):
    """Replace mDNS registration and the subnet scan with no-ops.

    ``discovery.start``/``stop`` cost about 1.6 s and 1 s per app lifespan and announce the test
    app on the real LAN; no simulator test needs peers.
    """
    import reacher.api.app as app_module

    async def no_scan(*args, **kwargs):
        return None

    monkeypatch.setattr(app_module.discovery, "start", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module.discovery, "stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module.discovery, "run_scan_loop", no_scan)
