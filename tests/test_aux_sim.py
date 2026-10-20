"""Simulated AUX (tail-shock TTL) output: the draw, the events and the safety rules.

The simulator's ``AuxDraw`` is a bit-for-bit port of firmware ``AuxDraw.h``, so
the golden vectors here are the same ones the bench test checks on a real Mega
(build plan section 3).
"""

import json
import queue

import pytest

from reacher.kernel.simulator import PIN_AUX, AuxDraw, FirmwareSimulator

GOLDEN_FIRST_FIVE = [3336926330, 1697253807, 2816511904, 1955480042, 718842323]
GOLDEN_INDEPENDENT_P30 = ".SS.S......S...S.SS."
GOLDEN_BLOCK_K2_N5 = "S..S.SS...S.S...S.S."

ARM = (1001, 1401)  # RH lever + AUX; FR1 by default, fr timeout defaults to 0
OUTCOMES = ("SHOCK", "SHOCK_OMITTED", "SHOCK_SUPPRESSED")


def _drain(q):
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait()))
    return out


def _sim(paradigm="fr", seed=1):
    q = queue.Queue()
    return FirmwareSimulator(q, paradigm=paradigm, seed=seed, realtime=False), q


def _send(sim, *cmds):
    for c in cmds:
        sim.handle_command(c if isinstance(c, dict) else {"cmd": c})


def run(commands=(), arm=ARM, horizon_ms=600_000, seed=1, paradigm="fr"):
    sim, q = _sim(paradigm, seed)
    _send(sim, *arm, *commands)
    sim.start()
    sim.run_until(horizon_ms)
    return sim, _drain(q)


def aux_events(msgs):
    return [m for m in msgs if m.get("level") == "007" and m.get("device") == "AUX_1"]


def pattern(msgs):
    return "".join("S" if m["event"] == "SHOCK" else "." for m in aux_events(msgs))


def rewards(msgs):
    return [m for m in msgs if m.get("level") == "007" and m.get("device") == "PUMP_1"]


SEED_12345 = {"cmd": 1484, "seed": 12345}


# --- The draw itself ------------------------------------------------------


def test_golden_first_five_outputs():
    d = AuxDraw()
    d.reset(12345, 0, 1)
    assert [d.next() for _ in range(5)] == GOLDEN_FIRST_FIVE


def test_golden_independent_and_block():
    d = AuxDraw()
    d.reset(12345, 2, 5)
    assert "".join("S" if d.draw_independent(30) else "." for _ in range(20)) == GOLDEN_INDEPENDENT_P30
    d.reset(12345, 2, 5)
    assert "".join("S" if d.draw_block() else "." for _ in range(20)) == GOLDEN_BLOCK_K2_N5


def test_golden_negative_wrong_seed_does_not_match():
    d = AuxDraw()
    d.reset(12346, 2, 5)
    assert "".join("S" if d.draw_independent(30) else "." for _ in range(20)) != GOLDEN_INDEPENDENT_P30


def test_zero_seed_never_sticks_the_prng():
    d = AuxDraw()
    d.reset(0, 0, 1)
    assert d.next() != 0


# --- A simulated session reproduces the golden vectors -----------------------


def test_session_independent_p30_matches_golden():
    _, msgs = run([SEED_12345, {"cmd": 1481, "probability": 30}])
    assert pattern(msgs)[:20] == GOLDEN_INDEPENDENT_P30


def test_session_block_k2_n5_matches_golden():
    _, msgs = run([SEED_12345, {"cmd": 1480, "mode": 1}, {"cmd": 1483, "n": 5}, {"cmd": 1482, "k": 2}])
    assert pattern(msgs)[:20] == GOLDEN_BLOCK_K2_N5


def test_independent_rate_over_1000_rewards():
    _, msgs = run([{"cmd": 1481, "probability": 30}], horizon_ms=8_000_000, seed=7)
    outcomes = pattern(msgs)
    assert len(outcomes) >= 1000, f"only {len(outcomes)} rewards in the horizon"
    rate = outcomes[:1000].count("S") / 1000
    assert 0.25 <= rate <= 0.35, rate


@pytest.mark.parametrize(("k", "n"), [(2, 5), (0, 4), (3, 3), (1, 10)])
def test_block_mode_gives_exactly_k_per_n(k, n):
    _, msgs = run([{"cmd": 1480, "mode": 1}, {"cmd": 1483, "n": n}, {"cmd": 1482, "k": k}], seed=3)
    outcomes = pattern(msgs)
    full = len(outcomes) // n * n
    assert full >= 4 * n
    for i in range(0, full, n):
        assert outcomes[i:i + n].count("S") == k, (i, outcomes[i:i + n])


def test_one_outcome_per_reward_and_shock_spans_the_pulse():
    _, msgs = run([
        {"cmd": 472, "duration": 100}, {"cmd": 1481, "probability": 50}, {"cmd": 1472, "duration": 250},
    ], arm=(1001, 1401, 401))
    rewarded, aux = rewards(msgs), aux_events(msgs)
    assert len(rewarded) > 10
    assert [m["start_timestamp"] for m in aux] == [m["start_timestamp"] for m in rewarded]
    for m in aux:
        assert m["pin"] == PIN_AUX
        assert m["event"] in ("SHOCK", "SHOCK_OMITTED")
        width = m["end_timestamp"] - m["start_timestamp"]
        assert width == (250 if m["event"] == "SHOCK" else 0)


def test_disarmed_aux_logs_nothing():
    _, msgs = run([{"cmd": 1481, "probability": 100}], arm=(1001,))
    assert not aux_events(msgs)


def test_disarm_mid_session_stops_outcomes():
    sim, q = _sim()
    _send(sim, *ARM, {"cmd": 1481, "probability": 100})
    sim.start()
    sim.run_until(60_000)
    before = len(aux_events(_drain(q)))
    _send(sim, 1400)
    sim.run_until(120_000)
    assert before > 0
    assert not aux_events(_drain(q))


def test_fr_lite_and_other_sketches_have_no_aux():
    for paradigm in ("fr_lite", "pr", "vi"):
        sim, msgs = run([{"cmd": 1481, "probability": 100}], paradigm=paradigm)
        assert not aux_events(msgs), paradigm
        assert not any(m.get("device") == "AUX" for m in msgs), paradigm
        assert all(step.target != "AUX" for step in sim._chains[0]), paradigm


def test_aux_is_the_last_step_of_chain_0():
    sim, _ = _sim()
    _send(sim, {"cmd": 1477, "delay": 40}, {"cmd": 1472, "duration": 300})
    last = sim._chains[0][-1]
    assert len(sim._chains[0]) == 6
    assert (last.target, last.offset, last.param, last.source_filter) == ("AUX", 40, 300, None)


# --- Config line, seed and acks --------------------------------------------


def test_session_start_reports_one_aux_config_line_with_seed():
    _, msgs = run([SEED_12345, {"cmd": 1481, "probability": 30}], horizon_ms=0)
    cfg = [m for m in msgs if m.get("level") == "000" and m.get("device") == "AUX" and "param" not in m]
    assert cfg == [{
        "level": "000", "device": "AUX", "armed": True, "duration": 500, "onset_delay": 0, "mode": 0,
        "probability": 30, "block_k": 0, "block_n": 10, "seed": 12345, "test_fire": False,
    }]


def test_auto_seed_is_reported_and_reproduces_the_stream():
    _, msgs = run([{"cmd": 1481, "probability": 30}], seed=11)
    cfg = next(m for m in msgs if m.get("device") == "AUX" and "seed" in m and "param" not in m)
    assert cfg["seed"] != 0 and cfg["seed"] % 2 == 1
    # Replaying with the reported seed gives the same outcomes: the seed is the record.
    _, replay = run([{"cmd": 1484, "seed": cfg["seed"]}, {"cmd": 1481, "probability": 30}], seed=11)
    assert pattern(replay) == pattern(msgs)


def test_aux_auto_seed_does_not_perturb_the_rest_of_the_session():
    _, with_aux = run([{"cmd": 1481, "probability": 30}], seed=5)
    _, without = run(arm=(1001,), seed=5)
    strip = [m for m in with_aux if m.get("device") not in ("AUX", "AUX_1")]
    assert [m for m in strip if m.get("level") == "007"] == [m for m in without if m.get("level") == "007"]


def test_setters_ack_the_effective_value():
    sim, q = _sim()
    _send(sim, {"cmd": 1472, "duration": 99999}, {"cmd": 1472, "duration": 0}, {"cmd": 1477, "delay": 700000},
          {"cmd": 1480, "mode": 5}, {"cmd": 1481, "probability": 150}, {"cmd": 1483, "n": 0},
          {"cmd": 1483, "n": 500}, {"cmd": 1482, "k": 250}, {"cmd": 1484, "seed": -1},
          {"cmd": 1485, "enable": True})
    acks = [(m["param"], m["value"]) for m in _drain(q) if m.get("device") == "AUX"]
    assert acks == [
        ("duration", 5000), ("duration", 1), ("onset_delay", 600000), ("mode", 1), ("probability", 100),
        ("block_n", 1), ("block_n", 100), ("block_k", 100), ("seed", 0), ("test_fire", True),
    ]


def test_shrinking_n_reclamps_k_and_acks_it_first():
    sim, q = _sim()
    _send(sim, {"cmd": 1483, "n": 10}, {"cmd": 1482, "k": 8})
    _drain(q)
    _send(sim, {"cmd": 1483, "n": 5})
    assert [(m["param"], m["value"]) for m in _drain(q)] == [("block_k", 5), ("block_n", 5)]
    _send(sim, {"cmd": 1483, "n": 20})
    assert [(m["param"], m["value"]) for m in _drain(q)] == [("block_n", 20)]


def test_aux_test_logs_like_pump_test():
    sim, q = _sim()
    _send(sim, 1403)  # disarmed: AUX_TEST is an explicit operator action, armed or not
    assert _drain(q) == [{"level": "000", "device": "AUX_1", "param": "test", "value": "FIRED"}]


# --- Test contexts and forced-off rules -----------------------------------


def test_test_chain_is_suppressed_without_touching_the_prng():
    sim, q = _sim()
    _send(sim, 1401, SEED_12345, {"cmd": 1481, "probability": 100})
    state = sim._aux_draw.state
    _send(sim, 103)
    events = aux_events(_drain(q))
    assert [m["event"] for m in events] == ["SHOCK_SUPPRESSED"]
    assert events[0]["start_timestamp"] == events[0]["end_timestamp"]
    assert sim._aux_draw.state == state


def test_test_chain_fires_only_with_test_fire_opt_in():
    sim, q = _sim()
    _send(sim, 1401, {"cmd": 1481, "probability": 100}, {"cmd": 1485, "enable": True}, 103)
    assert [m["event"] for m in aux_events(_drain(q))] == ["SHOCK"]


def test_test_mode_is_a_test_context_and_session_start_clears_it():
    sim, q = _sim()
    _send(sim, *ARM, {"cmd": 1481, "probability": 100}, {"cmd": 104, "enable": True})
    assert sim._test_mode
    sim.start()
    assert not sim._test_mode
    sim.run_until(60_000)
    assert {m["event"] for m in aux_events(_drain(q))} == {"SHOCK"}


def test_pause_drops_a_delayed_outcome_like_the_firmware_pending_queue():
    """Scheduler::SetPaused clears pending actions; a delayed AUX step goes with them."""
    sim, q = _sim()
    _send(sim, 1401, {"cmd": 1481, "probability": 100}, {"cmd": 1477, "delay": 1000})
    sim.start()
    _drain(q)
    sim._fire_chain(0, sim._clock)
    _send(sim, {"cmd": 105, "paused": True}, {"cmd": 105, "paused": False})
    sim._heap = [e for e in sim._heap if e[2].__name__ == "<lambda>"]  # keep only chain steps
    sim.run_until(sim._clock + 5000)
    assert not aux_events(_drain(q))


def test_delayed_outcome_fires_at_reward_plus_delay_when_not_interrupted():
    sim, q = _sim()
    _send(sim, 1401, {"cmd": 1481, "probability": 100}, {"cmd": 1477, "delay": 1000})
    sim.start()
    _drain(q)
    t = sim._clock
    sim._fire_chain(0, t)
    sim._heap = [e for e in sim._heap if e[2].__name__ == "<lambda>"]
    sim.run_until(t + 5000)
    assert [(m["event"], m["start_timestamp"]) for m in aux_events(_drain(q))] == [("SHOCK", t + 1000)]


def test_identify_and_session_end_disarm_aux_and_clear_test_fire():
    sim, _ = _sim()
    _send(sim, 1401, {"cmd": 1485, "enable": True}, 102)
    assert not sim.aux_armed and not sim.aux_test_fire
    _send(sim, 1401, {"cmd": 1485, "enable": True})
    sim.start()
    sim.run_until(10_000)
    sim.stop()
    assert not sim.aux_armed and not sim.aux_test_fire


def test_test_mode_suppresses_a_reward_without_touching_the_prng():
    """Test mode alone (not just 'no session') must suppress the pulse."""
    sim, q = _sim()
    _send(sim, *ARM, {"cmd": 1481, "probability": 100})
    sim.start()
    sim._test_mode = True  # isolates the testMode term from the !running term
    _drain(q)
    state = sim._aux_draw.state
    sim._fire_chain(0, sim._clock)
    assert [m["event"] for m in aux_events(_drain(q))] == ["SHOCK_SUPPRESSED"]
    assert sim._aux_draw.state == state
    _send(sim, {"cmd": 1485, "enable": True})
    _drain(q)
    sim._fire_chain(0, sim._clock)
    assert [m["event"] for m in aux_events(_drain(q))] == ["SHOCK"]
