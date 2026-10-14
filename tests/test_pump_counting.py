"""Configurable pump counting: which pump's INFUSION events count toward an Infusion limit."""

import logging
import time
from unittest.mock import Mock, patch

import pytest

from reacher.kernel.reacher import REACHER


@pytest.fixture
def mock_serial():
    with patch("serial.Serial") as mock_serial_class, patch("serial.tools.list_ports.comports") as mock_comports:
        mock_serial_instance = Mock()
        mock_serial_instance.baudrate = 115200
        mock_serial_class.return_value = mock_serial_instance
        mock_comports.return_value = [Mock(device="COM1", vid=1, pid=1)]
        yield mock_serial_instance


@pytest.fixture
def kernel(mock_serial):
    with (
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        instance = REACHER()
    instance._write_event_log = lambda *a, **k: None
    instance.logger = logging.getLogger("test.pump_counting")
    instance.program_running = True
    instance.program_flag.clear()
    return instance


def _infusion(device):
    return {
        "level": "007", "device": device, "event": "INFUSION",
        "pin": 4, "start_timestamp": 100, "end_timestamp": 200,
    }


def _at(monkeypatch, now):
    monkeypatch.setattr(time, "time", lambda: now)


class TestRewardChainPump2:
    def test_pump2_reward_chain_counts_toward_infusions(self, kernel):
        kernel.send_command(221, 1)
        for _ in range(2):
            kernel.update_behavioral_events(_infusion("PUMP_2"))
        assert kernel.get_total_infusion_count() == 2

    def test_default_counts_pump1_spellings_and_not_pump2(self, kernel):
        for device in ("PUMP_1", "PUMP", "PUMP_2"):
            kernel.update_behavioral_events(_infusion(device))
        assert kernel.get_total_infusion_count() == 2
        assert kernel.get_infusions_by_pump() == {"PUMP_1": 2, "PUMP_2": 1}

    def test_221_switch_counts_by_pump_active_at_arrival(self, kernel):
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        kernel.send_command(221, 1)
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        assert kernel.get_total_infusion_count() == 2
        assert kernel.get_infusions_by_pump() == {"PUMP_1": 2, "PUMP_2": 1}

    def test_221_switch_back_to_pump1(self, kernel):
        kernel.send_command(221, 1)
        kernel.send_command(221, 0)
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        assert kernel.get_total_infusion_count() == 1

    def test_failed_221_write_does_not_move_reward_pump(self, kernel):
        kernel.ser.write.side_effect = OSError("port gone")
        with pytest.raises(OSError):
            kernel.send_command(221, 1)
        assert kernel._reward_pump == "PUMP_1"

    def test_explicit_both_pumps_counts_each(self, kernel):
        kernel.set_counted_pumps(["PUMP_1", "PUMP_2"])
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        assert kernel.get_total_infusion_count() == 2

    def test_explicit_pump2_only_ignores_reward_pump(self, kernel):
        kernel.set_counted_pumps(["PUMP_2"])
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        assert kernel.get_total_infusion_count() == 1
        assert kernel.get_infusions_by_pump() == {"PUMP_1": 1, "PUMP_2": 1}

    def test_explicit_list_ignores_221(self, kernel):
        kernel.set_counted_pumps(["PUMP_2"])
        kernel.send_command(221, 0)
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        assert kernel.get_total_infusion_count() == 1

    def test_set_counted_pumps_mid_run_reevaluates_immediately(self, kernel):
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        kernel.send_command(221, 1)
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        assert kernel.get_total_infusion_count() == 2

        kernel.set_counted_pumps(["PUMP_2"])
        assert kernel.get_total_infusion_count() == 1
        kernel.set_counted_pumps(["PUMP_1"])
        assert kernel.get_total_infusion_count() == 1
        kernel.set_counted_pumps(None)
        assert kernel.get_total_infusion_count() == 2
        assert kernel.get_counted_pumps() == ["PUMP_2"]

    def test_set_counted_pumps_dedupes_and_keeps_order(self, kernel):
        kernel.set_counted_pumps(["PUMP_2", "PUMP_1", "PUMP_2"])
        assert kernel.get_counted_pumps() == ["PUMP_2", "PUMP_1"]

    def test_set_counted_pumps_rejects_unknown_key(self, kernel):
        with pytest.raises(ValueError):
            kernel.set_counted_pumps(["PUMP_3"])
        assert kernel.get_counted_pumps() == ["PUMP_1"]

    def test_set_counted_pumps_none_restores_default(self, kernel):
        kernel.set_counted_pumps(["PUMP_2"])
        kernel.set_counted_pumps(None)
        assert kernel.get_counted_pumps() == ["PUMP_1"]

    def test_split_segment_keeps_whole_session_totals(self, kernel, tmp_path):
        kernel._export_segment = lambda behavior, suffix="": str(tmp_path / f"seg{suffix}.csv")
        kernel._emit = lambda *a, **k: None
        kernel.send_command(221, 1)
        kernel.update_behavioral_events(_infusion("PUMP_1"))
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel.split_segment()
        kernel.update_behavioral_events(_infusion("PUMP_2"))

        assert kernel._infusions_by_pump == {"PUMP_1": 0, "PUMP_2": 1}
        assert kernel.get_infusions_by_pump() == {"PUMP_1": 1, "PUMP_2": 2}
        assert kernel.get_total_infusion_count() == 2

    def test_split_segment_keeps_explicit_total(self, kernel, tmp_path):
        kernel._export_segment = lambda behavior, suffix="": str(tmp_path / f"seg{suffix}.csv")
        kernel._emit = lambda *a, **k: None
        kernel.set_counted_pumps(["PUMP_2"])
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel.split_segment()
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        assert kernel.get_total_infusion_count() == 2

    def test_restart_zeroes_per_pump_counts_and_keeps_setting(self, kernel, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        kernel.set_counted_pumps(["PUMP_2"])
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel.send_serial_command = Mock()
        kernel.restart_program()

        assert kernel.get_infusions_by_pump() == {"PUMP_1": 0, "PUMP_2": 0}
        assert kernel.get_total_infusion_count() == 0
        assert kernel.get_counted_pumps() == ["PUMP_2"]

    def test_start_zeroes_per_pump_counts(self, kernel):
        kernel.send_command(221, 1)
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel.program_running = False
        kernel.start_program()

        assert kernel.get_infusions_by_pump() == {"PUMP_1": 0, "PUMP_2": 0}
        assert kernel.get_total_infusion_count() == 0

    def test_reset_keeps_counted_pumps_setting(self, kernel, mocker):
        kernel.set_counted_pumps(["PUMP_2"])
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        mocker.patch.object(kernel, "clear_queue")
        mocker.patch.object(kernel, "close_serial")
        mocker.patch("threading.Thread")

        kernel.reset()

        assert kernel.get_counted_pumps() == ["PUMP_2"]
        assert kernel.get_infusions_by_pump() == {"PUMP_1": 0, "PUMP_2": 0}

    def test_pump2_chain_meets_infusion_limit(self, kernel, monkeypatch):
        kernel.stop_program = Mock()
        kernel.program_start_time = 1000.0
        kernel.set_limit_type("Infusion")
        kernel.set_infusion_limit(2)
        kernel.set_stop_delay(0)
        kernel.send_command(221, 1)
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        _at(monkeypatch, 1010.0)

        kernel.check_limit_met()

        kernel.stop_program.assert_called_once()

    def test_infusion_count_emit_payload(self, kernel):
        kernel._emit = Mock()
        kernel.set_counted_pumps(["PUMP_1", "PUMP_2"])
        kernel._emit.assert_called_with("infusion_count", {
            "counted": 0,
            "by_pump": {"PUMP_1": 0, "PUMP_2": 0},
            "counted_pumps": ["PUMP_1", "PUMP_2"],
            "follows_reward_pump": False,
        })

        kernel.set_counted_pumps(None)
        kernel.send_command(221, 1)
        kernel.update_behavioral_events(_infusion("PUMP_2"))
        kernel._emit.assert_called_with("infusion_count", {
            "counted": 1,
            "by_pump": {"PUMP_1": 0, "PUMP_2": 1},
            "counted_pumps": ["PUMP_2"],
            "follows_reward_pump": True,
        })

    def test_non_infusion_pump_event_does_not_emit(self, kernel):
        kernel._emit = Mock()
        kernel.update_behavioral_events({
            "level": "007", "device": "PUMP_2", "event": "TONE",
            "pin": 4, "start_timestamp": 1, "end_timestamp": 2,
        })
        assert all(c.args[0] != "infusion_count" for c in kernel._emit.call_args_list)

    def test_open_serial_resets_reward_pump(self, kernel, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        kernel.send_command(221, 1)
        assert kernel._reward_pump == "PUMP_2"
        kernel.open_serial()
        assert kernel._reward_pump == "PUMP_1"
