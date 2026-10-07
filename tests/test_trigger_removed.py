"""The external start trigger was removed end to end; these pin that it stays gone."""

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.commands import COMMAND_REGISTRY, CommandCode
from reacher.kernel.reacher import REACHER

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.parametrize("route", ["arm-trigger", "disarm-trigger"])
def test_trigger_routes_are_gone(route):
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/program/abc123/{route}", headers=AUTH_HEADER)
    assert resp.status_code == 404


def test_trigger_commands_are_gone():
    assert not [c.name for c in CommandCode if c.name.startswith("EXT_TRIGGER")]
    assert not {1200, 1201, 1276} & set(COMMAND_REGISTRY)


def test_kernel_has_no_trigger_api():
    assert not [n for n in dir(REACHER) if "external_trigger" in n]
