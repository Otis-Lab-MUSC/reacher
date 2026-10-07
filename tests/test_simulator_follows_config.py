"""The SIMULATOR derives every session from the configuration it was sent.

The old simulator replayed a pre-written script: ``_run_pr`` walked a hard-coded
ratio table (``pr_step`` and the initial ratio were stored and never read),
``_run_omission`` rolled a 40 % die each slot and invented an
``OMISSION_WITHHELD`` event, Pavlovian ignored reward probability and never
played the CS- cue, and a ``START`` event was never printed for a software
start. The simulated board is now a port of the firmware's scheduling logic
(Scheduler.cpp / Trigger.h / each sketch's ReconfigureChain / PavlovianScheduler)
driven by a stochastic animal, so the outcome of a run is whatever the firmware
would do with the configured settings.

Every test here runs on a stepped clock (``realtime=False``) with a fixed seed,
asserts the *firmware rule* against the emitted stream (e.g. "the k-th reward
coincides with the (k*N)-th ACTIVE press"), and where a setting is under test
runs the schedule at two values so a simulator that ignored the setting could
not pass. The few realtime tests at the end cover the worker thread, pause, and
the kernel-side limits that end a simulated run.
"""

import json
import queue
import time

import pytest

from reacher.kernel.reacher import REACHER
from reacher.kernel.simulator import FirmwareSimulator

ARM_FR = (1001, 301, 311, 401, 411, 501)  # RH lever, cue, cue2, pump, pump2, lick
ARM_FR_NO_LICK = (1001, 301, 311, 401, 411)
ARM_PAV = (1001, 1301, 301, 311, 401, 411, 501)

HORIZON_MS = 900_000


def _cmds(spec):
    """Accept a code, (code, value-key, value) shorthand or a ready dict."""
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


def run(paradigm, commands=(), arm=ARM_FR_NO_LICK, horizon_ms=HORIZON_MS, seed=1, stop=False):
    """Connect-time commands -> start -> run to *horizon_ms*; return every emitted message."""
    q = queue.Queue()
    sim = FirmwareSimulator(q, paradigm=paradigm, seed=seed, realtime=False)
    for c in _cmds(arm) + _cmds(commands):
        sim.handle_command(c)
    sim.start()
    sim.run_until(horizon_ms)
    if stop:
        sim.stop()
    msgs = []
    while not q.empty():
        msgs.append(json.loads(q.get()))
    run.sim = sim
    return msgs


def ev(msgs, device=None, event=None, cls=None):
    return [
        m for m in msgs
        if m.get("level") == "007"
        and (device is None or m.get("device") in ((device,) if isinstance(device, str) else device))
        and (event is None or m.get("event") in ((event,) if isinstance(event, str) else event))
        and (cls is None or m.get("class") == cls)
    ]


def presses(msgs, cls=None):
    return ev(msgs, ("LEVER_RH", "LEVER_LH"), "PRESS", cls)


def infusions(msgs):
    return ev(msgs, "PUMP_1", "INFUSION")


def starts(rows):
    return [m["start_timestamp"] for m in rows]


# ---------------------------------------------------------------------------
# Determinism and the session-start contract
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_same_seed_same_stream(self):
        a = run("fr", [(201, "ratio", 3), (472, "duration", 500)])
        b = run("fr", [(201, "ratio", 3), (472, "duration", 500)])
        assert a == b and len(a) > 50

    def test_different_seed_different_stream(self):
        a = run("fr", [(201, "ratio", 3), (472, "duration", 500)], seed=1)
        b = run("fr", [(201, "ratio", 3), (472, "duration", 500)], seed=2)
        assert a != b

    def test_env_seed(self, monkeypatch):
        monkeypatch.setenv("REACHER_SIM_SEED", "7")
        a = FirmwareSimulator(queue.Queue(), realtime=False)
        b = FirmwareSimulator(queue.Queue(), realtime=False)
        assert a._rng.random() == b._rng.random()


class TestSessionStart:
    def test_software_start_prints_the_start_event_before_the_config_dump(self):
        msgs = run("fr", horizon_ms=0)
        assert msgs[0] == {
            "level": "007", "device": "CONTROLLER", "event": "START", "timestamp": 0, "source": "software",
        }
        assert msgs[1]["level"] == "000" and msgs[1]["device"] == "CONTROLLER"

    def test_dump_reports_the_configuration_that_was_sent(self):
        msgs = run(
            "fr",
            [(1074, "timeout", 7000), (1077, "timeout_mode", 1), (472, "duration", 1234), (371, "frequency", 5000)],
            arm=(401, 301),
            horizon_ms=0,
        )
        controller = next(m for m in msgs if m.get("device") == "CONTROLLER" and m["level"] == "000")
        assert controller["timeout"] == 7000 and controller["timeout_mode"] == 1
        rows = {m["device"]: m for m in msgs if m["level"] == "000"}
        assert rows["PUMP"] == {"level": "000", "device": "PUMP", "armed": True, "duration": 1234}
        assert rows["CUE"]["armed"] is True and rows["CUE"]["frequency"] == 5000
        assert rows["LEVER_RH"] == {"level": "000", "device": "LEVER_RH", "armed": False, "reinforced": True}
        assert rows["LASER"]["armed"] is False

    def test_stop_prints_end_with_the_session_clock(self):
        msgs = run("fr", horizon_ms=42_000, stop=True)
        end = ev(msgs, "CONTROLLER", "END")
        assert len(end) == 1 and end[0]["timestamp"] == 42_000

    def test_no_invented_events(self):
        """OMISSION_WITHHELD was never a firmware event."""
        msgs = run("omission", [(203, "interval", 3000), (472, "duration", 1000)])
        assert not ev(msgs, event="OMISSION_WITHHELD")


class TestArming:
    """Firmware boots disarmed and IDENTIFY disarms; the UI sends ARM only for what the user armed."""

    def test_unarmed_devices_do_nothing(self):
        msgs = run("fr", [(201, "ratio", 1), (472, "duration", 500)], arm=())
        assert presses(msgs) == [] and infusions(msgs) == [] and not ev(msgs, "LICK_CIRCUIT")

    @pytest.mark.parametrize(
        "disarmed,gone,kept",
        [
            (400, "PUMP_1", "CUE_1"),   # pump off: reward chain fires cue only
            (300, "CUE_1", "PUMP_1"),   # cue off
            (310, "CUE_2", "CUE_1"),    # cue2 off
        ],
    )
    def test_reward_chain_honors_per_device_arming(self, disarmed, gone, kept):
        msgs = run("fr", [(201, "ratio", 2), (472, "duration", 500), (372, "duration", 500), (382, "duration", 500),
                          disarmed])
        assert not ev(msgs, gone) and ev(msgs, kept)

    def test_disarmed_lever_is_invisible_to_the_board(self):
        msgs = run("fr", [(201, "ratio", 1), (472, "duration", 500)], arm=(401, 301))
        assert presses(msgs) == [] and infusions(msgs) == []

    def test_identify_disarms_everything_when_idle(self):
        q = queue.Queue()
        sim = FirmwareSimulator(q, realtime=False, seed=1)
        for c in _cmds(ARM_FR):
            sim.handle_command(c)
        assert sim.pump_armed and sim.lever_rh_armed
        sim.handle_command({"cmd": 102})
        assert not (sim.pump_armed or sim.lever_rh_armed or sim.cue_armed or sim.lick_armed)

    def test_restart_restores_arms_but_an_explicit_change_wins(self):
        """restart_program() sends 100 then 101 with nothing between."""
        sim = FirmwareSimulator(queue.Queue(), realtime=False, seed=1)
        for c in _cmds(ARM_FR):
            sim.handle_command(c)
        sim.start()
        sim.stop()
        sim.handle_command({"cmd": 101})
        assert sim.pump_armed
        sim.stop()
        sim.handle_command({"cmd": 400})  # user disarms the pump between sessions
        sim.handle_command({"cmd": 101})
        assert not sim.pump_armed


# ---------------------------------------------------------------------------
# FR
# ---------------------------------------------------------------------------


class TestFixedRatio:
    BASE = [(472, "duration", 500), (1074, "timeout", 0)]

    @pytest.mark.parametrize("ratio", [1, 3, 7])
    def test_kth_reward_is_the_kN_th_active_press(self, ratio):
        msgs = run("fr", [(201, "ratio", ratio)] + self.BASE)
        active, rewards = starts(presses(msgs, "ACTIVE")), starts(infusions(msgs))
        assert len(rewards) >= 3
        for k, reward_ts in enumerate(rewards):
            if (k + 1) * ratio <= len(active):
                assert reward_ts == active[(k + 1) * ratio - 1], (ratio, k)

    def test_a_bigger_ratio_earns_fewer_rewards_for_the_same_animal(self):
        low = len(infusions(run("fr", [(201, "ratio", 2)] + self.BASE)))
        high = len(infusions(run("fr", [(201, "ratio", 9)] + self.BASE)))
        assert low > 2 * high > 0

    def test_ratio_aliases_converge_last_write_wins(self):
        msgs = run("fr", [(201, "ratio", 9), (1075, "ratio", 2), (1375, "ratio", 4)] + self.BASE)
        active, rewards = starts(presses(msgs, "ACTIVE")), starts(infusions(msgs))
        assert rewards[0] == active[3]

    def test_pump_duration_is_the_infusion_duration(self):
        for duration in (500, 2500):
            msgs = run("fr", [(201, "ratio", 1), (472, "duration", duration), (1074, "timeout", 0)])
            lengths = {m["end_timestamp"] - m["start_timestamp"] for m in infusions(msgs)}
            assert lengths == {duration}

    def test_cue_and_cue2_durations_follow_their_own_settings(self):
        msgs = run("fr", [(201, "ratio", 1), (372, "duration", 700), (382, "duration", 1300), (472, "duration", 500)])
        assert {m["end_timestamp"] - m["start_timestamp"] for m in ev(msgs, "CUE_1")} == {700}
        assert {m["end_timestamp"] - m["start_timestamp"] for m in ev(msgs, "CUE_2")} == {1300}

    def test_chain_devices_start_together_at_press_onset(self):
        """Every ACTIVATE step has offset 0 by default — they are not sequential."""
        msgs = run("fr", [(201, "ratio", 1), (372, "duration", 700), (472, "duration", 500), (1074, "timeout", 0)])
        press = starts(presses(msgs, "ACTIVE"))[0]
        assert starts(ev(msgs, "CUE_1"))[0] == starts(infusions(msgs))[0] == press

    def test_onset_delays_shift_each_device_independently(self):
        msgs = run("fr", [(201, "ratio", 1), (372, "duration", 700), (472, "duration", 500), (1074, "timeout", 0),
                          (377, "delay", 250), (477, "delay", 1500)])
        press = starts(presses(msgs, "ACTIVE"))[0]
        assert starts(ev(msgs, "CUE_1"))[0] == press + 250
        assert starts(infusions(msgs))[0] == press + 1500

    def test_active_pump_selects_pump2_and_its_duration(self):
        msgs = run("fr", [(201, "ratio", 1), (472, "duration", 500), (482, "duration", 1800), (221, "pump2", True),
                          (1074, "timeout", 0)])
        assert not infusions(msgs)
        p2 = ev(msgs, "PUMP_2", "INFUSION")
        assert p2 and {m["end_timestamp"] - m["start_timestamp"] for m in p2} == {1800}

    def test_lever_filter_routes_a_device_to_one_lever(self):
        """Both levers reinforced; the pump answers RH presses only, the cue answers both."""
        msgs = run("fr", [(1381, "x", 0), (201, "ratio", 1), (472, "duration", 500), (372, "duration", 500),
                          (478, "filter", 1), (1074, "timeout", 0)], arm=ARM_FR_NO_LICK + (1301,))
        by_start = {m["start_timestamp"]: m["orientation"] for m in presses(msgs, "ACTIVE")}
        pump_levers = {by_start[t] for t in starts(infusions(msgs))}
        cue_levers = {by_start[t] for t in starts(ev(msgs, "CUE_1"))}
        assert pump_levers == {"RH"} and cue_levers == {"RH", "LH"}

    def test_reinforced_lever_selects_which_presses_count(self):
        msgs = run("fr", [(1381, "x", 0), (1080, "x", 0), (201, "ratio", 1), (472, "duration", 500),
                          (1074, "timeout", 0)], arm=ARM_FR_NO_LICK + (1301,))
        assert {m["orientation"] for m in presses(msgs, "ACTIVE")} == {"LH"}
        assert {m["orientation"] for m in presses(msgs, "INACTIVE")} == {"RH"}
        assert len(infusions(msgs)) == len(presses(msgs, "ACTIVE"))

    def test_laser_contingent_fires_with_the_reward_and_uses_its_duration(self):
        msgs = run("fr", [(201, "ratio", 2), (472, "duration", 500), (672, "duration", 900), (1074, "timeout", 0)],
                   arm=ARM_FR_NO_LICK + (601,))
        laser = ev(msgs, "LASER", "STIM")
        assert starts(laser) == starts(infusions(msgs)) and laser
        assert {m["end_timestamp"] - m["start_timestamp"] for m in laser} == {900}

    def test_laser_rh_only_fires_on_every_active_press_not_on_the_ratio(self):
        msgs = run("fr", [(201, "ratio", 5), (472, "duration", 500), (672, "duration", 400), (684, "x", 0),
                          (1074, "timeout", 0)], arm=ARM_FR_NO_LICK + (601,))
        assert starts(ev(msgs, "LASER", "STIM")) == starts(presses(msgs, "ACTIVE"))
        assert len(infusions(msgs)) == len(presses(msgs, "ACTIVE")) // 5

    def test_laser_independent_cycles_on_for_duration_off_for_duration(self):
        msgs = run("fr", [(682, "x", 0), (672, "duration", 2000)], arm=ARM_FR_NO_LICK + (601,), horizon_ms=20_000)
        assert starts(ev(msgs, "LASER", "STIM")) == [0, 4000, 8000, 12000, 16000, 20000]

    def test_pin_override_reaches_the_event_stream(self):
        # Device::SetPin disarms an armed device, so the pump is armed again after the move.
        msgs = run("fr", [(476, "pin", 30), 401, (201, "ratio", 1), (472, "duration", 500)], arm=(1001, 301))
        assert run.sim._pins["PUMP"] == 30
        assert {m["pin"] for m in infusions(msgs)} == {30}

    def test_moving_a_pin_disarms_the_device(self):
        msgs = run("fr", [(476, "pin", 30), (201, "ratio", 1), (472, "duration", 500)], arm=ARM_FR_NO_LICK)
        assert not infusions(msgs) and ev(msgs, "CUE_1")

    def test_lever_pins_default_to_pins_h(self):
        msgs = run("fr", [(201, "ratio", 1)])
        assert {m["pin"] for m in presses(msgs, "ACTIVE")} == {10}


class TestTimeout:
    def test_default_fr_has_no_timeout(self):
        msgs = run("fr", [(201, "ratio", 2), (472, "duration", 500)])
        assert not presses(msgs, "TIMEOUT")

    @pytest.mark.parametrize("paradigm", ["pr", "vi"])
    def test_pr_vi_boot_with_the_sketch_default_timeout(self, paradigm):
        sim = FirmwareSimulator(queue.Queue(), paradigm=paradigm)
        assert sim.timeout_interval == 20000

    def test_timeout_suppresses_presses_and_slows_the_schedule(self):
        free = run("fr", [(201, "ratio", 2), (472, "duration", 500), (1074, "timeout", 0)])
        locked = run("fr", [(201, "ratio", 2), (472, "duration", 500), (1074, "timeout", 20000)])
        assert presses(locked, "TIMEOUT") and len(infusions(locked)) < len(infusions(free))
        # mode 0: an ACTIVE press opens a window; nothing inside it can be ACTIVE
        window = 20000
        last_active = None
        for p in sorted(presses(locked), key=lambda m: m["start_timestamp"]):
            if p["class"] == "ACTIVE":
                assert last_active is None or p["start_timestamp"] > last_active + window
                last_active = p["start_timestamp"]
            elif p["class"] == "TIMEOUT":
                assert p["start_timestamp"] <= last_active + window

    def test_timeout_mode_1_only_locks_out_after_a_reward(self):
        mode0 = run("fr", [(201, "ratio", 4), (472, "duration", 500), (1074, "timeout", 20000)])
        mode1 = run("fr", [(201, "ratio", 4), (472, "duration", 500), (1074, "timeout", 20000), (1077, "timeout_mode", 1)])
        assert len(presses(mode1, "ACTIVE")) > len(presses(mode0, "ACTIVE"))
        assert len(infusions(mode1)) > len(infusions(mode0))

    def test_a_mid_session_timeout_edit_reaches_the_next_reward(self):
        """SetTimeoutInterval() rewrites the chain's SET_TIMEOUT step, no reconfigure needed.

        The chain's step targets the configured active lever (LH here) while the animal
        only presses RH, so the step — not the pressed lever's own bookkeeping — is the
        only thing that can lock LH.
        """
        q = queue.Queue()
        sim = FirmwareSimulator(q, realtime=False, seed=1)
        for c in _cmds(ARM_FR_NO_LICK) + _cmds(
            [1081, 1381, (201, "ratio", 1), (472, "duration", 500), (1074, "timeout", 0)]
        ):
            sim.handle_command(c)
        sim.start()
        sim.run_until(30_000)
        sim.handle_command({"cmd": 1074, "timeout": 600_000})
        sim.run_until(300_000)
        post_edit = [
            m["start_timestamp"] for m in (json.loads(b) for b in list(q.queue))
            if m.get("event") == "INFUSION" and m["start_timestamp"] > 30_000
        ]
        assert post_edit
        assert sim._timeout._end["LH"] == post_edit[-1] + 600_000

    def test_timeout_applies_only_to_the_reinforced_lever(self):
        msgs = run("fr", [(201, "ratio", 1), (472, "duration", 500), (1074, "timeout", 30000)],
                   arm=ARM_FR_NO_LICK + (1301,))
        assert presses(msgs, "TIMEOUT")
        assert {m["class"] for m in ev(msgs, "LEVER_LH", "PRESS")} == {"INACTIVE"}


# ---------------------------------------------------------------------------
# PR
# ---------------------------------------------------------------------------


class TestProgressiveRatio:
    BASE = [(472, "duration", 500), (1074, "timeout", 0)]

    @staticmethod
    def _thresholds(ratio, step, n):
        return [ratio + i * step for i in range(n)]

    @pytest.mark.parametrize("ratio,step", [(1, 1), (3, 2), (2, 5)])
    def test_reward_k_needs_ratio_plus_k_step_presses(self, ratio, step):
        msgs = run("pr", [(201, "ratio", ratio), (205, "step", step)] + self.BASE, horizon_ms=3_000_000)
        active, rewards = starts(presses(msgs, "ACTIVE")), starts(infusions(msgs))
        assert len(rewards) >= 4
        cumulative = 0
        for k, reward_ts in enumerate(rewards):
            cumulative += ratio + k * step
            if cumulative <= len(active):
                assert reward_ts == active[cumulative - 1], (ratio, step, k)

    def test_step_and_ratio_change_the_outcome(self):
        """The old runner ignored both: ratio 3/step 1 and ratio 20/step 10 were identical."""
        a = run("pr", [(201, "ratio", 3), (205, "step", 1)] + self.BASE, horizon_ms=1_800_000)
        b = run("pr", [(205, "step", 10), (201, "ratio", 20)] + self.BASE, horizon_ms=1_800_000)
        assert len(infusions(a)) > len(infusions(b)) >= 1

    def test_default_step_is_the_sketchs_one(self):
        msgs = run("pr", [(201, "ratio", 2)] + self.BASE, horizon_ms=1_800_000)
        active, rewards = starts(presses(msgs, "ACTIVE")), starts(infusions(msgs))
        assert rewards[0] == active[1] and rewards[1] == active[1 + 3]  # 2 then 3 presses

    def test_ratio_sent_before_session_start_is_lost_unless_the_chain_is_reconfigured(self):
        """Firmware quirk, mirrored on purpose (see findings): Scheduler::SetRatio writes the
        live threshold, Trigger::Reset() puts it back to initialThreshold at StartSession, and
        only ReconfigureChain() (any duration/filter/pump-select command) re-bases initialThreshold."""
        lost = run("pr", [(201, "ratio", 5), (205, "step", 1), (1074, "timeout", 0)], arm=ARM_FR_NO_LICK)
        kept = run("pr", [(201, "ratio", 5), (205, "step", 1), (1074, "timeout", 0), (472, "duration", 500)])
        active_lost, active_kept = starts(presses(lost, "ACTIVE")), starts(presses(kept, "ACTIVE"))
        assert starts(ev(lost, "CUE_1"))[0] == active_lost[0]  # started at 1
        assert starts(ev(kept, "CUE_1"))[0] == active_kept[4]  # started at 5

    def test_progression_restarts_every_session(self):
        sim = FirmwareSimulator(queue.Queue(), paradigm="pr", realtime=False, seed=1)
        for c in _cmds(ARM_FR_NO_LICK) + _cmds([(201, "ratio", 2), (472, "duration", 500), (1074, "timeout", 0)]):
            sim.handle_command(c)
        sim.start()
        sim.run_until(600_000)
        assert sim._triggers[0].threshold > 2
        sim.stop()
        assert sim._triggers[0].threshold == 2


# ---------------------------------------------------------------------------
# VI
# ---------------------------------------------------------------------------


class TestVariableInterval:
    BASE = [(472, "duration", 500), (1074, "timeout", 0)]

    @pytest.mark.parametrize("interval", [4000, 20000, 90000])
    def test_at_most_one_reward_per_interval_and_only_the_collecting_press_is_active(self, interval):
        msgs = run("vi", [(204, "interval", interval)] + self.BASE)
        rewards = starts(infusions(msgs))
        buckets = [t // interval for t in rewards]
        assert len(buckets) == len(set(buckets)), "two rewards in one availability window"
        assert starts(presses(msgs, "ACTIVE")) == rewards
        assert presses(msgs, "INACTIVE"), "presses outside the window are INACTIVE, not ACTIVE"

    def test_interval_sets_the_reward_rate(self):
        short = len(infusions(run("vi", [(204, "interval", 4000)] + self.BASE)))
        long = len(infusions(run("vi", [(204, "interval", 90000)] + self.BASE)))
        assert short > 5 * long >= 5

    def test_no_reward_before_the_windows_random_start(self):
        """Scheduler::StartSession places windowStart uniformly inside the interval; a press
        before it is just a press on the reinforced lever (INACTIVE), however long the animal waits."""
        for seed in (1, 2, 3):
            sim = FirmwareSimulator(queue.Queue(), paradigm="vi", realtime=False, seed=seed)
            for c in _cmds(ARM_FR_NO_LICK) + _cmds([(204, "interval", 200_000)] + self.BASE):
                sim.handle_command(c)
            sim.start()
            window_start = sim._triggers[0].window_start
            sim.run_until(199_000)
            msgs = []
            while not sim._tx.empty():
                msgs.append(json.loads(sim._tx.get()))
            rewards = starts(infusions(msgs))
            assert window_start > 5000
            assert rewards and rewards[0] >= window_start
            assert all(m["start_timestamp"] < window_start for m in presses(msgs, "INACTIVE")[:1])
            assert len(rewards) == 1

    def test_a_press_inside_the_timeout_is_not_collected(self):
        msgs = run("vi", [(204, "interval", 4000), (472, "duration", 500), (1074, "timeout", 12000)])
        assert presses(msgs, "TIMEOUT")
        assert all(
            b - a > 12000 for a, b in zip(starts(infusions(msgs)), starts(infusions(msgs))[1:])
        )


# ---------------------------------------------------------------------------
# Omission
# ---------------------------------------------------------------------------


class TestOmission:
    BASE = [(472, "duration", 500)]

    @pytest.mark.parametrize("interval", [2000, 6000])
    def test_reward_comes_exactly_one_interval_after_the_last_active_press_or_reward(self, interval):
        msgs = run("omission", [(203, "interval", interval)] + self.BASE)
        events = sorted(
            [("press", t) for t in starts(presses(msgs, "ACTIVE"))] + [("reward", t) for t in starts(infusions(msgs))],
            key=lambda e: e[1],
        )
        rewards = [t for kind, t in events if kind == "reward"]
        assert rewards
        for reward_ts in rewards:
            earlier = [t for _, t in events if t < reward_ts]
            assert reward_ts - max(earlier) == interval

    def test_no_reward_before_the_first_active_press(self):
        """configureOmission() zeroes absenceStart, so the timer is off until a press."""
        msgs = run("omission", [(203, "interval", 2000)] + self.BASE)
        assert starts(infusions(msgs))[0] > starts(presses(msgs, "ACTIVE"))[0]

    def test_no_presses_no_reward(self):
        msgs = run("omission", [(203, "interval", 2000)] + self.BASE, arm=(401, 301))  # lever not armed
        assert not presses(msgs) and not infusions(msgs)

    def test_interval_decides_whether_withholding_pays(self):
        quick = len(infusions(run("omission", [(203, "interval", 2000)] + self.BASE)))
        slow = len(infusions(run("omission", [(203, "interval", 40000)] + self.BASE)))
        assert quick > 20 and slow == 0

    def test_inactive_lever_presses_do_not_reset_the_timer(self):
        msgs = run("omission", [(203, "interval", 6000)] + self.BASE, arm=ARM_FR_NO_LICK + (1301,))
        assert presses(msgs, "INACTIVE")
        resets = starts(presses(msgs, "ACTIVE")) + starts(infusions(msgs))
        for reward_ts in starts(infusions(msgs)):
            assert reward_ts - max(t for t in resets if t < reward_ts) == 6000

    def test_omission_never_arms_a_timeout(self):
        msgs = run("omission", [(203, "interval", 3000)] + self.BASE)
        assert not presses(msgs, "TIMEOUT")


# ---------------------------------------------------------------------------
# Pavlovian
# ---------------------------------------------------------------------------

FAST_PAV = [
    (208, "count", 6), (209, "count", 4), (213, "duration", 800), (214, "interval", 300), (215, "duration", 500),
    (216, "iti_mean", 2000), (217, "iti_min", 1500), (218, "iti_max", 3000), (472, "duration", 400),
    (482, "duration", 400),
]


def pav(commands=(), arm=ARM_PAV, horizon_ms=300_000, **kw):
    return run("pavlovian", FAST_PAV + list(commands), arm=arm, horizon_ms=horizon_ms, **kw)


def trials(msgs):
    return ev(msgs, "PAVLOV", "TRIAL_START")


class TestPavlovian:
    def test_trial_counts_and_types_follow_the_configuration(self):
        msgs = pav()
        kinds = [m["trial_type"] for m in trials(msgs)]
        assert kinds.count("CS_PLUS") == 6 and kinds.count("CS_MINUS") == 4
        other = pav([(208, "count", 2), (209, "count", 9)])
        kinds = [m["trial_type"] for m in trials(other)]
        assert kinds.count("CS_PLUS") == 2 and kinds.count("CS_MINUS") == 9

    def test_no_more_than_three_consecutive_trials_of_one_type(self):
        for seed in range(1, 12):
            kinds = [m["trial_type"] for m in trials(pav([(208, "count", 15), (209, "count", 15)], seed=seed))]
            assert len(kinds) == 30
            assert all(len(set(kinds[i:i + 4])) > 1 for i in range(len(kinds) - 3))

    def test_cs_plus_plays_cue_cs_minus_plays_cue2(self):
        msgs = pav()
        tones = {m["start_timestamp"]: m["device"] for m in ev(msgs, ("CUE", "CUE_2"), "TONE")}
        for trial in trials(msgs):
            expected = "CUE" if trial["trial_type"] == "CS_PLUS" else "CUE_2"
            assert tones[trial["timestamp"]] == expected

    def test_counterbalance_swaps_which_cue_is_cs_plus(self):
        """pavlovian.ino reads the key "counterbalance"; the host's CommandSpec sends "enabled"
        (a firmware/host mismatch — reported in the findings), so only the former takes effect."""
        swapped = pav([{"cmd": 212, "counterbalance": True}])
        tones = {m["start_timestamp"]: m["device"] for m in ev(swapped, ("CUE", "CUE_2"), "TONE")}
        for trial in trials(swapped):
            assert tones[trial["timestamp"]] == ("CUE_2" if trial["trial_type"] == "CS_PLUS" else "CUE")
        as_the_host_sends_it = pav([{"cmd": 212, "enabled": True}])
        assert run.sim.pav_counterbalance is False
        assert as_the_host_sends_it

    @pytest.mark.parametrize("plus,minus", [(100, 0), (0, 100), (50, 50)])
    def test_reward_probability_per_cs(self, plus, minus):
        msgs = pav([(206, "probability", plus), (207, "probability", minus), (208, "count", 40), (209, "count", 40)],
                   horizon_ms=1_500_000)
        by_type = {}
        for t in trials(msgs):
            by_type.setdefault(t["trial_type"], []).append(t["reward_scheduled"])
        assert len(by_type["CS_PLUS"]) == 40 == len(by_type["CS_MINUS"])
        for kind, prob in (("CS_PLUS", plus), ("CS_MINUS", minus)):
            rate = sum(by_type[kind]) / 40
            if prob in (0, 100):
                assert rate == prob / 100
            else:
                assert 0.25 < rate < 0.75
        delivered = len(ev(msgs, "PAVLOV", "REWARD_DELIVERED"))
        omitted = len(ev(msgs, "PAVLOV", "REWARD_OMITTED"))
        assert delivered + omitted == 80 and delivered == sum(sum(v) for v in by_type.values())

    def test_cs_plus_reward_uses_pump_cs_minus_reward_uses_pump2(self):
        msgs = pav([(206, "probability", 100), (207, "probability", 100)])
        assert len(ev(msgs, "PUMP", "INFUSION")) == 6 and len(ev(msgs, "PUMP_2", "INFUSION")) == 4

    def test_phase_timing_follows_cue_trace_consumption_and_iti(self):
        msgs = pav([(206, "probability", 100)])
        trial_starts = trials(msgs)
        for t in trial_starts:
            n = t["trial"]
            trace = next(m for m in ev(msgs, "PAVLOV", "TRACE_START") if m["trial"] == n)
            reward = next(m for m in ev(msgs, "PAVLOV", "REWARD_DELIVERED") if m["trial"] == n) \
                if t["reward_scheduled"] else next(m for m in ev(msgs, "PAVLOV", "REWARD_OMITTED") if m["trial"] == n)
            assert trace["timestamp"] == t["timestamp"] + 800
            assert reward["timestamp"] == trace["timestamp"] + 300
        for prev, nxt in zip(trial_starts, trial_starts[1:]):
            gap = nxt["timestamp"] - (prev["timestamp"] + 800 + 300 + 500)
            assert gap == nxt["iti_ms"] and 1500 <= gap <= 3000

    def test_iti_bounds_are_honored_and_a_degenerate_range_is_exact(self):
        msgs = pav([(217, "iti_min", 4321), (218, "iti_max", 4321)])
        assert {t["iti_ms"] for t in trials(msgs)} == {4321}
        wide = pav([(217, "iti_min", 1000), (218, "iti_max", 20000), (216, "iti_mean", 5000)], horizon_ms=1_000_000)
        itis = [t["iti_ms"] for t in trials(wide)]
        assert min(itis) >= 1000 and max(itis) <= 20000 and len(set(itis)) > 5

    def test_session_completes_once_and_the_end_event_carries_the_completion_time(self):
        msgs = pav(stop=True)
        done = ev(msgs, "PAVLOV", "ALL_TRIALS_COMPLETE")
        assert len(done) == 1
        last_reward = max(m["timestamp"] for m in ev(msgs, "PAVLOV", ("REWARD_DELIVERED", "REWARD_OMITTED")))
        assert done[0]["timestamp"] == last_reward + 500
        assert ev(msgs, "CONTROLLER", "END")[0]["timestamp"] == done[0]["timestamp"]

    def test_a_disarmed_cue_drops_its_trials(self):
        """PavlovianScheduler::StartSession gates each trial count on its cue's armed state."""
        msgs = pav(arm=(1001, 1301, 301, 401, 411))  # cue2 (CS-) not armed
        kinds = [m["trial_type"] for m in trials(msgs)]
        assert kinds == ["CS_PLUS"] * 6
        assert not ev(msgs, "CUE_2")

    def test_counts_cannot_change_mid_session(self):
        q = queue.Queue()
        sim = FirmwareSimulator(q, paradigm="pavlovian", realtime=False, seed=1)
        for c in _cmds(ARM_PAV) + _cmds(FAST_PAV):
            sim.handle_command(c)
        sim.start()
        while not q.empty():
            q.get()
        sim.handle_command({"cmd": 208, "count": 99})
        err = json.loads(q.get())
        assert err["level"] == "006" and "CS+ count" in err["desc"]
        assert sim.pav_cs_plus_count == 6

    @pytest.mark.parametrize("phase,cmd", [("CUE", 695), ("REWARD", 694)])
    def test_laser_phase_and_trial_filter(self, phase, cmd):
        msgs = pav([cmd, 691, (672, "duration", 200), (206, "probability", 100)], arm=ARM_PAV + (601,))
        laser = ev(msgs, "LASER", "STIMULATION")
        assert len(laser) == 6, "CS+ trials only"
        anchors = {t["trial"]: t["timestamp"] for t in trials(msgs)}
        for m, t in zip(laser, [t for t in trials(msgs) if t["trial_type"] == "CS_PLUS"]):
            offset = m["start_timestamp"] - anchors[t["trial"]]
            assert offset == (0 if phase == "CUE" else 800 + 300)

    def test_presses_are_logged_as_switch_lever_and_never_affect_trials(self):
        a = pav()
        b = pav(arm=(1301, 301, 311, 401, 411))  # RH lever disarmed: fewer presses, same trials
        assert ev(a, "SWITCH_LEVER", "PRESS") and {m["class"] for m in ev(a, "SWITCH_LEVER")} == {"ACTIVE"}
        assert [t["trial_type"] for t in trials(a)] == [t["trial_type"] for t in trials(b)]

    def test_drinking_follows_delivered_rewards_only(self):
        none = pav([(206, "probability", 0)])
        all_ = pav([(206, "probability", 100)])
        assert not ev(none, "LICK_CIRCUIT") and ev(all_, "LICK_CIRCUIT")


# ---------------------------------------------------------------------------
# Realtime worker, pause, and kernel-side limits
# ---------------------------------------------------------------------------


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestRealtimeWorker:
    def test_events_flow_on_the_worker_thread_and_stop_is_prompt(self):
        q = queue.Queue()
        sim = FirmwareSimulator(q, seed=1, speed=2000)
        for c in _cmds(ARM_FR_NO_LICK) + _cmds([(201, "ratio", 1), (472, "duration", 500)]):
            sim.handle_command(c)
        sim.start()
        try:
            assert _wait(lambda: q.qsize() > 20)
        finally:
            began = time.monotonic()
            sim.stop()
        assert time.monotonic() - began < 1.0

    def test_pause_freezes_the_session_clock(self):
        q = queue.Queue()
        sim = FirmwareSimulator(q, seed=1, speed=2000)
        for c in _cmds(ARM_FR_NO_LICK) + _cmds([(201, "ratio", 1), (472, "duration", 500)]):
            sim.handle_command(c)
        sim.start()
        assert _wait(lambda: q.qsize() > 10)
        sim.handle_command({"cmd": 105, "paused": True})
        time.sleep(0.15)
        frozen = q.qsize()
        time.sleep(0.3)
        assert q.qsize() == frozen
        sim.handle_command({"cmd": 105, "paused": False})
        assert _wait(lambda: q.qsize() > frozen)
        sim.stop()


@pytest.fixture
def fast_sim_env(monkeypatch, tmp_path):
    monkeypatch.setenv("REACHER_SIM_SPEED", "400")
    monkeypatch.setenv("REACHER_SIM_SEED", "3")


def _kernel(paradigm):
    instance = REACHER(session_id=f"limit-{paradigm}")
    instance.set_COM_port("SIMULATOR", paradigm)
    instance.open_serial()
    return instance


class TestKernelLimitsEndSimulatedRuns:
    def test_infusion_limit_stops_a_simulated_fr_session(self, fast_sim_env):
        instance = _kernel("fr")
        try:
            for code, value in ((1001, None), (301, None), (401, None), (201, 1), (472, 500), (1074, 0)):
                instance.send_command(code, value)
            instance.set_limit_type("Infusion")
            instance.set_infusion_limit(3)
            instance.set_stop_delay(0)
            instance.start_program()
            assert _wait(lambda: not instance.program_running, timeout=20)
            assert instance.get_total_infusion_count() >= 3
        finally:
            if instance.program_running:
                instance.stop_program()
            if instance.ser.is_open:
                instance.close_serial()

    def test_time_limit_stops_a_simulated_session_and_the_end_event_carries_session_time(self, fast_sim_env):
        instance = _kernel("vi")
        try:
            for code, value in ((1001, None), (301, None), (401, None), (204, 4000), (472, 500), (1074, 0)):
                instance.send_command(code, value)
            instance.set_limit_type("Time")
            instance.set_time_limit(1)
            instance.start_program()
            assert _wait(lambda: not instance.program_running, timeout=20)
            def ends():  # stop_program() clears program_running before the END line lands
                return [e for e in instance.get_behavior_data() if e["device"] == "CONTROLLER" and e["event"] == "END"]

            assert _wait(lambda: bool(ends()), timeout=10)
            end = ends()
            # 1 s of wall time at 400x is ~400 s of session time
            assert end and 300_000 < end[0]["start_timestamp"] < 900_000, end
            assert [e for e in instance.get_behavior_data() if e["event"] == "INFUSION"]
        finally:
            if instance.program_running:
                instance.stop_program()
            if instance.ser.is_open:
                instance.close_serial()

    def test_pavlovian_trial_completion_stops_the_session(self, fast_sim_env):
        instance = _kernel("pavlovian")
        try:
            for code, value in (
                (1001, None), (1301, None), (301, None), (311, None), (401, None),
                (208, 2), (209, 1), (213, 300), (214, 100), (215, 100), (217, 200), (218, 300), (216, 250),
            ):
                instance.send_command(code, value)
            instance.set_limit_type("Trials")
            instance.start_program()
            assert _wait(lambda: not instance.program_running, timeout=20)
            assert instance.get_total_trial_count() == 3
        finally:
            if instance.program_running:
                instance.stop_program()
            if instance.ser.is_open:
                instance.close_serial()
