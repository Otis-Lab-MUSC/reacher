"""A Pavlovian session that completes itself ends itself (pavlovian.ino loop -> EndSession).

The sketch prints CONTROLLER END on the tick ``IsComplete()`` turns true, and a later cmd 100
hits EndSession()'s early return — no second END. The simulator used to print END only from
stop(), so an unarmed session's Stop waited out the kernel's 8 s END timeout.
"""

import json
import logging
import queue
import threading
import time

from reacher.kernel.simulator import FirmwareSimulator
from tests.sim_harness import connect, create_session, events, send, start, stop, wait_until
from tests.sim_harness import sim_client as sim_client  # noqa: PLC0414 - fixture re-export

PAV = "pavlovian"
TIMEOUT_WARNING = "Timed out waiting for CONTROLLER END"


def _ends(msgs):
    return events(msgs, device="CONTROLLER", event="END")


def test_unarmed_start_emits_one_end_at_zero_and_stop_adds_none():
    q = queue.Queue()
    sim = FirmwareSimulator(q, paradigm=PAV, seed=1, realtime=False)
    for code in (300, 310):
        sim.handle_command({"cmd": code})
    sim.start()
    sim.run_until(1_000)
    sim.stop()
    msgs = []
    while not q.empty():
        msgs.append(json.loads(q.get()))
    ends = _ends(msgs)
    assert [m["timestamp"] for m in ends] == [0]


def test_armed_session_ends_after_all_trials_then_stop_adds_none():
    q = queue.Queue()
    sim = FirmwareSimulator(q, paradigm=PAV, seed=1, realtime=False)
    for c in ({"cmd": 301}, {"cmd": 310}, {"cmd": 208, "count": 2}, {"cmd": 209, "count": 0}):
        sim.handle_command(c)
    sim.start()
    sim.run_until(600_000)
    sim.stop()
    msgs = []
    while not q.empty():
        msgs.append(json.loads(q.get()))
    done = events(msgs, device="PAVLOV", event="ALL_TRIALS_COMPLETE")
    ends = _ends(msgs)
    assert len(done) == 1 and len(ends) == 1
    assert msgs.index(done[0]) < msgs.index(ends[0])


def _unarmed_stop(client):
    sid = create_session(client, PAV).json()["session_id"]
    assert connect(client, sid).status_code == 200
    for code in (300, 310):
        assert send(client, sid, code).status_code == 200, code
    instance = client.app.state.session_manager.get_session(sid).instance
    assert start(client, sid).status_code in (200, 201, 202)
    wait_until(instance._controller_end_received.is_set, timeout=3.0)
    t0 = time.monotonic()
    resp = stop(client, sid)
    return resp, time.monotonic() - t0, instance


def test_real_stack_unarmed_stop_is_fast_and_clean(sim_client, caplog):
    caplog.set_level(logging.WARNING)
    resp, elapsed, _ = _unarmed_stop(sim_client)
    assert 200 <= resp.status_code < 300, resp.text
    assert elapsed < 1.0
    assert TIMEOUT_WARNING not in caplog.text
    assert "export may be incomplete" not in caplog.text


def test_golden_negative_session_that_never_prints_end_times_out(sim_client, caplog, monkeypatch):
    def silent_end(self):
        # An ended session whose END never reaches the host: stop() adds none either.
        self._running = False
        self._self_ended = True
        self._scheduler_end()

    monkeypatch.setattr(FirmwareSimulator, "_pav_self_end", silent_end)
    # Shrink the kernel's 8 s END wait; the rest of Event keeps working.
    real_wait = threading.Event.wait
    monkeypatch.setattr(
        threading.Event, "wait", lambda self, timeout=None: real_wait(self, 0.3 if timeout == 8.0 else timeout)
    )
    caplog.set_level(logging.WARNING)
    sid = create_session(sim_client, PAV).json()["session_id"]
    assert connect(sim_client, sid).status_code == 200
    for code in (300, 310):
        send(sim_client, sid, code)
    assert start(sim_client, sid).status_code in (200, 201, 202)
    time.sleep(0.3)
    stop(sim_client, sid)
    assert TIMEOUT_WARNING in caplog.text
