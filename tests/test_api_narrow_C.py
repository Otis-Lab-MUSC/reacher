"""Exception narrowing in the discovery, file and validate routers.

For each handler: a failure the guarded call can really raise is still handled,
and an unrelated exception (a bug) now surfaces instead of being swallowed.
"""

import sys
import zipfile
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import reacher.api.routers.file as file_router
from reacher import machines
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    """TestClient over an app whose REACHER instances are mocks."""
    with patch("reacher.session_manager.REACHER") as mock_reacher, patch("os.makedirs"):
        instance = Mock()
        instance.program_running = False
        instance.ser = Mock()
        instance.ser.is_open = False
        mock_reacher.return_value = instance
        with TestClient(create_app()) as c:
            yield c


@pytest.fixture
def paired(tmp_path, monkeypatch):
    """One paired machine in an isolated machines store."""
    monkeypatch.setattr(machines, "_FILE", str(tmp_path / "machines.json"))
    monkeypatch.setattr(machines, "_DIR", str(tmp_path))
    monkeypatch.setattr(machines, "_cache", {})
    machines.upsert("dev-1", "http://10.0.0.9:6229", "key", "pi", "Pi")
    return "dev-1"


# --------------------------------------------------------------------------- #
# discovery: health probe in /manual, /pair-by-url, /register
# --------------------------------------------------------------------------- #

_PROBES = [
    ("/api/discovery/manual", {"url": "http://10.0.0.9:6229", "api_key": "k"}, AUTH),
    ("/api/discovery/pair-by-url", {"url": "http://10.0.0.9:6229", "code": "123456"}, AUTH),
    ("/api/discovery/register", {"device_id": "x", "url": "http://10.0.0.9:6229", "hostname": "h"}, {}),
]
_PROBE_FAILURES = [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.InvalidURL("bad url"),
    ValueError("body is not JSON"),
]


@pytest.mark.parametrize("path,payload,headers", _PROBES)
@pytest.mark.parametrize("failure", _PROBE_FAILURES, ids=lambda e: type(e).__name__)
def test_probe_failure_is_a_400(client, path, payload, headers, failure):
    """A network or JSON failure while probing /health is reported as 400."""
    client.app.state.http_client.get = AsyncMock(side_effect=failure)
    resp = client.post(path, json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot reach device at that URL"


@pytest.mark.parametrize("path,payload,headers", _PROBES)
def test_probe_bug_surfaces(client, path, payload, headers):
    """An error outside the probe's failure set is not relabelled as an unreachable device."""
    client.app.state.http_client.get = AsyncMock(side_effect=RuntimeError("bug in client"))
    with pytest.raises(RuntimeError, match="bug in client"):
        client.post(path, json=payload, headers=headers)


# --------------------------------------------------------------------------- #
# discovery: /pair-by-code best-effort per-peer claim
# --------------------------------------------------------------------------- #


@pytest.fixture
def one_peer(monkeypatch):
    """A single unpaired mDNS-style peer."""
    peers = {"peer-1": {"host": "10.0.0.5", "port": 6229, "hostname": "pi-5"}}
    monkeypatch.setattr("reacher.api.routers.discovery.discovery.get_peers", lambda: peers)
    monkeypatch.setattr("reacher.api.routers.discovery.machines.get_all", lambda: {})


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), KeyError("api_key"), ValueError("not json")])
def test_pair_by_code_skips_unreachable_peer(client, one_peer, failure):
    """A peer that cannot be reached, or answers 200 with no key, is skipped (401: no match)."""
    client.app.state.http_client.post = AsyncMock(side_effect=failure)
    resp = client.post("/api/discovery/pair-by-code", json={"code": "123456"}, headers=AUTH)
    assert resp.status_code == 401


def test_pair_by_code_bug_surfaces(client, one_peer):
    """A bug while claiming on a peer is not silently treated as 'peer declined'."""
    client.app.state.http_client.post = AsyncMock(side_effect=RuntimeError("bug in claim"))
    with pytest.raises(RuntimeError, match="bug in claim"):
        client.post("/api/discovery/pair-by-code", json={"code": "123456"}, headers=AUTH)


# --------------------------------------------------------------------------- #
# discovery: unpair notification is best effort
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), httpx.ReadTimeout("slow")])
def test_remove_machine_survives_unreachable_remote(client, paired, failure):
    """An offline remote device does not stop the local entry being removed."""
    client.app.state.http_client.post = AsyncMock(side_effect=failure)
    resp = client.delete(f"/api/discovery/{paired}", headers=AUTH)
    assert resp.status_code == 200
    assert machines.get(paired) is None


def test_remove_machine_bug_surfaces(client, paired):
    """A bug in the unpair call is not hidden behind the 'may be offline' warning."""
    client.app.state.http_client.post = AsyncMock(side_effect=RuntimeError("bug in unpair"))
    with pytest.raises(RuntimeError, match="bug in unpair"):
        client.delete(f"/api/discovery/{paired}", headers=AUTH)


# --------------------------------------------------------------------------- #
# file: folder picker
# --------------------------------------------------------------------------- #


@pytest.fixture
def no_zenity(monkeypatch):
    """Skip the zenity branch so the tkinter picker is the one exercised."""
    monkeypatch.setattr(sys, "platform", "win32")


def test_browse_without_display_returns_null(client, no_zenity):
    """A host with no display (TclError) answers path=None."""
    import tkinter

    with patch("tkinter.Tk", side_effect=tkinter.TclError("no display name")):
        resp = client.get("/api/file/browse", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"path": None}


def test_browse_bug_surfaces(client, no_zenity):
    """An unrelated error from the picker is not reported as 'no display'."""
    with patch("tkinter.Tk", side_effect=KeyError("bug")):
        with pytest.raises(KeyError):
            client.get("/api/file/browse", headers=AUTH)


# --------------------------------------------------------------------------- #
# file: create_folder
# --------------------------------------------------------------------------- #


def _session(client):
    resp = client.post("/api/sessions", json={"port": "/dev/ttyUSB0"}, headers=AUTH)
    sid = resp.json()["session_id"]
    return sid, client.app.state.session_manager.get_instance(sid)


def test_create_folder_os_error_is_a_500(client):
    """A filesystem failure while making the folder is a 500 with the stable detail."""
    sid, instance = _session(client)
    instance.make_destination_folder.side_effect = PermissionError("read-only")
    resp = client.post(f"/api/file/{sid}/create_folder", headers=AUTH)
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Failed to create folder"


def test_create_folder_bug_surfaces(client):
    """An unrelated error is not flattened into the generic folder-creation 500."""
    sid, instance = _session(client)
    instance.make_destination_folder.side_effect = RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        client.post(f"/api/file/{sid}/create_folder", headers=AUTH)


# --------------------------------------------------------------------------- #
# file: best-effort ZIP members
# --------------------------------------------------------------------------- #


def _export_ready(client, tmp_path):
    """Create a session whose mock instance can complete an export; return (sid, instance)."""
    sid, instance = _session(client)
    folder = tmp_path / "out"
    folder.mkdir()
    instance.get_filename.return_value = "run"
    instance.get_data_destination.return_value = str(tmp_path)
    instance.make_destination_folder.return_value = str(folder)
    instance.get_behavior_data.return_value = []
    instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2"}
    instance.get_hardware_settings.return_value = []
    instance.get_frame_data.return_value = []
    instance.get_slm_data.return_value = []
    instance.get_total_infusion_count.return_value = 0
    instance.get_total_press_count.return_value = 0
    instance.get_total_trial_count.return_value = 0
    instance.get_infusions_by_pump.return_value = {"PUMP_1": 0}
    instance.get_counted_pumps.return_value = ["PUMP_1"]
    instance.get_segment_exports.return_value = []
    instance.get_segment_event_counts.return_value = []
    instance.get_session_summary.return_value = {"ok": True}
    instance.get_event_log_path.return_value = str(tmp_path / "missing.jsonl")
    return sid, instance


def _names(resp):
    assert resp.status_code == 200
    with zipfile.ZipFile(resp.json()["file_path"]) as zf:
        return zf.namelist()


def test_export_omits_summary_when_it_cannot_be_encoded(client, tmp_path):
    """A summary json cannot serialise drops that member and keeps the export."""
    sid, instance = _export_ready(client, tmp_path)
    instance.get_session_summary.return_value = {"bad": {1, 2}}
    names = _names(client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH))
    assert "session_summary.json" not in names
    assert "metadata.json" in names


def test_export_summary_bug_surfaces(client, tmp_path):
    """A bug building the summary is not hidden behind a quietly shorter archive."""
    sid, instance = _export_ready(client, tmp_path)
    instance.get_session_summary.side_effect = RuntimeError("bug in summary")
    with pytest.raises(RuntimeError, match="bug in summary"):
        client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH)


def test_export_omits_event_log_on_os_error(client, tmp_path):
    """An unreadable event log drops that member and keeps the export."""
    sid, instance = _export_ready(client, tmp_path)
    instance.flush_event_log.side_effect = OSError("disk gone")
    names = _names(client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH))
    assert "event_log.jsonl" not in names
    assert "metadata.json" in names


def test_export_event_log_bug_surfaces(client, tmp_path):
    """A bug flushing the event log is not hidden behind a quietly shorter archive."""
    sid, instance = _export_ready(client, tmp_path)
    instance.flush_event_log.side_effect = RuntimeError("bug in flush")
    with pytest.raises(RuntimeError, match="bug in flush"):
        client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH)


def test_export_omits_xlsx_on_illegal_cell_text(client, tmp_path):
    """Control characters in a behavior row drop the workbook and keep the CSV."""
    sid, instance = _export_ready(client, tmp_path)
    instance.get_behavior_data.return_value = [
        {"device": "\x07", "event": "press", "start_timestamp": 1, "end_timestamp": 2}
    ]
    names = _names(client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH))
    assert "behavior_events.xlsx" not in names
    assert "behavior_events.csv" in names


def test_export_xlsx_bug_surfaces(client, tmp_path):
    """A bug building the workbook is not hidden behind a quietly shorter archive."""
    sid, _instance = _export_ready(client, tmp_path)
    with patch.object(file_router, "build_behavior_workbook", side_effect=RuntimeError("bug in xlsx")):
        with pytest.raises(RuntimeError, match="bug in xlsx"):
            client.post(f"/api/file/{sid}/export/zip", json={}, headers=AUTH)


# --------------------------------------------------------------------------- #
# validate: the rule engine never blocks session start on bad input
# --------------------------------------------------------------------------- #


def test_validate_malformed_values_return_empty_warnings(client):
    """A value of the wrong type makes a rule raise TypeError; the answer is still 200/valid."""
    body = {"limitSettings": {"limitType": "Time", "timeLimit": "ten minutes"}}
    resp = client.post("/api/validate/config", json=body, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "warnings": [], "suggestions": ""}


def test_validate_non_dict_section_returns_empty_warnings(client):
    """A hardware entry that is a list, not an object, raises AttributeError; still 200/valid."""
    body = {"paradigm": "fr", "paradigmSettings": {"ratio": 5}, "hardwareUi": {"rhLever": [1, 2]}}
    resp = client.post("/api/validate/config", json=body, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["warnings"] == []


def test_validate_rule_engine_bug_surfaces(client):
    """A rule-engine defect is not reported as a clean 'valid' config."""
    with patch("reacher.api.routers.validate.run_validation", side_effect=RuntimeError("bug")):
        with pytest.raises(RuntimeError, match="bug"):
            client.post("/api/validate/config", json={}, headers=AUTH)
