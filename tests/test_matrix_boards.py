"""Config matrix rows 15, 16, 19-23, 25, 27: board gates, lite strip, Pavlovian.

Rows 19, 20 and 27 run on the stepped-clock simulator (same seed, same output);
the rest go over HTTP through the real SessionManager and kernel. Row 20 is a
known defect and is pinned ``xfail(strict=True)``. Pin overrides saved by a test
are cleared in a ``finally`` so the in-memory SIMn store does not leak across tests.
"""

import logging
from contextlib import contextmanager

import pytest

from reacher.kernel.commands import CommandCode, build_command_payload
from reacher.kernel.simulator import FRAME_INTERVAL_MS
from tests import sim_harness
from tests.sim_harness import (
    AUTH,
    connect,
    create_session,
    delete_session,
    events,
    send,
    simulator_session,
    start,
    stepped_sim,
    upload,
    wait_until,
)

sim_client = sim_harness.sim_client  # pytest fixture, bound here so params below don't shadow an import

CS_TRIAL_TYPES = {"CS_PLUS", "CS_MINUS"}

LITE_PARADIGMS = ["fr_lite", "pr_lite", "vi_lite", "omission_lite"]
TWO_PHOTON_CODES = [900, 976, 1101, 1176]

LASER_ARM = int(CommandCode.LASER_ARM)
LASER_CONTINGENT = int(CommandCode.LASER_MODE_CONTINGENT)
LASER_CS_BOTH = int(CommandCode.PAV_LASER_CS_BOTH)
LASER_PHASE_REWARD = int(CommandCode.PAV_LASER_PHASE_REWARD)
LASER_PHASE_CUE = int(CommandCode.PAV_LASER_PHASE_CUE)
LASER_TRIGGER_LH_ONLY = int(CommandCode.LASER_TRIGGER_LH_ONLY)
PAV_COUNTERBALANCE = int(CommandCode.PAV_COUNTERBALANCE)
PAV_CS_PLUS_COUNT = int(CommandCode.PAV_CS_PLUS_COUNT)
PAV_CS_MINUS_COUNT = int(CommandCode.PAV_CS_MINUS_COUNT)
PAV_CS_PLUS_PROB = int(CommandCode.PAV_CS_PLUS_PROB)
PAV_CS_MINUS_PROB = int(CommandCode.PAV_CS_MINUS_PROB)
PAV_ITI_MEAN = int(CommandCode.PAV_ITI_MEAN)
PAV_ITI_MIN = int(CommandCode.PAV_ITI_MIN)
PAV_ITI_MAX = int(CommandCode.PAV_ITI_MAX)
SET_RATIO = int(CommandCode.SET_RATIO)
SET_ACTIVE_PUMP = int(CommandCode.SET_ACTIVE_PUMP)
MICROSCOPE_ARM = int(CommandCode.MICROSCOPE_ARM)
SLM_ARM = int(CommandCode.SLM_ARM)
LICK_ARM = int(CommandCode.LICK_ARM)

PAV_ARM = (1301, 301, 311, 401, 411)  # LH lever, cue, cue2, pump, pump2 (Pavlovian boots disarmed)

ITI_RANGE_MS = (2000, 5000)


@contextmanager
def _rig(client, paradigm, board="mega"):
    """``simulator_session`` that also clears the pin overrides saved under its SIMn port."""
    with simulator_session(client, paradigm, board) as sid:
        port = client.app.state.session_manager.get_session(sid).port
        try:
            yield sid, port
        finally:
            client.delete("/api/serial/pin-overrides", params={"port": port}, headers=AUTH)


def _pavlovian_cfg(counts=(5, 5), prob=(100, 0), iti=(2000, 3000, 5000)):
    return [
        (PAV_CS_PLUS_PROB, "probability", prob[0]),
        (PAV_CS_MINUS_PROB, "probability", prob[1]),
        (PAV_CS_PLUS_COUNT, "count", counts[0]),
        (PAV_CS_MINUS_COUNT, "count", counts[1]),
        (PAV_ITI_MEAN, "iti_mean", iti[1]),
        (PAV_ITI_MIN, "iti_min", iti[0]),
        (PAV_ITI_MAX, "iti_max", iti[2]),
    ]


def _trial_types(msgs):
    return {m["trial"]: m["trial_type"] for m in events(msgs, device="PAVLOV", event="TRIAL_START")}


def _host_payload(code, value):
    """What the host actually puts on the wire for *code*, via the same builder REACHER uses."""
    return build_command_payload(code, value)


# ---------------------------------------------------------------------------
# Row 15: vi_lite accepts 685 over HTTP but the firmware has no LH laser contingency
# ---------------------------------------------------------------------------


def test_row15_vi_lite_lh_laser_is_accepted_then_firmware_rejects(sim_client, caplog):
    caplog.set_level(logging.ERROR)
    with _rig(sim_client, "vi_lite", board="uno") as (sid, _port):
        # The HTTP gate is the paradigm list, which declares 685 for vi_lite (KNOWN_FIRMWARE_GAPS).
        resp = send(sim_client, sid, LASER_TRIGGER_LH_ONLY)
        assert resp.status_code == 200, resp.text
        assert resp.json()["command"] == "LASER_TRIGGER_LH_ONLY"

        needle = "Command not found (LASER_TRIGGER_LH_ONLY=685)"
        wait_until(lambda: any(needle in r.getMessage() for r in caplog.records))


# ---------------------------------------------------------------------------
# Row 16: two-photon commands are refused on every lite paradigm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", TWO_PHOTON_CODES)
@pytest.mark.parametrize("paradigm", LITE_PARADIGMS)
def test_row16_lite_paradigm_rejects_two_photon_command(sim_client, paradigm, code):
    # The paradigm gate runs before the connection check and does not depend on the board,
    # so an unconnected session is enough (a UNO flash + connect costs ~7 s here).
    resp = create_session(sim_client, paradigm)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    try:
        resp = send(sim_client, sid, code, 5)
        assert resp.status_code == 400, resp.text
        assert "not available" in resp.json()["detail"]
    finally:
        delete_session(sim_client, sid)


# ---------------------------------------------------------------------------
# Row 19: Pavlovian trial structure, laser filter x phase, counts and ITI
# ---------------------------------------------------------------------------


def test_row19_pavlovian_5_5_rewards_only_on_cs_plus():
    msgs = stepped_sim(
        "pavlovian",
        commands=_pavlovian_cfg() + [LASER_ARM, LASER_CONTINGENT, LASER_CS_BOTH, LASER_PHASE_REWARD],
        arm=PAV_ARM,
        horizon_ms=120_000,
    )
    types = _trial_types(msgs)
    assert len(types) == 10
    assert set(types.values()) <= CS_TRIAL_TYPES

    delivered = events(msgs, device="PAVLOV", event="REWARD_DELIVERED")
    omitted = events(msgs, device="PAVLOV", event="REWARD_OMITTED")
    assert len(delivered) == 5
    assert {types[m["trial"]] for m in delivered} == {"CS_PLUS"}
    assert len(omitted) == 5
    assert {types[m["trial"]] for m in omitted} == {"CS_MINUS"}
    assert len(events(msgs, device="PAVLOV", event="ALL_TRIALS_COMPLETE")) == 1

    itis = [m["iti_ms"] for m in events(msgs, device="PAVLOV", event="TRIAL_START")]
    assert all(ITI_RANGE_MS[0] <= t <= ITI_RANGE_MS[1] for t in itis), itis


@pytest.mark.parametrize(
    ("phase_code", "anchor_events"),
    [
        (LASER_PHASE_REWARD, ("REWARD_DELIVERED", "REWARD_OMITTED")),
        (LASER_PHASE_CUE, ("TRIAL_START",)),
    ],
    ids=["reward_phase", "cue_phase"],
)
def test_row19_laser_fires_only_in_selected_phase(phase_code, anchor_events):
    msgs = stepped_sim(
        "pavlovian",
        commands=_pavlovian_cfg() + [LASER_ARM, LASER_CONTINGENT, LASER_CS_BOTH, phase_code],
        arm=PAV_ARM,
        horizon_ms=120_000,
    )
    anchors = {m["timestamp"] for name in anchor_events for m in events(msgs, device="PAVLOV", event=name)}
    laser = events(msgs, device="LASER", event="STIMULATION")
    assert len(laser) == 10  # CS_BOTH: every trial, both types
    assert {m["start_timestamp"] for m in laser} <= anchors


def test_row19_zero_counts_produces_no_trials():
    msgs = stepped_sim("pavlovian", commands=_pavlovian_cfg(counts=(0, 0)), arm=PAV_ARM, horizon_ms=120_000)
    assert events(msgs, device="PAVLOV", event="TRIAL_START") == []
    assert events(msgs, device="PAVLOV", event="ALL_TRIALS_COMPLETE") == []


@pytest.mark.xfail(
    strict=True,
    reason="simulator omits CONTROLLER END when Pavlovian completes itself (firmware EndSession prints it), "
    "so the kernel waits 8 s for END before the run ends",
)
def test_row19_zero_counts_session_ends_at_once(sim_client):
    with _rig(sim_client, "pavlovian") as (sid, _port):
        assert send(sim_client, sid, PAV_CS_PLUS_COUNT, 0).status_code == 200
        assert send(sim_client, sid, PAV_CS_MINUS_COUNT, 0).status_code == 200
        assert start(sim_client, sid).status_code == 200
        wait_until(lambda: sim_client.get(f"/api/sessions/{sid}", headers=AUTH).json()["state"] != "running")


# ---------------------------------------------------------------------------
# Row 20: counterbalance. The host sends the payload key "enabled"; pavlovian.ino reads "counterbalance".
# ---------------------------------------------------------------------------


def _cs_plus_cue_devices(msgs):
    """Tone device (CUE / CUE_2) that sounded on each CS+ trial, keyed by trial start time."""
    tones = {m["start_timestamp"]: m["device"] for m in events(msgs, event="TONE")}
    return [
        tones[m["timestamp"]]
        for m in events(msgs, device="PAVLOV", event="TRIAL_START")
        if m["trial_type"] == "CS_PLUS"
    ]


def test_row20_counterbalance_firmware_key_swaps_cues():
    """Control: the key pavlovian.ino actually reads swaps the CS+ tone onto CUE_2."""
    msgs = stepped_sim(
        "pavlovian",
        commands=_pavlovian_cfg() + [{"cmd": PAV_COUNTERBALANCE, "counterbalance": True}],
        arm=PAV_ARM,
        horizon_ms=120_000,
    )
    devices = _cs_plus_cue_devices(msgs)
    assert devices and set(devices) == {"CUE_2"}


@pytest.mark.xfail(
    strict=True,
    reason="CommandSpec 212 sends 'enabled' but pavlovian.ino reads 'counterbalance'; counterbalance is silently off",
)
def test_row20_counterbalance_from_host_swaps_cues():
    msgs = stepped_sim(
        "pavlovian",
        commands=_pavlovian_cfg() + [_host_payload(PAV_COUNTERBALANCE, True)],
        arm=PAV_ARM,
        horizon_ms=120_000,
    )
    devices = _cs_plus_cue_devices(msgs)
    assert devices and set(devices) == {"CUE_2"}


# ---------------------------------------------------------------------------
# Row 21: operant-only commands are refused on pavlovian
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "value"), [(SET_RATIO, 5), (SET_ACTIVE_PUMP, 1)], ids=["201", "221"])
def test_row21_pavlovian_rejects_operant_command(sim_client, code, value):
    with _rig(sim_client, "pavlovian") as (sid, _port):
        resp = send(sim_client, sid, code, value)
        assert resp.status_code == 400, resp.text
        assert "not available for pavlovian" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Row 22: pin validation by board, over HTTP
# ---------------------------------------------------------------------------


def _put_pins(client, sid, assignments):
    return client.put(f"/api/hardware/{sid}/pins", json={"assignments": assignments}, headers=AUTH)


def test_row22_uno_rejects_non_pwm_cue_pin(sim_client):
    with _rig(sim_client, "fr_lite", board="uno") as (sid, _port):
        resp = _put_pins(sim_client, sid, {"cue": 2})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "pin_violations"


def test_row22_mega_rejects_out_of_range_lever_pin(sim_client):
    with _rig(sim_client, "fr") as (sid, _port):
        resp = _put_pins(sim_client, sid, {"lever_rh": 60})
        assert resp.status_code == 422, resp.text


def test_row22_mega_rejects_slm_pin_outside_pcint0(sim_client):
    with _rig(sim_client, "fr") as (sid, _port):
        resp = _put_pins(sim_client, sid, {"slm": 9})
        assert resp.status_code == 422, resp.text


def test_row22_mega_accepts_valid_pins(sim_client):
    with _rig(sim_client, "fr") as (sid, _port):
        resp = _put_pins(sim_client, sid, {"cue": 11, "pump": 6})
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] == {"cue": 11, "pump": 6}
        assert resp.json()["errors"] == []


# ---------------------------------------------------------------------------
# Row 23: an override saved under uno is not replayed onto a mega
# ---------------------------------------------------------------------------


def _reconnect_as(client, monkeypatch, sid, port, detected):
    """Disconnect, then connect with USB detection stubbed to *detected*.

    The simulator has no USB VID/PID, so detection is the one seam that cannot run
    for real here; stubbing it lets the board-keyed replay filter be exercised.
    """
    monkeypatch.setattr("reacher.api.routers.serial.detect_board_from_port", lambda _p: detected)
    client.post(f"/api/serial/{sid}/disconnect", headers=AUTH)
    resp = connect(client, sid, port=port)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tag_uno(client, sid):
    """Stand in for the uno flash: the board tag is what the upload route sets via set_board.

    A real uno flash costs a fixed 2 s reboot wait in the upload route, which these
    tests do not need; the tag is the only thing the override filter reads.
    """
    client.app.state.session_manager.set_board(sid, "uno")


def test_row23_uno_override_is_replayed_on_uno(sim_client, monkeypatch):
    with _rig(sim_client, "fr_lite") as (sid, port):
        _tag_uno(sim_client, sid)
        assert _put_pins(sim_client, sid, {"cue": 11}).status_code == 200
        body = _reconnect_as(sim_client, monkeypatch, sid, port, detected="uno")
        assert body.get("replayed_pins") == {"cue": 11}


def test_row23_uno_override_is_ignored_on_mega(sim_client, monkeypatch):
    with _rig(sim_client, "fr_lite") as (sid, port):
        _tag_uno(sim_client, sid)
        assert _put_pins(sim_client, sid, {"cue": 11}).status_code == 200
        body = _reconnect_as(sim_client, monkeypatch, sid, port, detected="mega")
        assert body.get("replayed_pins") == {}


# ---------------------------------------------------------------------------
# Row 25: start and upload state gates
# ---------------------------------------------------------------------------


def test_row25_upload_while_running_is_409(sim_client):
    with _rig(sim_client, "fr") as (sid, _port):
        assert start(sim_client, sid).status_code == 200
        try:
            resp = upload(sim_client, sid, "fr", "mega")
            assert resp.status_code == 409, resp.text
        finally:
            sim_client.post(f"/api/program/{sid}/stop", headers=AUTH)


def test_row25_start_while_disconnected_is_409(sim_client):
    resp = create_session(sim_client, "fr")
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    try:
        resp = start(sim_client, sid)
        assert resp.status_code == 409, resp.text
    finally:
        sim_client.delete(f"/api/sessions/{sid}", headers=AUTH)


# ---------------------------------------------------------------------------
# Row 27: microscope frames run at 100 ms on a Pavlovian rig with lick armed
# ---------------------------------------------------------------------------


def test_row27_pavlovian_microscope_frames_every_100ms():
    msgs = stepped_sim(
        "pavlovian",
        commands=_pavlovian_cfg() + [LICK_ARM, MICROSCOPE_ARM, SLM_ARM],
        arm=PAV_ARM,
        horizon_ms=10_000,
    )
    frames = [m["timestamp"] for m in msgs if m.get("level") == "008"]
    assert len(frames) >= 50
    assert FRAME_INTERVAL_MS == 100
    assert {b - a for a, b in zip(frames, frames[1:])} == {100}
