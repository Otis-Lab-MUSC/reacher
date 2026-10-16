"""The shared simulator harness itself: it must work before other suites lean on it."""

# ruff: noqa: F811

import pytest

from tests.sim_harness import (
    AUTH,
    assert_no_leak,
    assert_reconciled,
    behavior,
    config,
    connect,
    create_session,
    delete_session,
    disconnect,
    events,
    pause,
    resume,
    simulator_session,
    sim_client,  # noqa: F401
    snapshot_resources,
    start,
    stepped_sim,
    stop,
    wait_until,
)


def test_full_fr_session_reconciles(sim_client):
    with simulator_session(sim_client, "fr") as sid:
        assert start(sim_client, sid).status_code == 200
        wait_until(lambda: len(behavior(sim_client, sid)) >= 1, timeout=8.0)
        assert stop(sim_client, sid).status_code == 200
        assert_reconciled(sim_client, sid)


def test_teardown_on_error_deletes_session(sim_client):
    sid = None
    with pytest.raises(RuntimeError):
        with simulator_session(sim_client, "fr") as sid:
            raise RuntimeError("boom")
    assert sim_client.get(f"/api/sessions/{sid}", headers=AUTH).status_code == 404


def test_wait_until_timeout_message():
    with pytest.raises(AssertionError, match="timed out after 0.05s"):
        wait_until(lambda: False, timeout=0.05, interval=0.01)


def test_wait_until_returns_last_value():
    assert wait_until(lambda: "ok", timeout=0.5) == "ok"


def test_snapshot_resources_shape_and_leak_check():
    snap = snapshot_resources()
    assert set(snap) == {"threads", "fds"}
    assert snap["threads"] >= 1
    assert snap["fds"] is None or snap["fds"] > 0
    assert_no_leak(snap, snap)
    with pytest.raises(AssertionError, match="thread leak"):
        assert_no_leak(snap, {**snap, "threads": snap["threads"] + 5})


def test_stepped_sim_reproducible_and_has_events():
    a = stepped_sim("fr", seed=3)
    b = stepped_sim("fr", seed=3)
    assert a == b
    assert events(a, device="LEVER_RH", event="PRESS")
    assert events(a, device="NO_SUCH_DEVICE") == []


def test_pause_and_resume_toggle(sim_client):
    with simulator_session(sim_client, "fr") as sid:
        assert start(sim_client, sid).status_code == 200
        assert pause(sim_client, sid).json()["status"] == "paused"
        assert resume(sim_client, sid).json()["status"] == "resumed"


def test_wrappers_return_raw_responses_and_config(sim_client):
    sid = create_session(sim_client, "fr").json()["session_id"]
    try:
        assert start(sim_client, sid).status_code == 409  # not connected yet
        assert connect(sim_client, sid).status_code == 200
        assert "firmware_info" in config(sim_client, sid)
    finally:
        disconnect(sim_client, sid)
        assert delete_session(sim_client, sid).status_code == 200


def test_uno_session_via_upload(sim_client):
    with simulator_session(sim_client, "fr_lite", board="uno") as sid:
        info = sim_client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert info["board"] == "uno"
