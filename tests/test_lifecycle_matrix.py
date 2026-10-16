"""Session lifecycle as a (state, action) matrix, plus concurrent-request races.

Each cell drives a real SIMULATOR session (real SessionManager, real kernel,
real FirmwareSimulator) into one lifecycle state and applies one action. The
expected outcome comes from the routers' own guards: a legal move is 2xx, an
illegal one is a refusal (409 for connect/start/disconnect/reset conflicts,
400 for pause/resume/split/restart in a state that cannot take them), and
nothing is ever 5xx. A cell that deviates from the pinned status is a defect and
is pinned as a strict xfail with the observed status in its reason.

``resume`` and ``pause`` are the same route (``POST /pause`` toggles), so they
differ only in the state they are applied to.
"""

import threading
import time

import pytest

import reacher.kernel.reacher as kernel_module
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from tests.sim_harness import (
    AUTH,
    connect,
    create_session,
    delete_session,
    disconnect,
    pause,
    fast_sim_env,
    reset,
    restart,
    resume,
    split,
    start,
    stop,
    upload,
    wait_until,
)

STATES = ["created", "connected", "running", "paused", "stopped", "disconnected"]
ACTIONS = {
    "connect": connect,
    "start": start,
    "pause": pause,
    "resume": resume,
    "split": split,
    "restart": restart,
    "stop": stop,
    "reset": reset,
    "disconnect": disconnect,
    "delete": delete_session,
}

OK, CONFLICT, REFUSED = "2xx", "409", "400"

# Derived from the routers' guards (api/routers/{serial,program,session}.py).
OUTCOMES = {
    # connect refuses a live run; start needs an open port and no live run or disconnect.
    ("created", "connect"): OK,
    ("connected", "connect"): OK,
    ("running", "connect"): CONFLICT,
    ("paused", "connect"): CONFLICT,
    ("stopped", "connect"): OK,
    ("disconnected", "connect"): OK,
    ("created", "start"): CONFLICT,  # "Connect the board first"
    ("connected", "start"): OK,
    ("running", "start"): CONFLICT,
    ("paused", "start"): CONFLICT,
    ("stopped", "start"): CONFLICT,  # stop closes the port: "Connect the board first"
    ("disconnected", "start"): CONFLICT,
    # pause/resume and split/restart only make sense while a run is live.
    ("running", "pause"): OK,
    ("paused", "pause"): OK,
    ("created", "pause"): REFUSED,
    ("connected", "pause"): REFUSED,
    ("stopped", "pause"): REFUSED,
    ("disconnected", "pause"): REFUSED,
    ("running", "resume"): OK,
    ("paused", "resume"): OK,
    ("created", "resume"): REFUSED,
    ("connected", "resume"): REFUSED,
    ("stopped", "resume"): REFUSED,
    ("disconnected", "resume"): REFUSED,
    ("running", "split"): OK,
    ("paused", "split"): OK,
    ("created", "split"): REFUSED,
    ("connected", "split"): REFUSED,
    ("stopped", "split"): REFUSED,
    ("disconnected", "split"): REFUSED,
    ("running", "restart"): OK,
    ("paused", "restart"): OK,
    ("created", "restart"): REFUSED,
    ("connected", "restart"): REFUSED,
    ("stopped", "restart"): REFUSED,
    ("disconnected", "restart"): REFUSED,
    # stop is idempotent ("not_running" is a 200), delete always removes.
    **{(s, "stop"): OK for s in STATES},
    **{(s, "delete"): OK for s in STATES},
    # reset refuses paused and disconnected, anything else re-opens the port.
    ("created", "reset"): OK,
    ("connected", "reset"): OK,
    ("running", "reset"): OK,
    ("paused", "reset"): CONFLICT,
    ("stopped", "reset"): OK,
    ("disconnected", "reset"): CONFLICT,
    ("created", "disconnect"): OK,
    ("connected", "disconnect"): OK,
    ("running", "disconnect"): CONFLICT,
    ("paused", "disconnect"): CONFLICT,
    ("stopped", "disconnect"): OK,
    ("disconnected", "disconnect"): OK,
}

# Known defects: cells whose observed status deviates from the pinned one (none today).
DEFECTS: dict[tuple[str, str], str] = {}


class _CappedSleepTime:
    """Stand-in for the ``time`` module inside the kernel only: ``sleep`` is capped at 20 ms.

    ``open_serial()`` sleeps 2 s for the board to boot and ``restart_program()`` 1 s; the
    real waits are right for hardware but would put several seconds on every cell. Only
    the kernel's name for ``time`` is replaced, so the rest of the process keeps real sleeps.
    """

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(seconds):
        time.sleep(min(seconds, 0.02))


@pytest.fixture(autouse=True)
def _capped_kernel_sleep(monkeypatch):
    monkeypatch.setattr(kernel_module, "time", _CappedSleepTime())


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """One app for the module: every cell builds and deletes its own session, and the
    per-cell app startup (~0.3 s) would otherwise dominate. Env is set here, not by the
    function-scoped autouse fixtures, because the app reads it at startup."""
    root = tmp_path_factory.mktemp("lifecycle")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("REACHER_NO_BROWSER", "1")
        mp.setenv("REACHER_LOG_DIR", str(root / "runs"))
        mp.setenv("REACHER_SESSION_LOG_DIR", str(root / "sessions"))
        fast_sim_env(mp)
        with TestClient(create_app()) as c:
            yield c


def _state(client, sid):
    return client.app.state.session_manager.get_session(sid).state


def _reach(client, sid, state):
    """Drive a fresh session from ``created`` into *state*.

    """
    if state == "created":
        return
    assert connect(client, sid).status_code == 200
    if state == "connected":
        return
    if state == "disconnected":
        assert disconnect(client, sid).status_code == 200
        return
    assert start(client, sid).status_code == 200
    if state == "running":
        return
    if state == "paused":
        assert pause(client, sid).status_code == 200
        return
    assert stop(client, sid).status_code == 200
    wait_until(lambda: _state(client, sid) == "stopped", timeout=3.0)


def _cells():
    for (state, action), expected in OUTCOMES.items():
        marks = ()
        if (state, action) in DEFECTS:
            marks = (pytest.mark.xfail(strict=True, reason=DEFECTS[(state, action)]),)
        yield pytest.param(state, action, expected, id=f"{state}-{action}", marks=marks)


@pytest.mark.parametrize("state,action,expected", list(_cells()))
def test_lifecycle_transition(client, state, action, expected):
    sid = create_session(client, "fr").json()["session_id"]
    try:
        _reach(client, sid, state)
        resp = ACTIONS[action](client, sid)

        assert resp.status_code < 500, f"{state} + {action} -> {resp.status_code}: {resp.text}"
        if expected == OK:
            assert 200 <= resp.status_code < 300, f"{state} + {action} should be legal: {resp.status_code} {resp.text}"
        else:
            want = int(expected)
            assert resp.status_code == want, f"{state} + {action} should be {want}: {resp.status_code} {resp.text}"
    finally:
        delete_session(client, sid)


# ---------------------------------------------------------------------------
# Races: two requests released together on a fresh session per iteration
# ---------------------------------------------------------------------------

ITERATIONS = 15


def _race(client, sid, first, second):
    """Run two ``(action, label)`` calls against *sid* at the same instant.

    Returns ``(statuses, errors)``: status per label, and any exception a worker
    thread raised (a thread that raised is a failure, not a silent pass).
    """
    barrier = threading.Barrier(2)
    statuses: dict[str, int] = {}
    errors: list[tuple[str, BaseException]] = []

    def run(label, call):
        try:
            barrier.wait(timeout=5)
            statuses[label] = call(client, sid).status_code
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append((label, exc))

    threads = [threading.Thread(target=run, args=(label, call), daemon=True) for label, call in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a request hung past 10 s"
    return statuses, errors


def _assert_coherent(client, sid, statuses, errors):
    assert errors == [], f"worker thread raised: {errors}"
    assert all(code < 500 for code in statuses.values()), f"5xx under race: {statuses}"
    get = client.get(f"/api/sessions/{sid}", headers=AUTH)
    if get.status_code == 404:
        return
    assert get.status_code == 200, get.text
    assert get.json()["state"] in ("connected", "running", "paused", "stopped", "disconnected", "idle", "uploading")
    assert delete_session(client, sid).status_code == 200


def _fresh(client, state):
    sid = create_session(client, "fr").json()["session_id"]
    _reach(client, sid, state)
    return sid


# reset() and disconnect() each join the serial reader, which takes ~1 s, so their
# races run fewer iterations to stay inside the per-test budget.
@pytest.mark.parametrize(
    "setup_state, first, second, iterations",
    [
        pytest.param("created", ("connect", connect), ("delete", delete_session), ITERATIONS, id="connect-vs-delete"),
        pytest.param("connected", ("start", start), ("delete", delete_session), ITERATIONS, id="start-vs-delete"),
        pytest.param("running", ("stop", stop), ("disconnect", disconnect), 8, id="stop-vs-disconnect"),
        pytest.param("connected", ("reset", reset), ("delete", delete_session), 6, id="reset-vs-delete"),
    ],
)
def test_race_pair_is_coherent(client, setup_state, first, second, iterations):
    for _ in range(iterations):
        sid = _fresh(client, setup_state)
        try:
            statuses, errors = _race(client, sid, first, second)
            _assert_coherent(client, sid, statuses, errors)
        finally:
            delete_session(client, sid)


def test_race_connect_during_firmware_upload_is_coherent(client, monkeypatch):
    # Read per call by the uploader, so this only slows the flash in this test.
    monkeypatch.setenv("REACHER_SIM_UPLOAD_SECONDS", "1")
    iterations = 2  # each pass is a 1 s flash plus a reconnect; 15 would exceed the per-test budget
    for _ in range(iterations):
        sid = _fresh(client, "connected")
        try:
            statuses, errors = _race(
                client,
                sid,
                ("upload", lambda c, s: upload(c, s, "fr", "mega")),
                ("connect", connect),
            )
            _assert_coherent(client, sid, statuses, errors)
        finally:
            delete_session(client, sid)
