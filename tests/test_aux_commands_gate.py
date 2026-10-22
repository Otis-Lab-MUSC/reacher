"""/commands offers AUX only to firmware whose IDN advertises ``caps: "aux"``.

A paradigm name cannot tell an AUX-capable fr build from beta.9 fr (both report
FIXED_RATIO), so the gate is the IDN capability, not the paradigm.
"""

import json
import logging
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from reacher.api.routers.hardware import get_commands
from reacher.kernel.reacher import REACHER
from reacher.kernel.simulator import FirmwareSimulator

AUX_CODES = {1400, 1401, 1403, 1472, 1476, 1477, 1480, 1481, 1482, 1483, 1484, 1485}

IDN_FR_OLD = {"level": "000", "device": "CONTROLLER", "sketch": "fr.ino", "version": "v3.4.0-beta.9",
              "baud_rate": 115200, "schedule": "FIXED_RATIO"}
IDN_FR_AUX = {**IDN_FR_OLD, "caps": "aux"}


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


async def _offered(paradigm, instance) -> set[int]:
    info = SimpleNamespace(paradigm=paradigm, instance=instance)
    sm = SimpleNamespace(get_session=lambda _sid: info)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=sm)))
    body = await get_commands("sid", request)
    return {int(c["code"]) for c in body["commands"]}


async def test_fr_before_any_idn_hides_aux(reacher):
    assert not AUX_CODES & await _offered("fr", reacher)


async def test_fr_without_caps_hides_aux(reacher):
    reacher.update_firmware_information(dict(IDN_FR_OLD))
    offered = await _offered("fr", reacher)
    assert 1001 in offered  # the paradigm's ordinary commands are still there
    assert not AUX_CODES & offered


async def test_fr_with_caps_shows_aux(reacher):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    assert reacher.get_firmware_caps() == {"aux"}
    assert AUX_CODES <= await _offered("fr", reacher)


async def test_fr_lite_never_shows_aux_even_with_caps(reacher):
    reacher.update_firmware_information({**IDN_FR_AUX, "sketch": "fr_lite.ino"})
    assert not AUX_CODES & await _offered("fr_lite", reacher)


@pytest.mark.parametrize("paradigm", ["pr", "vi", "omission", "pavlovian"])
async def test_other_paradigms_never_show_aux(reacher, paradigm):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    assert not AUX_CODES & await _offered(paradigm, reacher)


async def test_caps_cleared_when_older_firmware_identifies(reacher):
    """A reflash to beta.9 fr on the same connection must not inherit the old caps."""
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.update_firmware_information(dict(IDN_FR_OLD))
    assert reacher.get_firmware_caps() == set()
    assert not AUX_CODES & await _offered("fr", reacher)


async def test_caps_survive_controller_param_rows(reacher):
    """CONTROLLER param acks (no ``sketch``) merge into firmware info; they are not an IDN."""
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.update_firmware_information({"level": "000", "device": "CONTROLLER", "param": "ratio", "value": 3})
    assert reacher.get_firmware_caps() == {"aux"}


async def test_caps_cleared_on_reset(reacher):
    reacher.update_firmware_information(dict(IDN_FR_AUX))
    reacher.reset()
    assert reacher.get_firmware_caps() == set()


async def test_instance_without_caps_getter_hides_aux():
    assert not AUX_CODES & await _offered("fr", SimpleNamespace())


@pytest.mark.parametrize(("paradigm", "has_caps"), [("fr", True), ("fr_lite", False), ("pr", False)])
def test_simulated_idn_advertises_caps_only_on_fr(paradigm, has_caps):
    q = queue.Queue()
    FirmwareSimulator(q, paradigm=paradigm, seed=1, realtime=False).handle_command({"cmd": 102})
    idn = json.loads(q.get_nowait())
    assert idn["device"] == "CONTROLLER" and "sketch" in idn
    assert (idn.get("caps") == "aux") is has_caps
