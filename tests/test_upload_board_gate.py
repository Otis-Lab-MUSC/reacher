"""Upload gate: a Uno can only be flashed with a ``_lite`` paradigm.

``mega`` + ``*_lite`` is deliberately not gated (today: no ``hex/mega/*_lite.hex``
ships, so the upload answers 404 from the hex lookup). This file pins that
behaviour; it is not an endorsement of it.
"""

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.commands import LITE_PARADIGMS
from reacher.uploader.uploader import SIM_UPLOAD_FAIL_ENV, SIM_UPLOAD_SECONDS_ENV

AUTH = {"Authorization": f"Bearer {API_KEY}"}
FULL = ("fr", "pr", "vi", "omission", "pavlovian")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(SIM_UPLOAD_SECONDS_ENV, "0")
    monkeypatch.delenv(SIM_UPLOAD_FAIL_ENV, raising=False)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sid(client):
    resp = client.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": "fr"}, headers=AUTH)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    assert client.post(f"/api/serial/{sid}/connect", json={}, headers=AUTH).status_code == 200
    yield sid
    client.delete(f"/api/sessions/{sid}", headers=AUTH)


def _upload(client, sid, paradigm, board):
    return client.post(f"/api/firmware/upload/{sid}", json={"paradigm": paradigm, "board": board}, headers=AUTH)


@pytest.mark.parametrize("paradigm", FULL)
def test_uno_with_full_paradigm_is_rejected(client, sid, paradigm):
    resp = _upload(client, sid, paradigm, "uno")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    for lite in LITE_PARADIGMS:
        assert lite in detail


def test_uno_rejection_is_case_insensitive_on_board(client, sid):
    assert _upload(client, sid, "fr", "UNO").status_code == 400


def test_uno_gate_runs_before_session_lookup(client):
    assert _upload(client, "nosuchsession", "fr", "uno").status_code == 400


@pytest.mark.parametrize("paradigm", LITE_PARADIGMS)
def test_uno_with_lite_paradigm_is_accepted(client, sid, paradigm):
    assert _upload(client, sid, paradigm, "uno").status_code == 200


def test_mega_with_full_paradigm_is_accepted(client, sid):
    assert _upload(client, sid, "fr", "mega").status_code == 200


def test_mega_with_lite_paradigm_is_not_gated(client, sid):
    resp = _upload(client, sid, "fr_lite", "mega")
    assert resp.status_code == 404
