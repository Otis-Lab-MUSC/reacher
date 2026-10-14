"""The SIMULATOR names pump 2 the way the firmware does.

Reward-chain INFUSION events are ``PUMP_1`` for the primary pump and ``PUMP_2``
once SET_ACTIVE_PUMP selects the secondary; Pavlovian logs ``PUMP``/``PUMP_2``.
The manual pump-2 test answers with a parameter echo, not an infusion event.
"""

import json
import queue

from reacher.kernel.simulator import FirmwareSimulator, SimulatedSerial

ARM_FR_NO_LICK = (1001, 301, 311, 401, 411)
ARM_PAV = (1001, 1301, 301, 311, 401, 411, 501)
HORIZON_MS = 300_000


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


def run(paradigm, commands=(), arm=ARM_FR_NO_LICK, horizon_ms=HORIZON_MS, seed=1):
    q = queue.Queue()
    sim = FirmwareSimulator(q, paradigm=paradigm, seed=seed, realtime=False)
    for c in _cmds(arm) + _cmds(commands):
        sim.handle_command(c)
    sim.start()
    sim.run_until(horizon_ms)
    msgs = []
    while not q.empty():
        msgs.append(json.loads(q.get()))
    return msgs


def ev(msgs, device=None, event=None):
    return [
        m for m in msgs
        if m.get("level") == "007"
        and (device is None or m.get("device") == device)
        and (event is None or m.get("event") == event)
    ]


def pump_devices(msgs):
    return {m["device"] for m in msgs if m.get("level") == "007" and m.get("event") == "INFUSION"}


FR_REWARD = [(201, "ratio", 1), (472, "duration", 500), (482, "duration", 1800), (1074, "timeout", 0)]


class TestRewardChainDeviceNames:
    def test_fr_primary_pump_logs_pump_1(self):
        msgs = run("fr", FR_REWARD)
        assert pump_devices(msgs) == {"PUMP_1"}
        assert len(ev(msgs, "PUMP_1", "INFUSION")) > 0

    def test_fr_active_pump2_logs_pump_2_and_never_pump_1(self):
        msgs = run("fr", FR_REWARD + [(221, "pump2", True)])
        assert pump_devices(msgs) == {"PUMP_2"}
        assert len(ev(msgs, "PUMP_2", "INFUSION")) > 0

    def test_fr_pump2_selected_then_cleared_returns_to_pump_1(self):
        msgs = run("fr", FR_REWARD + [(221, "pump2", True), (221, "pump2", False)])
        assert pump_devices(msgs) == {"PUMP_1"}

    def test_pavlovian_cs_plus_logs_pump_and_cs_minus_logs_pump_2(self):
        msgs = run("pavlovian", [(206, "probability", 100), (207, "probability", 100)], arm=ARM_PAV,
                   horizon_ms=300_000)
        assert pump_devices(msgs) == {"PUMP", "PUMP_2"}


class TestManualPumpTest:
    def test_pump2_test_echoes_a_param_change_not_an_infusion(self):
        q = queue.Queue()
        sim = FirmwareSimulator(q, paradigm="fr", seed=1, realtime=False)
        sim.handle_command({"cmd": 413})
        msgs = []
        while not q.empty():
            msgs.append(json.loads(q.get()))
        assert {"level": "000", "device": "PUMP2", "param": "test", "value": "FIRED"} in msgs
        assert not ev(msgs, "PUMP", "INFUSION") and not ev(msgs, "PUMP_2", "INFUSION")


class TestPowerOnPumpTarget:
    def test_reopening_the_port_resets_the_reward_chain_to_pump_1(self):
        ser = SimulatedSerial(paradigm="fr")
        ser.open()
        ser.write(json.dumps({"cmd": 221, "pump2": True}).encode() + b"\n")
        assert ser._simulator.pump2_active is True
        ser.close()
        ser.open()
        assert ser._simulator.pump2_active is False
