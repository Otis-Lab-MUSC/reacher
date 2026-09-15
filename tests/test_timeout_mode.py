"""Configurable lever-timeout mode (Cmd 1077 / 1377).

Three layers, one feature:

* the HTTP router's value/paradigm/state gates for the new payload key,
* the simulator's command handling and its level-000 session-start dump
  reaching ``firmware_information``,
* the behavioral golden pair — the point of the whole change. Mode 0 must
  reproduce today's behavior (every ACTIVE press arms the timeout, so presses
  inside it classify TIMEOUT and never advance the ratio) and mode 1 must not.
  A test that passes in both modes is not testing this feature.

The firmware itself has no host-side test framework; ``TimeoutWindow`` is the
host mirror of ``Scheduler::OnInputEvent`` + ``SwitchLever::InTimeout`` and is
what the behavioral assertions below run against.
"""

import json
import queue
import time

import pytest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.commands import CommandCode, build_command_payload
from reacher.kernel.reacher import REACHER
from reacher.kernel.simulator import (
    TIMEOUT_MODE_EVERY_PRESS,
    TIMEOUT_MODE_REWARD_ONLY,
    FirmwareSimulator,
    TimeoutWindow,
)

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}

RH = int(CommandCode.LEVER_RH_SET_TIMEOUT_MODE)
LH = int(CommandCode.LEVER_LH_SET_TIMEOUT_MODE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient over a mocked REACHER — this is an HTTP-contract check only."""
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        instance = Mock()
        instance.program_running = False
        instance.ser = Mock()
        instance.ser.is_open = False
        instance.get_hardware_settings.return_value = []
        instance.get_program_running.return_value = False
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_detected_paradigm.return_value = None
        instance.emit_failure_count = 0
        MockReacher.return_value = instance
        app = create_app()
        with TestClient(app) as c:
            c.mock_instance = instance
            yield c


def _session(client, paradigm="fr", state="connected", port="COM1"):
    resp = client.post("/api/sessions", json={"port": port}, headers=AUTH_HEADER)
    sid = resp.json()["session_id"]
    sm = client.app.state.session_manager
    sm.set_paradigm(sid, paradigm)
    sm.set_state(sid, state)
    return sid


@pytest.fixture
def sim():
    s = FirmwareSimulator(queue.Queue())
    yield s
    s.stop()


def _drain(sim):
    out = []
    while not sim._tx.empty():
        out.append(json.loads(sim._tx.get_nowait()))
    return out


# ---------------------------------------------------------------------------
# Router — value range, paradigm filter, armed freeze
# ---------------------------------------------------------------------------


class TestRouterContract:
    @pytest.mark.parametrize("code", [RH, LH])
    @pytest.mark.parametrize("value", [0, 1])
    def test_accepts_both_modes(self, client, code, value):
        sid = _session(client)
        resp = client.post(
            f"/api/hardware/{sid}/command",
            json={"code": code, "value": value},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200, resp.text
        client.mock_instance.send_command.assert_called_with(code, value)

    @pytest.mark.parametrize("code", [RH, LH])
    @pytest.mark.parametrize("value", [2, -1, 255])
    def test_rejects_out_of_range(self, client, code, value):
        """_VALUE_RANGES golden-negative: the payload is int precisely so that
        an out-of-range mode is a 400 rather than being coerced to true."""
        sid = _session(client)
        resp = client.post(
            f"/api/hardware/{sid}/command",
            json={"code": code, "value": value},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400, resp.text
        assert "timeout_mode" in resp.json()["detail"]

    @pytest.mark.parametrize("paradigm", ["omission", "omission_lite", "pavlovian"])
    @pytest.mark.parametrize("code", [RH, LH])
    def test_rejected_for_paradigms_without_a_timeout(self, client, paradigm, code):
        sid = _session(client, paradigm=paradigm)
        resp = client.post(
            f"/api/hardware/{sid}/command",
            json={"code": code, "value": 1},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400, resp.text

    @pytest.mark.parametrize("code", [RH, LH])
    def test_frozen_while_armed(self, client, code):
        """An armed session is frozen: a TTL edge landing mid-edit would start
        on a half-applied config (hardware.py's armed gate)."""
        sid = _session(client, state="armed")
        resp = client.post(
            f"/api/hardware/{sid}/command",
            json={"code": code, "value": 1},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Payload + simulator command handling
# ---------------------------------------------------------------------------


class TestSimulatorCommandHandling:
    def test_payload_shape(self):
        assert build_command_payload(RH, 1) == {"cmd": RH, "timeout_mode": 1}

    @pytest.mark.parametrize("code", [RH, LH])
    def test_either_code_writes_the_one_scheduler_wide_flag(self, sim, code):
        assert sim.lever_timeout_mode == TIMEOUT_MODE_EVERY_PRESS
        sim.handle_command({"cmd": code, "timeout_mode": 1})
        assert sim.lever_timeout_mode == TIMEOUT_MODE_REWARD_ONLY
        sim.handle_command({"cmd": code, "timeout_mode": 0})
        assert sim.lever_timeout_mode == TIMEOUT_MODE_EVERY_PRESS

    def test_last_write_wins_across_levers(self, sim):
        """R4: 1077 and 1377 are not independent, exactly like 1074/1374."""
        sim.handle_command({"cmd": RH, "timeout_mode": 1})
        sim.handle_command({"cmd": LH, "timeout_mode": 0})
        assert sim.lever_timeout_mode == TIMEOUT_MODE_EVERY_PRESS

    def test_clamps_above_one(self, sim):
        """Mirrors Scheduler::SetTimeoutMode; the router rejects it first."""
        sim.handle_command({"cmd": RH, "timeout_mode": 7})
        assert sim.lever_timeout_mode == TIMEOUT_MODE_REWARD_ONLY

    def test_mode_reaches_the_session_start_config_dump(self, sim):
        sim.handle_command({"cmd": RH, "timeout_mode": 1})
        sim.handle_command({"cmd": 1074, "timeout": 5000})
        _drain(sim)
        sim.start()
        try:
            config = [
                m for m in _drain(sim)
                if m.get("level") == "000" and m.get("device") == "CONTROLLER"
            ]
        finally:
            sim.stop()
        assert config, "no level-000 CONTROLLER config line at session start"
        assert config[0]["timeout_mode"] == 1
        assert config[0]["timeout"] == 5000

    @pytest.mark.parametrize("paradigm,schedule", [("omission", "OMISSION"), ("pavlovian", "PAVLOVIAN")])
    def test_no_config_dump_where_the_sketch_has_no_timeout(self, sim, paradigm, schedule):
        """omission/pavlovian sketches carry no timeout_mode field, so the
        simulator must not invent one."""
        sim.handle_command({"cmd": 202, "paradigm": paradigm})
        assert sim.schedule == schedule
        _drain(sim)
        sim.start()
        try:
            config = [
                m for m in _drain(sim)
                if m.get("level") == "000" and m.get("device") == "CONTROLLER"
            ]
        finally:
            sim.stop()
        assert config == []


class TestFirmwareInformation:
    """The session-start dump has to land in firmware_information, which is
    what the UI echo and the session summary both read."""

    def test_mode_lands_in_firmware_information_and_the_summary(self):
        instance = REACHER(session_id="timeout-mode")
        instance.set_COM_port("SIMULATOR")
        instance.open_serial()
        try:
            instance.send_command(RH, 1)
            instance.start_program()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if "timeout_mode" in instance.get_firmware_information():
                    break
                time.sleep(0.02)
            firmware = instance.get_firmware_information()
            assert firmware.get("timeout_mode") == 1, firmware
            summary = instance.get_session_summary()
            assert summary["final_state"]["firmware"].get("timeout_mode") == 1
        finally:
            if instance.program_running:
                instance.stop_program()
            if instance.ser.is_open:
                instance.close_serial()


# ---------------------------------------------------------------------------
# Behavioral golden pair
# ---------------------------------------------------------------------------


class TestTimeoutWindowModel:
    """Unit coverage of the host mirror of Scheduler::OnInputEvent."""

    def test_zero_interval_arms_nothing(self):
        w = TimeoutWindow(interval=0, mode=TIMEOUT_MODE_EVERY_PRESS)
        w.note_active_press("RH", 1000, rewarded=True)
        assert w.classify("RH", 1000) == "ACTIVE"

    def test_in_timeout_is_inclusive_of_the_end(self):
        """SwitchLever::InTimeout is ts <= timeoutEnd, not <."""
        w = TimeoutWindow(interval=100)
        w.note_active_press("RH", 0, rewarded=False)
        assert w.classify("RH", 100) == "TIMEOUT"
        assert w.classify("RH", 101) == "ACTIVE"

    def test_mode_0_arms_on_an_unrewarded_press(self):
        w = TimeoutWindow(interval=1000, mode=TIMEOUT_MODE_EVERY_PRESS)
        w.note_active_press("RH", 0, rewarded=False)
        assert w.classify("RH", 500) == "TIMEOUT"

    def test_mode_1_does_not_arm_on_an_unrewarded_press(self):
        w = TimeoutWindow(interval=1000, mode=TIMEOUT_MODE_REWARD_ONLY)
        w.note_active_press("RH", 0, rewarded=False)
        assert w.classify("RH", 500) == "ACTIVE"

    def test_mode_1_still_arms_on_reward(self):
        w = TimeoutWindow(interval=1000, mode=TIMEOUT_MODE_REWARD_ONLY)
        w.note_active_press("RH", 0, rewarded=True)
        assert w.classify("RH", 500) == "TIMEOUT"

    def test_window_is_per_lever(self):
        w = TimeoutWindow(interval=1000)
        w.note_active_press("RH", 0, rewarded=False)
        assert w.classify("LH", 500) == "ACTIVE"

    def test_reset_clears_both_levers(self):
        w = TimeoutWindow(interval=1000)
        w.note_active_press("RH", 0, rewarded=False)
        w.reset()
        assert w.classify("RH", 500) == "ACTIVE"


def _run_fr_presses(mode, ratio, interval, press_times):
    """Replay a fixed press schedule through the window model.

    Returns (classes, rewards) — the class logged for each press and how many
    reward chains fired. Deterministic: the simulator's own runner uses random
    inter-press delays, so the golden pair drives the model directly rather
    than racing a thread.
    """
    w = TimeoutWindow(interval=interval, mode=mode)
    classes = []
    rewards = 0
    press_count = 0
    for ts in press_times:
        cls = w.classify("RH", ts)
        classes.append(cls)
        if cls != "ACTIVE":
            # Firmware logs it but never reaches Trigger::OnInputEvent.
            continue
        press_count += 1
        rewarded = press_count >= ratio
        w.note_active_press("RH", ts, rewarded)
        if rewarded:
            press_count = 0
            rewards += 1
    return classes, rewards


class TestBehavioralGoldenPair:
    """FR ratio 2 with a 20 s timeout, pressed every 5 s for a minute."""

    PRESSES = list(range(0, 60_001, 5_000))  # 13 presses, 5 s apart
    RATIO = 2
    INTERVAL = 20_000

    def test_mode_0_locks_the_lever_out_between_presses(self):
        classes, rewards = _run_fr_presses(
            TIMEOUT_MODE_EVERY_PRESS, self.RATIO, self.INTERVAL, self.PRESSES,
        )
        assert "TIMEOUT" in classes, classes
        # Every press must be spaced by > 20 s to count, so 13 presses at 5 s
        # yield only ceil(60/25) ACTIVE presses and at most one reward.
        assert classes.count("ACTIVE") == 3, classes
        assert rewards == 1, classes

    def test_mode_1_counts_every_press_until_a_reward(self):
        classes, rewards = _run_fr_presses(
            TIMEOUT_MODE_REWARD_ONLY, self.RATIO, self.INTERVAL, self.PRESSES,
        )
        assert classes.count("ACTIVE") > 3, classes
        assert rewards > 1, classes

    def test_the_two_modes_differ(self):
        """The assertion the whole change exists for."""
        mode0 = _run_fr_presses(
            TIMEOUT_MODE_EVERY_PRESS, self.RATIO, self.INTERVAL, self.PRESSES,
        )
        mode1 = _run_fr_presses(
            TIMEOUT_MODE_REWARD_ONLY, self.RATIO, self.INTERVAL, self.PRESSES,
        )
        assert mode0 != mode1

    def test_mode_1_still_honors_the_post_reward_timeout(self):
        """Mode 1 is not "no timeout" — the lockout after a reward stays."""
        classes, _ = _run_fr_presses(
            TIMEOUT_MODE_REWARD_ONLY, self.RATIO, self.INTERVAL, self.PRESSES,
        )
        # press 0 ACTIVE, press 1 (5 s) ACTIVE -> reward, window to 25 s:
        # presses at 10/15/20/25 s are TIMEOUT.
        assert classes[:7] == [
            "ACTIVE", "ACTIVE", "TIMEOUT", "TIMEOUT", "TIMEOUT", "TIMEOUT", "ACTIVE",
        ], classes

    def test_zero_timeout_is_immune_in_both_modes(self):
        """The shipped FR default (fr/Config.h) is timeout = 0, which has never
        been affected by this at all."""
        mode0 = _run_fr_presses(TIMEOUT_MODE_EVERY_PRESS, self.RATIO, 0, self.PRESSES)
        mode1 = _run_fr_presses(TIMEOUT_MODE_REWARD_ONLY, self.RATIO, 0, self.PRESSES)
        assert mode0 == mode1
        assert set(mode0[0]) == {"ACTIVE"}


def _fr_stream(mode, presses=20):
    """Drive the simulator's real FR runner on a deterministic clock.

    Randomness is pinned (5 s between presses, no stray inactive presses or
    licks, fixed press duration) and ``_stop_event.wait`` is replaced with a
    counter, so the emitted stream is a function of the timeout mode alone.
    """
    sim = FirmwareSimulator(queue.Queue())
    sim.handle_command({"cmd": 1074, "timeout": 20_000})
    sim.handle_command({"cmd": 201, "ratio": 2})
    sim.handle_command({"cmd": RH, "timeout_mode": mode})
    sim._running = True
    sim._clock = 0
    sim._timeout.reset()
    sim._sync_timeout_config()
    _drain(sim)

    remaining = [presses]

    def fake_wait(delay):
        remaining[0] -= 1
        if remaining[0] < 0:
            sim._running = False
            return True
        return False

    sim._stop_event.wait = fake_wait
    with patch("reacher.kernel.simulator.random.uniform", return_value=5.0), \
         patch("reacher.kernel.simulator.random.random", return_value=0.9), \
         patch("reacher.kernel.simulator.random.randint", return_value=100):
        sim._run_fr()

    msgs = _drain(sim)
    classes = [m["class"] for m in msgs if m.get("device") == "SWITCH_LEVER"]
    infusions = [m for m in msgs if m.get("event") == "INFUSION"]
    return classes, len(infusions)


class TestSimulatorStreamsDiffer:
    """The golden pair again, this time through the simulator's own FR runner
    rather than the window model, so the wiring is covered too."""

    def test_mode_0_produces_timeout_class_presses(self):
        classes, _ = _fr_stream(TIMEOUT_MODE_EVERY_PRESS)
        assert "TIMEOUT" in classes, classes

    def test_mode_1_earns_more_rewards_from_the_same_presses(self):
        classes0, rewards0 = _fr_stream(TIMEOUT_MODE_EVERY_PRESS)
        classes1, rewards1 = _fr_stream(TIMEOUT_MODE_REWARD_ONLY)
        assert classes0 != classes1
        assert rewards1 > rewards0, (rewards0, rewards1)
