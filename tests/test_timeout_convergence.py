"""1074/1374 (lever timeout) and 1077/1377 (timeout mode) are one write each.

Firmware has exactly one timeout interval and one timeout-mode flag. Both RH and LH
cases of each pair run the same statements onto the same global
(``TIMEOUT_INTERVAL`` / ``Scheduler::SetTimeoutInterval``, ``Scheduler::SetTimeoutMode``;
fr.ino:315-321 and 335-341), so the last write wins for both levers. The host used to
model 1074/1374 as independent ``LEVER_RH``/``LEVER_LH`` fields (and 1077/1377 not at
all), recording a value for each lever the board cannot hold. Same bug class as the
ratio triple in ``test_commands.TestRatioSimulatorConvergence``, and the same remedy:
one scheduler-scoped ``CONTROLLER`` field.

These tests drive the real kernel over a stubbed serial port rather than the simulator:
``FirmwareSimulator`` still keeps a per-lever timeout (``lever_rh_timeout`` /
``lever_lh_timeout``), which masks the clobber. If a future change splits the host
fields back into per-lever ones to make a test pass, that change is the regression.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reacher.kernel.commands import COMMAND_REGISTRY
from reacher.kernel.reacher import _COMMAND_STATE_MAP, REACHER

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware"

TIMEOUT_CODES = (1074, 1374)
MODE_CODES = (1077, 1377)


@pytest.fixture
def host():
    """A kernel with a stubbed serial port; every config event it emits is recorded."""
    events: list = []
    instance = REACHER(session_id="timeout-convergence", event_callback=lambda sid, kind, data: events.append((kind, data)))
    instance.ser = MagicMock()
    instance.ser.is_open = True
    instance.captured_events = events
    return instance


def _row(instance: REACHER, device: str):
    return next((e for e in instance.get_hardware_settings() if e.get("device") == device), None)


def _lever_rows(instance: REACHER) -> list:
    return [e for e in instance.get_hardware_settings() if e.get("device") in ("LEVER_RH", "LEVER_LH")]


class TestHostModelConverges:
    @pytest.mark.parametrize(("first", "second"), [(1074, 1374), (1374, 1074)])
    def test_timeout_last_write_wins_across_levers(self, host, first, second):
        host.send_command(first, 20_000)
        host.send_command(second, 0)

        assert _row(host, "CONTROLLER")["timeout"] == 0
        assert _lever_rows(host) == [], "timeout is not per-lever: no LEVER_RH/LEVER_LH row may carry it"

    @pytest.mark.parametrize(("first", "second"), [(1077, 1377), (1377, 1077)])
    def test_timeout_mode_last_write_wins_across_levers(self, host, first, second):
        host.send_command(first, 1)
        host.send_command(second, 0)

        assert _row(host, "CONTROLLER")["timeout_mode"] == 0
        assert _lever_rows(host) == []

    @pytest.mark.parametrize("code", TIMEOUT_CODES)
    def test_a_lone_timeout_write_is_what_the_host_records(self, host, code):
        host.send_command(code, 20_000)
        assert _row(host, "CONTROLLER")["timeout"] == 20_000

    @pytest.mark.parametrize("code", MODE_CODES)
    def test_a_lone_mode_write_is_modelled(self, host, code):
        """1077/1377 used to be absent from the host model: hardware_settings stayed empty."""
        host.send_command(code, 1)
        assert _row(host, "CONTROLLER")["timeout_mode"] == 1

    def test_timeout_and_mode_do_not_collide(self, host):
        host.send_command(1074, 5_000)
        host.send_command(1377, 1)
        assert _row(host, "CONTROLLER") == {"device": "CONTROLLER", "timeout": 5_000, "timeout_mode": 1}

    def test_the_optimistic_echo_names_the_scheduler_not_a_lever(self, host):
        host.send_command(1074, 7_000)
        host.send_command(1374, 3_000)
        echoes = [d for kind, d in host.captured_events if kind == "config" and "timeout" in d]
        assert echoes == [{"device": "CONTROLLER", "timeout": 7_000}, {"device": "CONTROLLER", "timeout": 3_000}]

    def test_state_map_points_all_four_codes_at_the_scheduler(self):
        for code in TIMEOUT_CODES:
            assert _COMMAND_STATE_MAP[code][:2] == ("CONTROLLER", "timeout")
        for code in MODE_CODES:
            assert _COMMAND_STATE_MAP[code][:2] == ("CONTROLLER", "timeout_mode")


class TestSpecsSayTheyAreSchedulerWide:
    @pytest.mark.parametrize("code", TIMEOUT_CODES + MODE_CODES)
    def test_description_states_the_shared_write(self, code):
        description = COMMAND_REGISTRY[code].description.lower()
        assert "last write wins" in description, f"{code} reads as per-lever"
        assert "not per-lever" in description or "scheduler-wide" in description, f"{code} reads as per-lever"
        assert "exactly one" in description, f"{code} gives no guidance on which code to send"


@pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware/ is absent (wheel-only tree)")
class TestFirmwareStillHasOneRegister:
    """The host collapse above is only right while the board has one register. Parse the
    sketches so a firmware that grows per-lever timeouts fails here, not silently in the field."""

    SKETCHES = ["fr", "pr", "vi", "fr_lite", "pr_lite", "vi_lite"]

    @staticmethod
    def _case(sketch: str, name: str) -> str:
        src = (FIRMWARE / sketch / f"{sketch}.ino").read_text()
        match = re.search(rf"case Cmd::{name}:(.*?)break;", src, re.S)
        assert match, f"{sketch} has no case Cmd::{name}"
        return re.sub(r"logParamChange\([^;]*;", "", match.group(1))  # the ACK carries the lever's name; the write does not

    @pytest.mark.parametrize("sketch", SKETCHES)
    def test_rh_and_lh_timeout_run_the_same_statements(self, sketch):
        rh, lh = self._case(sketch, "LEVER_RH_SET_TIMEOUT"), self._case(sketch, "LEVER_LH_SET_TIMEOUT")
        assert "SetTimeoutInterval" in rh
        assert rh.split() == lh.split()

    @pytest.mark.parametrize("sketch", SKETCHES)
    def test_rh_and_lh_timeout_mode_run_the_same_statements(self, sketch):
        rh, lh = self._case(sketch, "LEVER_RH_SET_TIMEOUT_MODE"), self._case(sketch, "LEVER_LH_SET_TIMEOUT_MODE")
        assert "SetTimeoutMode" in rh
        assert rh.split() == lh.split()
