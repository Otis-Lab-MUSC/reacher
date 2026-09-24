"""The simulator and kernel treat a firmware param-change ACK the way a real board's is shaped.

Firmware answers many setters with ``logParamChange(device, param, value)``, i.e.
``{"level":"000","device":D,"param":P,"value":V}`` (``Device.cpp``). Two things
used to hide behind a simulator that never sent one:

* the simulator applied 201/1075/1375 silently, so nothing exercised the record
  a rig actually produces (pressure-test F5a);
* ``update_firmware_information`` replaced the whole ``hardware_settings`` row
  with that record, wiping ``armed``/``reinforced`` and leaving ``param``/
  ``value`` where the host row keeps ``<param>: <value>`` (pressure-test F5b).

The expected record is parsed out of each sketch, not restated here, so the
simulator cannot drift from the firmware without this file noticing.
"""

import json
import queue
import re
import time
from pathlib import Path

import pytest

from reacher.kernel.reacher import REACHER
from reacher.kernel.simulator import FirmwareSimulator

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware"

pytestmark = pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware/ is absent (wheel-only tree)")

SKETCHES = [
    "fr", "pr", "vi", "omission", "pavlovian",
    "fr_lite", "pr_lite", "vi_lite", "omission_lite",
]

#: (command code, the Commands.h name the sketches switch on)
RATIO_COMMANDS = [(201, "SET_RATIO"), (1075, "LEVER_RH_SET_RATIO"), (1375, "LEVER_LH_SET_RATIO")]


def _firmware_ack(sketch: str, case_name: str):
    """``(device, param)`` the sketch logs for *case_name*; ``None`` if it has no such case
    or logs nothing for it."""
    src = (FIRMWARE / sketch / f"{sketch}.ino").read_text()
    case = re.search(rf"case Cmd::{case_name}:(.*?)break;", src, re.S)
    if case is None:
        return None
    ack = re.search(r'logParamChange\(F\("(\w+)"\),\s*F\("(\w+)"\)', case.group(1))
    return ack.groups() if ack else None


def _drain(q: queue.Queue) -> list:
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait()))
    return out


def _param_records(records: list) -> list:
    return [r for r in records if "param" in r and r.get("device") != "EXT_TRIGGER"]


class TestSimulatorEmitsTheFirmwareShapedAck:
    """F5a — emitted exactly where the sketch emits it, in the sketch's own shape."""

    @pytest.mark.parametrize("sketch", SKETCHES)
    @pytest.mark.parametrize(("code", "case_name"), RATIO_COMMANDS)
    def test_ack_matches_the_sketch(self, sketch, code, case_name):
        q: queue.Queue = queue.Queue()
        sim = FirmwareSimulator(q, paradigm=sketch)
        _drain(q)

        sim.handle_command({"cmd": code, "ratio": 7})

        emitted = _param_records(_drain(q))
        ack = _firmware_ack(sketch, case_name)
        if ack is None:
            assert emitted == [], f"{sketch} has no ACK for {code}, but the simulator emitted {emitted}"
        else:
            device, param = ack
            assert emitted == [{"level": "000", "device": device, "param": param, "value": 7}]

    def test_the_documented_split_still_holds(self):
        """201 is a CONTROLLER ACK; 1075/1375 are per-lever ACKs. If a sketch ever unifies
        them this fails and the F5 design note needs revisiting."""
        assert _firmware_ack("fr", "SET_RATIO") == ("CONTROLLER", "ratio")
        assert _firmware_ack("fr", "LEVER_RH_SET_RATIO") == ("LEVER_RH", "ratio")
        assert _firmware_ack("fr", "LEVER_LH_SET_RATIO") == ("LEVER_LH", "ratio")


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def sim_reacher():
    """A real kernel over SimulatedSerial with a callback that records every emitted event."""
    events: list = []
    instance = REACHER(session_id="param-ack", event_callback=lambda sid, kind, data: events.append((kind, data)))
    instance.set_COM_port("SIMULATOR", "fr")
    instance.open_serial()
    instance.captured_events = events
    yield instance
    if instance.ser.is_open:
        instance.close_serial()


def _row(instance: REACHER, device: str) -> dict:
    return next(e for e in instance.get_hardware_settings() if e.get("device") == device)


def _config_events(instance: REACHER, device: str, param: str) -> list:
    return [
        d for kind, d in instance.captured_events
        if kind == "config" and d.get("device") == device and d.get("param") == param
    ]


class TestThroughTheKernel:
    """F5d — the whole path: command out, simulator ACK back, kernel state."""

    def test_201_ack_is_the_firmware_record_and_the_host_row_keeps_ratio(self, sim_reacher):
        device, param = _firmware_ack("fr", "SET_RATIO")

        sim_reacher.send_command(201, 7)

        assert _wait_until(lambda: _config_events(sim_reacher, device, param)), (
            "no param-change ACK reached the kernel: the simulator is not modelling the firmware's reply to 201"
        )
        # The wire contract is the raw firmware record.
        assert _config_events(sim_reacher, device, param) == [
            {"level": "000", "device": "CONTROLLER", "param": "ratio", "value": 7}
        ]
        row = _row(sim_reacher, "CONTROLLER")
        assert row["ratio"] == 7
        assert "param" not in row and "value" not in row

    def test_lever_ack_keeps_armed_on_the_row(self, sim_reacher):
        device, param = _firmware_ack("fr", "LEVER_RH_SET_RATIO")
        sim_reacher.send_command(1001)
        assert _row(sim_reacher, "LEVER_RH")["armed"] is True

        sim_reacher.send_command(1075, 3)

        assert _wait_until(lambda: _config_events(sim_reacher, device, param))
        row = _row(sim_reacher, "LEVER_RH")
        assert row["armed"] is True, "the firmware ACK wiped armed from the host row"
        assert row["ratio"] == 3
        assert "param" not in row and "value" not in row


@pytest.fixture
def bare_reacher():
    """A kernel with no threads and no serial, for feeding update_firmware_information directly."""
    events: list = []
    instance = REACHER(session_id="param-ack-unit", event_callback=lambda sid, kind, data: events.append((kind, data)))
    instance.captured_events = events
    return instance


class TestKernelMergeSemantics:
    """F5b — the rule, stated on the kernel alone so it cannot pass by accident of the simulator."""

    def test_non_controller_ack_merges_into_the_row(self, bare_reacher):
        bare_reacher.hardware_settings.append({"device": "LEVER_RH", "armed": True, "reinforced": True})
        record = {"level": "000", "device": "LEVER_RH", "param": "timeout", "value": 500}

        bare_reacher.update_firmware_information(dict(record))

        row = _row(bare_reacher, "LEVER_RH")
        assert row == {"device": "LEVER_RH", "armed": True, "reinforced": True, "level": "000", "timeout": 500}
        assert len([e for e in bare_reacher.hardware_settings if e["device"] == "LEVER_RH"]) == 1
        assert bare_reacher.captured_events[-1] == ("config", record)

    def test_non_controller_ack_with_no_row_appends_the_normalised_record(self, bare_reacher):
        bare_reacher.update_firmware_information({"level": "000", "device": "LEVER_LH", "param": "reinforced", "value": True})

        assert bare_reacher.hardware_settings == [{"level": "000", "device": "LEVER_LH", "reinforced": True}]

    def test_controller_ack_also_lands_on_the_controller_row(self, bare_reacher):
        bare_reacher.hardware_settings.append({"device": "CONTROLLER", "ratio": 7})
        record = {"level": "000", "device": "CONTROLLER", "param": "session_paused", "value": True}

        bare_reacher.update_firmware_information(dict(record))

        assert _row(bare_reacher, "CONTROLLER") == {
            "device": "CONTROLLER", "ratio": 7, "level": "000", "session_paused": True,
        }
        # ...and the existing firmware_information merge and readiness signal are untouched.
        assert bare_reacher.firmware_information["param"] == "session_paused"
        assert bare_reacher._firmware_ready.is_set()
        assert bare_reacher.captured_events[-1] == ("config", record)

    def test_identify_is_not_a_param_change(self, bare_reacher):
        bare_reacher.update_firmware_information(
            {"level": "000", "device": "CONTROLLER", "sketch": "fr.ino", "version": "v3.4.0", "baud_rate": 115200}
        )

        assert bare_reacher.hardware_settings == []
        assert bare_reacher.firmware_information["sketch"] == "fr.ino"
