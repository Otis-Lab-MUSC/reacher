"""External TTL start-trigger — simulator command handling, kernel-level
stress/churn, and the races a physical edge can land in.

tests/test_external_trigger.py already covers pin policy, paradigm gating,
arm/disarm semantics, ``_begin_external_session`` bookkeeping, state-event
mirroring, the HTTP routes, and release-on-teardown against a *mocked* port.
This file does not repeat any of that. It covers two things that file
cannot: the simulator's own EXT_TRIGGER_* handling (it previously had none —
see the simulator.py diff this file ships alongside), and behavior that only
shows up with real threads racing a real (simulated) wire — churn, back-to-
back triggered runs, and the executor-vs-queue-thread interleaving named in
program.py's F-001 comment.
"""

import json
import queue
import threading
import time

import pytest

from reacher.kernel.commands import CommandCode
from reacher.kernel.reacher import REACHER
from reacher.kernel.simulator import FirmwareSimulator
from reacher.session_manager import SessionManager

ARM = int(CommandCode.EXT_TRIGGER_ARM)
DISARM = int(CommandCode.EXT_TRIGGER_DISARM)
SET_PIN = int(CommandCode.EXT_TRIGGER_SET_PIN)


def _drain(sim: FirmwareSimulator) -> list:
    """Pop every queued outbound line off a bare FirmwareSimulator as dicts."""
    out = []
    while not sim._tx.empty():
        out.append(json.loads(sim._tx.get_nowait()))
    return out


def _sim(instance: REACHER) -> FirmwareSimulator:
    return instance.ser._simulator


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sim():
    """A bare FirmwareSimulator, no REACHER/threads — for isolating the
    simulator's own command handling from the kernel's threading model."""
    s = FirmwareSimulator(queue.Queue())
    yield s
    s.stop()


@pytest.fixture
def sim_reacher():
    """A real REACHER instance with real serial/queue threads against
    SimulatedSerial. Mirrors TestSimulatorControllerEnd.sim_reacher in
    tests/core/test_reacher.py — that fixture proved the pattern works for
    the CONTROLLER END path; this file is the external-trigger analogue."""
    instance = REACHER(session_id="ext-trigger-stress")
    instance.set_COM_port("SIMULATOR")
    instance.open_serial()
    yield instance
    if instance.program_running:
        instance.stop_program()
    if instance.ser.is_open:
        instance.close_serial()


# ---------------------------------------------------------------------------
# Task 1 — simulator command handling (net-new: simulator.py had zero
# EXT_TRIGGER_* handling before this change)
# ---------------------------------------------------------------------------


class TestSimulatorCommandHandling:
    def test_arm_emits_armed_state_at_the_default_pin(self, sim):
        sim.handle_command({"cmd": ARM})
        assert sim.ext_trigger_armed is True
        assert _drain(sim) == [
            {"level": "001", "device": "EXT_TRIGGER", "event": "ARMED", "pin": 18},
        ]

    def test_disarm_emits_disarmed_state(self, sim):
        sim.handle_command({"cmd": ARM})
        _drain(sim)
        sim.handle_command({"cmd": DISARM})
        assert sim.ext_trigger_armed is False
        assert _drain(sim) == [
            {"level": "001", "device": "EXT_TRIGGER", "event": "DISARMED", "pin": 18},
        ]

    @pytest.mark.parametrize("pin", [18, 19, 20, 21])
    def test_valid_pins_accepted(self, sim, pin):
        sim.handle_command({"cmd": SET_PIN, "pin": pin})
        assert sim.ext_trigger_pin == pin
        assert _drain(sim) == [
            {"level": "000", "device": "EXT_TRIGGER", "param": "pin", "value": pin},
        ]

    @pytest.mark.parametrize("pin", [2, 3, 0, 1, 17, 22, 53, -1])
    def test_invalid_pins_rejected_matching_firmware_exactly(self, sim, pin):
        """Mirrors ExternalTrigger::SetPin's rejection message byte for byte."""
        sim.handle_command({"cmd": SET_PIN, "pin": pin})
        assert sim.ext_trigger_pin == 18  # unchanged
        events = _drain(sim)
        assert events == [{
            "level": "006", "device": "EXT_TRIGGER", "error_code": "EXT_PIN_INVALID",
            "desc": "External trigger pin must be 18, 19, 20 or 21", "got": pin,
        }]

    def test_set_pin_preserves_armed_state(self, sim):
        """Mirrors ExternalTrigger::SetPin: re-attaches on the new pin if it
        was armed rather than forcing a disarm."""
        sim.handle_command({"cmd": ARM})
        sim.handle_command({"cmd": SET_PIN, "pin": 20})
        assert sim.ext_trigger_armed is True
        assert sim.ext_trigger_pin == 20

    def test_ext_trigger_appears_in_the_identify_dump(self, sim):
        """The frontend's armed indicator is a read-only mirror synced from
        the level-000 config dump (REACHER.update_firmware_information)."""
        sim.handle_command({"cmd": SET_PIN, "pin": 19})
        sim.handle_command({"cmd": ARM})
        _drain(sim)
        sim.handle_command({"cmd": 102})  # IDENTIFY
        events = _drain(sim)
        entry = next(e for e in events if e.get("device") == "EXT_TRIGGER")
        assert entry == {"level": "000", "device": "EXT_TRIGGER", "armed": True, "pin": 19}


class TestLiteParadigmHonesty:
    """Task 2: the simulator must not implement what real _lite firmware
    doesn't compile in. The router's paradigm gate (program.py) protects the
    UI path but does not exist at the kernel/simulator level — see
    TestKernelBypassesRouterGate below."""

    @pytest.mark.parametrize("paradigm", ["fr_lite", "pr_lite", "vi_lite", "omission_lite"])
    def test_arm_is_silently_ignored(self, sim, paradigm):
        sim.handle_command({"cmd": 202, "paradigm": paradigm})
        _drain(sim)
        sim.handle_command({"cmd": ARM})
        assert sim.ext_trigger_armed is False
        assert _drain(sim) == []

    def test_set_pin_is_silently_ignored_even_for_a_bad_pin(self, sim):
        """Not even the EXT_PIN_INVALID error — the command is unhandled,
        full stop, matching a lite sketch that never compiled the case in."""
        sim.handle_command({"cmd": 202, "paradigm": "vi_lite"})
        _drain(sim)
        sim.handle_command({"cmd": SET_PIN, "pin": 999})
        assert _drain(sim) == []

    def test_excluded_from_the_identify_dump(self, sim):
        sim.handle_command({"cmd": 202, "paradigm": "omission_lite"})
        sim.handle_command({"cmd": 102})
        events = _drain(sim)
        assert not any(e.get("device") == "EXT_TRIGGER" for e in events)

    def test_non_lite_paradigms_unaffected(self, sim):
        for paradigm in ("fr", "pr", "vi", "omission", "pavlovian"):
            sim.handle_command({"cmd": 202, "paradigm": paradigm})
            sim.handle_command({"cmd": ARM})
            assert sim.ext_trigger_armed is True, paradigm
            sim.handle_command({"cmd": DISARM})


# ---------------------------------------------------------------------------
# fire_external_trigger() — the injectable TTL edge
# ---------------------------------------------------------------------------


class TestFireExternalTrigger:
    def test_fire_while_unarmed_is_a_noop(self, sim):
        assert sim.fire_external_trigger() is False
        assert _drain(sim) == []

    def test_fire_while_armed_starts_the_session_in_firmware_order(self, sim):
        """DISARMED lands before START — Consume() self-disarms (and logs)
        before StartSession(true) prints the START line (fr.ino:152-159)."""
        sim.handle_command({"cmd": ARM})
        _drain(sim)
        assert sim.fire_external_trigger() is True
        assert sim._running is True
        events = _drain(sim)
        assert events[0] == {"level": "001", "device": "EXT_TRIGGER", "event": "DISARMED", "pin": 18}
        assert events[1] == {
            "level": "007", "device": "CONTROLLER", "event": "START",
            "timestamp": 0, "source": "external",
        }

    def test_fire_is_one_shot(self, sim):
        sim.handle_command({"cmd": ARM})
        _drain(sim)
        assert sim.fire_external_trigger() is True
        assert sim.fire_external_trigger() is False  # second edge: nothing

    def test_arming_never_fires_immediately(self, sim):
        """Mirrors ArmToggle dropping any latched `fired` on arm."""
        sim.handle_command({"cmd": ARM})
        events = _drain(sim)
        assert events == [{"level": "001", "device": "EXT_TRIGGER", "event": "ARMED", "pin": 18}]
        assert sim._running is False

    def test_re_arming_after_a_fire_does_not_replay_it(self, sim):
        sim.handle_command({"cmd": ARM})
        _drain(sim)
        sim.fire_external_trigger()
        sim.stop()
        _drain(sim)
        sim.handle_command({"cmd": ARM})
        events = _drain(sim)
        assert events == [{"level": "001", "device": "EXT_TRIGGER", "event": "ARMED", "pin": 18}]
        assert sim._running is False

    def test_fire_while_already_running_is_a_noop(self, sim):
        """labrynth-6e's reachability question, answered at the simulator
        level: mirrors firmware's `!scheduler.IsSessionActive()` guard
        (fr.ino:155). The host cannot normally reach armed-while-running (see
        TestReachabilityOfArmedWhileRunning), so the state is forced here to
        confirm the guard itself is correct if it ever were reached."""
        sim.start()
        sim.ext_trigger_armed = True
        assert sim.fire_external_trigger() is False
        sim.stop()


# ---------------------------------------------------------------------------
# Task 3 — end-to-end through the simulator, with real threads
# ---------------------------------------------------------------------------


class TestEndToEndThroughSimulator:
    def test_arm_edge_running_end_to_end(self, sim_reacher):
        sim_reacher.arm_external_trigger()
        assert sim_reacher.program_running is False
        assert _sim(sim_reacher).fire_external_trigger() is True
        # Gate on the START event, not on program_running: reacher.py:905-911
        # sets program_running inside the match block so the session_state
        # broadcast precedes the event that caused it, and the append to
        # behavior_data happens after. Waiting on the flag therefore returns
        # inside that window and the event may not have landed yet.
        assert _wait_until(
            lambda: any(
                e.get("device") == "CONTROLLER" and e.get("event") == "START"
                for e in sim_reacher.behavior_data
            )
        )
        assert sim_reacher.program_running is True
        entry = next(e for e in sim_reacher.get_hardware_settings() if e["device"] == "EXT_TRIGGER")
        assert entry["armed"] is False

    def test_buffers_reset_at_arm_survive_the_fire(self, sim_reacher):
        """Buffers reset at ARM, not at fire time — the START event the fire
        produces must not be wiped by a reset racing in behind it."""
        sim_reacher.behavior_data = [{"stale": True}]
        sim_reacher.frame_data = [1, 2, 3]
        sim_reacher.arm_external_trigger()
        assert sim_reacher.behavior_data == []
        assert sim_reacher.frame_data == []

        assert _sim(sim_reacher).fire_external_trigger() is True
        assert _wait_until(
            lambda: any(e.get("event") == "START" for e in sim_reacher.behavior_data)
        )
        assert not any(e.get("stale") for e in sim_reacher.behavior_data)

    def test_program_start_time_lifecycle_and_receipt_lag(self, sim_reacher):
        sim_reacher.program_start_time = 999999.0  # stale anchor from a prior run
        sim_reacher.arm_external_trigger()
        assert sim_reacher.program_start_time is None

        before = time.time()
        assert _sim(sim_reacher).fire_external_trigger() is True
        assert _wait_until(lambda: sim_reacher.program_start_time is not None)
        after = time.time()
        assert before <= sim_reacher.program_start_time <= after + 0.05

        with open(sim_reacher._event_log_path) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        start_entries = [e for e in entries if e.get("source") == "external"]
        assert len(start_entries) == 1
        lag = start_entries[0]["receipt_lag_s"]
        assert 0.0 <= lag < 1.0, f"receipt lag {lag} looks unreasonable for an in-process queue hop"

    def test_no_second_session_start_command_on_the_external_path(self, sim_reacher):
        """A duplicate cmd 101 would re-fire microscope.Trigger() — a toggle
        that would stop the scope scanning mid-run. Assert against the actual
        serial write log, not a mock."""
        sent = []
        real_write = sim_reacher.ser.write

        def spy_write(data: bytes):
            sent.append(json.loads(data.decode()))
            real_write(data)

        sim_reacher.ser.write = spy_write

        sim_reacher.arm_external_trigger()
        assert _sim(sim_reacher).fire_external_trigger() is True
        assert _wait_until(lambda: sim_reacher.program_running is True)

        assert all(c.get("cmd") != 101 for c in sent), sent

    def test_back_to_back_triggered_runs_reset_elapsed_time(self, sim_reacher):
        """arm, fire, stop, arm, fire — the second run's clock must not carry
        anything over from the first."""
        sim_reacher.arm_external_trigger()
        assert _sim(sim_reacher).fire_external_trigger() is True
        assert _wait_until(lambda: sim_reacher.program_running is True)
        first_start = sim_reacher.program_start_time
        time.sleep(0.15)
        sim_reacher.stop_program()
        assert _wait_until(lambda: not sim_reacher.program_running and not sim_reacher.ser.is_open)

        # stop_program() closes serial — a real operator would reconnect
        # before arming again, so the test does the same.
        sim_reacher.set_COM_port("SIMULATOR")
        sim_reacher.open_serial()

        sim_reacher.arm_external_trigger()
        assert sim_reacher.program_start_time is None
        assert sim_reacher.behavior_data == []
        assert _sim(sim_reacher).fire_external_trigger() is True
        assert _wait_until(lambda: sim_reacher.program_running is True)
        second_start = sim_reacher.program_start_time

        assert second_start > first_start
        # A fresh FirmwareSimulator backs the reopened port, so its internal
        # clock genuinely restarted at 0 rather than continuing to accumulate.
        assert _sim(sim_reacher)._clock < 1000  # ms; well short of a carried-over run


# ---------------------------------------------------------------------------
# Task 3 — arm/disarm churn and lifecycle exits
# ---------------------------------------------------------------------------


class TestChurnAndLifecycleExits:
    def test_reconnect_over_an_armed_open_port_releases_first(self, sim_reacher):
        """F-2, fixed: open_serial() force-closes an already-open port on a
        reconnect (reacher.py:439-446). Without releasing first, that would
        leave the board watching the pin with the host reporting a fresh
        connect and no record it was ever armed."""
        sim_reacher.arm_external_trigger()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)
        assert sim_reacher.ser.is_open is True  # still open — no stop happened

        sim_reacher.open_serial()  # reconnect over the open, armed port

        assert sim_reacher._external_trigger_armed is False
        assert sim_reacher.ser.is_open is True  # the reconnect itself still succeeds

    def test_100_arm_disarm_cycles(self, sim_reacher):
        for i in range(100):
            sim_reacher.arm_external_trigger()
            assert _wait_until(
                lambda: any(
                    e["device"] == "EXT_TRIGGER" and e["armed"] is True
                    for e in sim_reacher.get_hardware_settings()
                ),
                timeout=1.0,
            ), f"arm did not land on cycle {i}"
            sim_reacher.disarm_external_trigger()
            assert _wait_until(
                lambda: any(
                    e["device"] == "EXT_TRIGGER" and e["armed"] is False
                    for e in sim_reacher.get_hardware_settings()
                ),
                timeout=1.0,
            ), f"disarm did not land on cycle {i}"
        assert sim_reacher._external_trigger_armed is False
        assert sim_reacher.program_running is False

    def test_double_arm_is_harmless(self, sim_reacher):
        sim_reacher.arm_external_trigger()
        sim_reacher.arm_external_trigger()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)
        assert _sim(sim_reacher).fire_external_trigger() is True
        assert _wait_until(lambda: sim_reacher.program_running is True)

    def test_arm_then_stop_releases_without_a_full_teardown(self, sim_reacher):
        """stop_program() releases the trigger before its re-entrance guard
        (reacher.py:1567), so calling it on a merely-armed (not running)
        session must disarm the board without closing serial or ending a
        program that was never running."""
        sim_reacher.arm_external_trigger()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)
        sim_reacher.stop_program()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is False)
        assert sim_reacher.program_running is False
        assert sim_reacher.ser.is_open is True  # no full teardown occurred

    def test_arm_then_destroy_session_releases_the_board(self):
        """SessionManager.destroy_session releases the trigger before
        anything closes the port (session_manager.py:129-140)."""
        sm = SessionManager()
        sid = sm.create_session(port="SIMULATOR")
        instance = sm.get_instance(sid)
        instance.set_COM_port("SIMULATOR")
        instance.open_serial()
        try:
            instance.arm_external_trigger()
            assert _wait_until(lambda: _sim(instance).ext_trigger_armed is True)
            sm.destroy_session(sid)
            assert _sim(instance).ext_trigger_armed is False
            assert instance._external_trigger_armed is False
            assert instance.ser.is_open is False
            with pytest.raises(KeyError):
                sm.get_session(sid)
        finally:
            if instance.ser.is_open:
                instance.close_serial()


# ---------------------------------------------------------------------------
# Task 3 — races
# ---------------------------------------------------------------------------


class TestRaces:
    def test_edge_vs_disarm(self, sim_reacher):
        """Concurrent disarm and fire: exactly one must win, and the board
        must never end up stuck armed with neither side having claimed it."""
        wins = {"fired": 0, "disarmed_first": 0}
        for _ in range(30):
            sim_reacher.arm_external_trigger()
            assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)

            results = {}

            def do_fire():
                results["fired"] = _sim(sim_reacher).fire_external_trigger()

            def do_disarm():
                sim_reacher.disarm_external_trigger()

            t1 = threading.Thread(target=do_fire)
            t2 = threading.Thread(target=do_disarm)
            t1.start()
            t2.start()
            t1.join(timeout=2)
            t2.join(timeout=2)
            assert not t1.is_alive() and not t2.is_alive()

            assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is False)
            if results.get("fired"):
                wins["fired"] += 1
                assert _wait_until(lambda: sim_reacher.program_running is True)
                sim_reacher.stop_program()
                sim_reacher.set_COM_port("SIMULATOR")
                sim_reacher.open_serial()
            else:
                wins["disarmed_first"] += 1

        assert wins["fired"] + wins["disarmed_first"] == 30

    def test_edge_after_stop_is_a_noop(self, sim_reacher):
        """stop_program() (or its release_external_trigger call) must win
        against a trailing edge that lands after the session is torn down."""
        sim_reacher.arm_external_trigger()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)
        sim_reacher.stop_program()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is False)
        assert _sim(sim_reacher).fire_external_trigger() is False
        assert sim_reacher.program_running is False

    def test_edge_vs_start_now_override(self, sim_reacher):
        """The router's "Start Now" override disarms the firmware before
        calling start_program() (program.py). Reproduced at the kernel level
        since that router file is out of scope here: a trailing edge after
        the disarm command must not sneak in a second, external-sourced
        start on top of the software one."""
        sim_reacher.arm_external_trigger()
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)

        sim_reacher.disarm_external_trigger()  # what the override does first
        assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is False)
        assert _sim(sim_reacher).fire_external_trigger() is False  # too late, already disarmed

        sim_reacher.start_program()  # the override's actual start
        assert _wait_until(lambda: sim_reacher.program_running is True)
        assert not any(
            e.get("event") == "START" and e.get("source") == "external"
            for e in sim_reacher.behavior_data
        )

    def test_executor_race_edge_vs_stop(self, sim_reacher):
        """F-001 (program.py): stop_program() runs off the event loop via
        run_in_executor in production specifically so it does not block
        concurrent delivery of the external START line. Firing the edge and
        immediately calling stop_program() (no synchronization) reproduces
        that interleaving: _begin_external_session() runs on the queue
        thread and can set program_running=True before or after
        stop_program()'s own program_running check.

        CONFIRMED by reading the code, not just this test: stop_program()
        reads/writes program_running under self.thread_lock
        (reacher.py:1568-1571), but _begin_external_session() sets it
        (reacher.py:1529) with no lock at all — so the lock protects nothing
        here. This loop cannot force the bad interleaving deterministically
        (it depends on thread-scheduling timing), but it does assert the
        invariant that must survive it regardless of who wins: the trigger
        is never left armed, and the session is always recoverable with one
        more stop_program() call within a bounded time.
        """
        recovered_late = 0
        for _ in range(20):
            sim_reacher.arm_external_trigger()
            assert _wait_until(lambda: _sim(sim_reacher).ext_trigger_armed is True)

            fired = _sim(sim_reacher).fire_external_trigger()
            assert fired is True
            sim_reacher.stop_program()  # races _begin_external_session on the queue thread

            # Let the race actually resolve before inspecting it: the queue
            # thread may not have processed the START line yet even after
            # stop_program() has already returned via its early-return path.
            settled = _wait_until(
                lambda: (not sim_reacher.ser.is_open) or sim_reacher.program_running,
                timeout=3,
            )
            assert settled, "race never settled — neither a full stop nor a start landed"

            if sim_reacher.program_running:
                # stop_program() lost the race (saw program_running=False and
                # returned early via reacher.py:1568-1571) — the edge's
                # queue-thread bookkeeping landed afterward. This is exactly
                # the F-001 race: the operator's stop was silently a no-op.
                recovered_late += 1
                sim_reacher.stop_program()

            assert _wait_until(lambda: not sim_reacher.program_running, timeout=5)
            assert _wait_until(lambda: not sim_reacher.ser.is_open, timeout=5)
            assert _sim(sim_reacher).ext_trigger_armed is False

            sim_reacher.set_COM_port("SIMULATOR")
            sim_reacher.open_serial()

        # Not asserted as a required count — a scheduler that never lets the
        # queue thread lag would legitimately make this 0 on this machine.
        # Recorded so a human reviewing CI output can see whether the race
        # window was actually exercised.
        print(f"executor race: stop lost to the queue thread {recovered_late}/20 times")


# ---------------------------------------------------------------------------
# Task 4 — teardown audit (report-first; findings below are backed by the
# tests in this section, not by a patch)
# ---------------------------------------------------------------------------


class TestKernelBypassesRouterGate:
    """Finding: EXT_TRIGGER_ARM's paradigm gate (int(CommandCode.EXT_TRIGGER_ARM)
    not in get_commands_for_paradigm(...)) lives only in
    api/routers/program.py's /arm-trigger handler. REACHER.arm_external_trigger()
    itself performs no such check. Anything that reaches the kernel without
    going through that route — a future CLI, a script using the package
    directly, or a test — can arm a lite-paradigm session, which the
    simulator now silently no-ops (matching real lite firmware) but which a
    real Mega running a NON-lite sketch would not: the gate exists to keep
    the UI honest about hardware capability, not to protect the kernel."""

    def test_kernel_arm_has_no_paradigm_check_of_its_own(self, sim_reacher):
        _sim(sim_reacher).handle_command({"cmd": 202, "paradigm": "fr_lite"})
        # No exception, no rejection — the kernel method has no gate to fail.
        sim_reacher.arm_external_trigger()
        assert sim_reacher._external_trigger_armed is True


class TestUnplugDoesNotReleaseTheTrigger:
    """A-3, fixed: SessionManager.handle_disconnect now calls
    release_external_trigger() alongside the "disconnected" transition,
    matching every other teardown path (destroy_session, stop_program,
    reset, the explicit /serial/disconnect route). Previously this was the
    one exit that left the host's own _external_trigger_armed bookkeeping
    stale after an unplug.

    In practice the board being physically unplugged likely loses power and
    forgets its own armed state too (unverified without hardware — the
    reasonable inference, per the package's own instruction to prefer that
    framing over asserting unverifiable firmware behavior). The finding was
    about the host's bookkeeping going stale, not about whether the board
    stays armed.
    """

    def test_disconnect_releases_the_trigger(self):
        sm = SessionManager()
        sid = sm.create_session(port="SIMULATOR")
        instance = sm.get_instance(sid)
        instance.set_COM_port("SIMULATOR")
        instance.open_serial()
        try:
            instance.arm_external_trigger()
            assert _wait_until(lambda: _sim(instance).ext_trigger_armed is True)

            sm.handle_disconnect(sid, "cable pulled")

            assert sm.get_session(sid).state == "disconnected"
            # Desired behavior, matching every other exit path: the host's
            # bookkeeping should not claim the board is still armed once the
            # session has been marked disconnected.
            assert instance._external_trigger_armed is False
        finally:
            if instance.ser.is_open:
                instance.close_serial()


class TestCrashPathAudit:
    """Enumerated, not all independently testable in-process:

    | Path | Releases trigger? | Evidence |
    |---|---|---|
    | stop_program() | Yes | reacher.py:1567, before the re-entrance guard |
    | reset() | Yes | reacher.py:319 |
    | SessionManager.destroy_session() | Yes | session_manager.py:129-140, before port teardown |
    | /api/serial/{id}/disconnect route | Yes | api/routers/serial.py:178 |
    | Orphan cleanup (websocket.py:_orphan_cleanup) | Yes (via destroy_session) | websocket.py:356 |
    | Graceful shutdown (SIGINT/SIGTERM -> lifespan) | Yes (via destroy_all -> destroy_session) | api/app.py:264 |
    | SessionManager.handle_disconnect (unplug) | **No** | see TestUnplugDoesNotReleaseTheTrigger |
    | Unhandled exception escaping the main event loop | **No** | see below |
    | SIGKILL / power loss | No (uncatchable by definition) | not a code gap |

    The second "No" is structural and not exercised as an in-process test:
    api/app.py's lifespan releases every session in its post-``yield`` body
    (line 264, ``sm.destroy_all()``), which only runs on an *orderly* return
    from the ``yield`` — i.e. uvicorn's graceful-shutdown path. An unhandled
    exception that escapes the running event loop (crashing the process
    outside of a request handler) does not resume the generator past
    ``yield``; it unwinds past it. diagnostics/setup.py's crash hooks
    (``sys.excepthook``, ``atexit``) only log and flush diagnostics
    (setup.py:267-278) — neither calls into SessionManager. A thread crash in
    read_serial/handle_queue is different and *is* recovered: ``_resilient``
    (reacher.py:516-558) catches, logs, and restarts the thread body up to 10
    times, so an ordinary exception there does not orphan an armed trigger.
    Only exhausting that budget (10 consecutive crashes) or a crash in the
    main thread/event loop itself reaches the uncovered path. This is
    reported rather than tested because reproducing it faithfully needs an
    out-of-process harness (spawn the server, kill it abnormally, inspect
    what the simulator/board would have been left holding) — exactly the
    kind of firmware-adjacent claim the package asked to flag as unverified
    rather than assert.
    """

    def test_placeholder_see_class_docstring(self):
        """No assertion — this class exists to carry the audit table into
        the test report. See TestUnplugDoesNotReleaseTheTrigger for the one
        finding in this table that IS independently reproduced."""
