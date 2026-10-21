"""Host-side AUX safety: the board owns armed/pin state, and /command gates what it cannot honour."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from reacher import pin_overrides
from reacher.api.routers import serial as serial_router
from reacher.api.routers.hardware import CommandRequest, send_command
from reacher.kernel.reacher import REACHER

IDN_FR_AUX = {"level": "000", "device": "CONTROLLER", "sketch": "fr.ino", "version": "v3.4.0-beta.9",
              "baud_rate": 115200, "schedule": "FIXED_RATIO", "caps": "aux"}
IDN_FR_OLD = {k: v for k, v in IDN_FR_AUX.items() if k != "caps"}

AUX_ARM, AUX_TEST, AUX_SET_PIN, AUX_SET_DURATION = 1401, 1403, 1476, 1472


@pytest.fixture
def reacher():
    with (
        patch("serial.Serial"),
        patch("threading.Thread"),
        patch("os.makedirs"),
        patch("logging.basicConfig"),
        patch.object(logging.FileHandler, "_open", return_value=Mock()),
    ):
        yield REACHER()


def _aux(reacher) -> dict:
    return next(e for e in reacher.get_hardware_settings() if e["device"] == "AUX")


def _line(desc):
    return {"level": "001", "device": "AUX", "pin": 22, "desc": desc}


def _unconfigured(command):
    return {"level": "006", "device": "AUX", "error_code": "AUX_UNCONFIGURED",
            "desc": "AUX not configured: set duration (and block N in block mode) first", "command": command}


def test_001_lines_drive_armed(reacher):
    reacher.code_dict["001"](_line("ARMED"))
    assert _aux(reacher)["armed"] is True
    reacher.code_dict["001"](_line("DISARMED"))
    assert _aux(reacher)["armed"] is False


def test_001_for_another_device_is_ignored(reacher):
    reacher.code_dict["001"]({"level": "001", "device": "PUMP_1", "pin": 4, "desc": "ARMED"})
    assert not any(e["device"] == "AUX" for e in reacher.get_hardware_settings())


def test_set_pin_disarms_mirror(reacher):
    reacher.send_command(AUX_ARM)
    assert _aux(reacher)["armed"] is True
    reacher.send_command(AUX_SET_PIN, 30)
    assert _aux(reacher)["armed"] is False
    assert _aux(reacher)["pin"] == 30


@pytest.mark.parametrize("command", [1403, "1403"])
def test_refused_test_fire_leaves_armed(reacher, command):
    reacher.send_command(AUX_ARM)
    reacher.handle_firmware_error(_unconfigured(command))
    assert _aux(reacher)["armed"] is True


@pytest.mark.parametrize("command", [1401, "1401"])
def test_refused_arm_clears_armed(reacher, command):
    reacher.send_command(AUX_ARM)
    reacher.handle_firmware_error(_unconfigured(command))
    assert _aux(reacher)["armed"] is False


def test_pin_conflict_keeps_old_pin(reacher):
    reacher.send_command(AUX_SET_PIN, 30)
    reacher.send_command(AUX_SET_PIN, 5)
    assert _aux(reacher)["pin"] == 5
    reacher.handle_firmware_error({"level": "006", "device": "AUX", "error_code": "AUX_PIN_CONFLICT",
                                   "desc": "AUX pin in use by another device", "command": 1476, "pin": 5})
    assert _aux(reacher)["pin"] == 30


def test_pin_conflict_without_prior_pin_restores_default(reacher):
    reacher.send_command(AUX_SET_PIN, 5)
    reacher.handle_firmware_error({"level": "006", "device": "AUX", "error_code": "AUX_PIN_CONFLICT",
                                   "desc": "AUX pin in use by another device", "command": 1476, "pin": 5})
    assert _aux(reacher)["pin"] == 22


def test_identify_disarms_mirror(reacher):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.code_dict["001"](_line("ARMED"))
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    assert _aux(reacher)["armed"] is False


def test_identify_without_aux_adds_no_row(reacher):
    reacher.update_firmware_information(dict(IDN_FR_OLD))
    assert not any(e["device"] == "AUX" for e in reacher.get_hardware_settings())


def _command_request(instance, port="SIM1"):
    info = SimpleNamespace(paradigm="fr", instance=instance, state="connected", board="mega", port=port)
    sm = SimpleNamespace(get_session=lambda _sid: info)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=sm)))


async def test_command_rejects_aux_without_cap(reacher):
    reacher.update_firmware_information(dict(IDN_FR_OLD))
    reacher.send_command = Mock()
    with pytest.raises(HTTPException) as exc:
        await send_command("rejects-nocap", CommandRequest(code=AUX_ARM), _command_request(reacher))
    assert exc.value.status_code == 422
    assert exc.value.detail == {"error": "firmware_lacks_cap", "cap": "aux"}
    reacher.send_command.assert_not_called()


async def test_command_allows_aux_with_cap(reacher):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.send_command = Mock()
    await send_command("allows-cap", CommandRequest(code=AUX_ARM), _command_request(reacher))
    reacher.send_command.assert_called_once_with(AUX_ARM, None)


async def test_command_rejects_aux_pin_onto_other_component(reacher):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.send_command = Mock()
    with pytest.raises(HTTPException) as exc:
        await send_command("conflict-default", CommandRequest(code=AUX_SET_PIN, value=4), _command_request(reacher))
    assert exc.value.status_code == 422
    assert exc.value.detail == {"error": "pin_conflict", "pin": 4, "component": "pump"}
    reacher.send_command.assert_not_called()


async def test_command_pin_conflict_uses_overrides(reacher, monkeypatch):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.send_command = Mock()
    monkeypatch.setattr(pin_overrides, "get", lambda _port, _board=None: {"pump": 30, "lick": 31})
    # 4 is no longer the pump's pin; 30 now is.
    await send_command("conflict-free", CommandRequest(code=AUX_SET_PIN, value=4), _command_request(reacher))
    with pytest.raises(HTTPException) as exc:
        await send_command("conflict-override", CommandRequest(code=AUX_SET_PIN, value=30), _command_request(reacher))
    assert exc.value.detail["component"] == "pump"


async def test_replay_labels_idn_timeout(monkeypatch):
    instance = Mock()
    instance._firmware_ready.wait.return_value = False
    instance.get_firmware_caps.return_value = set()
    info = SimpleNamespace(state="disconnected", instance=instance, port="/dev/ttyACM0", paradigm="fr", board=None)
    sm = Mock()
    sm.get_session.return_value = info
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(serial_router, "detect_board_from_port", lambda _p: "mega")
    monkeypatch.setattr(serial_router._ws, "enqueue_event", lambda *a, **k: None)
    monkeypatch.setattr(pin_overrides, "get", lambda _port, _board=None: {"aux": 30})
    body = await serial_router.connect_serial("idn-timeout", request)
    assert body["skipped_pins"] == [{"component": "aux", "reason": "idn_timeout"}]
    assert body["replayed_pins"] == {}


async def test_replay_labels_lacking_cap_when_idn_answered(monkeypatch):
    instance = Mock()
    instance._firmware_ready.wait.return_value = True
    instance.get_firmware_caps.return_value = {"other"}
    info = SimpleNamespace(state="disconnected", instance=instance, port="/dev/ttyACM0", paradigm="fr", board=None)
    sm = Mock()
    sm.get_session.return_value = info
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(serial_router, "detect_board_from_port", lambda _p: "mega")
    monkeypatch.setattr(serial_router._ws, "enqueue_event", lambda *a, **k: None)
    monkeypatch.setattr(pin_overrides, "get", lambda _port, _board=None: {"aux": 30})
    body = await serial_router.connect_serial("lacks-cap", request)
    assert body["skipped_pins"] == [{"component": "aux", "reason": "firmware_lacks_cap"}]
