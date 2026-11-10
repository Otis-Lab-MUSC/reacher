"""Behaviour of ``/api/update/*`` with the network, the signing key and process spawning replaced."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.api.routers import update as update_router
from reacher.update import download, handoff
from reacher.update.releases import Release, ReleaseAsset, ReleaseFetchError
from reacher.update.state import UpdateState, UpdateStatus
from reacher.update.versions import parse_version
from tests.sim_harness import stub_discovery, wait_until
from tests.test_update_download import ASSET, BASE, Release as FakeRelease

AUTH = {"Authorization": f"Bearer {API_KEY}"}
CURRENT = "3.0.1-beta.16"
LATEST_TAG = "v3.0.1-beta.17"


class Rig:
    """The client, the fake release server and the mocks a router test needs."""

    def __init__(self, client: TestClient, served: FakeRelease, tmp_path: Path) -> None:
        self.client = client
        self.served = served
        self.install_dir = tmp_path / "apps" / "Labrynth"
        self.install_dir.mkdir(parents=True)
        self.spawn = Mock()
        self.shutdown = Mock()
        self.release_list: list[Release] = [self.make_release()]
        self.list_error: Exception | None = None

    def make_release(self, names: tuple[str, ...] | None = None) -> Release:
        listed = names if names is not None else (ASSET, "SHA256SUMS", "SHA256SUMS.sig")
        assets = tuple(ReleaseAsset(name, BASE + name) for name in listed)
        return Release(LATEST_TAG, parse_version(LATEST_TAG), True, assets)

    def state(self) -> UpdateState:
        return update_router.get_update_state(self.client.app)

    def wait_for(self, status: UpdateStatus) -> UpdateState:
        wait_until(lambda: self.state().status is status, timeout=5.0)
        return self.state()


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    stub_discovery(monkeypatch)
    served = FakeRelease()
    monkeypatch.setattr(download, "load_release_public_key", lambda: served.key.public_key())
    monkeypatch.setenv("LABRYNTH_APP_VERSION", CURRENT)
    with TestClient(create_app()) as client:
        client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(served.handler))
        built = Rig(client, served, tmp_path)

        async def fake_fetch(http_client: httpx.AsyncClient, url: str) -> list[Release]:
            if built.list_error is not None:
                raise built.list_error
            return built.release_list

        monkeypatch.setattr(update_router, "fetch_releases", fake_fetch)
        monkeypatch.setattr(update_router, "current_platform", lambda: ("linux", "x86_64"))
        monkeypatch.setattr(update_router, "find_install_dir", lambda: built.install_dir)
        monkeypatch.setattr(update_router, "spawn_helper", built.spawn)
        monkeypatch.setattr(update_router, "trigger_shutdown", built.shutdown)
        yield built


ROUTES = [
    ("GET", "/api/update/info"),
    ("POST", "/api/update/download"),
    ("GET", "/api/update/status"),
    ("POST", "/api/update/launch"),
]


@pytest.mark.parametrize("method, path", ROUTES)
def test_update_routes_require_the_api_key(rig: Rig, method: str, path: str):
    assert rig.client.request(method, path).status_code == 401
    rig.spawn.assert_not_called()


def test_info_reports_an_available_update(rig: Rig):
    body = rig.client.get("/api/update/info", headers=AUTH).json()
    assert body["currentVersion"] == CURRENT
    assert body["latestVersion"] == "3.0.1-beta.17"
    assert body["updateAvailable"] is True
    assert body["channel"] == "prerelease"
    assert body["assetName"] == ASSET
    assert body["assetUrl"] == BASE + ASSET
    assert body["sha256Url"] == BASE + "SHA256SUMS"
    assert body["applyMode"] == "restart"


def test_an_appimage_is_told_to_update_by_hand(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_router, "find_install_dir", handoff.find_install_dir)
    monkeypatch.setattr(handoff.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", str(rig.install_dir.parent / "labrynth.AppImage"))
    assert rig.client.get("/api/update/info", headers=AUTH).json()["applyMode"] == "manual"
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 409
    assert not list(rig.install_dir.parent.glob(".labrynth-update_*"))


def test_info_reports_no_update_when_current(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LABRYNTH_APP_VERSION", "3.0.1-beta.17")
    body = rig.client.get("/api/update/info", headers=AUTH).json()
    assert body["updateAvailable"] is False
    assert body["assetName"] is None


def test_info_with_a_release_missing_its_signature_offers_no_asset(rig: Rig):
    rig.release_list = [rig.make_release((ASSET, "SHA256SUMS"))]
    body = rig.client.get("/api/update/info", headers=AUTH).json()
    assert body["updateAvailable"] is True
    assert body["assetName"] is None
    assert body["assetUrl"] is None


def test_info_outside_a_bundle_says_apply_is_manual(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_router, "find_install_dir", lambda: None)
    assert rig.client.get("/api/update/info", headers=AUTH).json()["applyMode"] == "manual"


def test_info_honours_the_stable_channel(rig: Rig):
    body = rig.client.get("/api/update/info?channel=stable", headers=AUTH).json()
    assert body["channel"] == "stable"
    assert body["updateAvailable"] is False


def test_info_rejects_an_unknown_channel(rig: Rig):
    assert rig.client.get("/api/update/info?channel=nightly", headers=AUTH).status_code == 422


def test_info_maps_an_unreachable_release_list_to_503(rig: Rig):
    rig.list_error = ReleaseFetchError("down")
    assert rig.client.get("/api/update/info", headers=AUTH).status_code == 503


def test_status_is_idle_before_any_download(rig: Rig):
    body = rig.client.get("/api/update/status", headers=AUTH).json()
    assert body["status"] == "idle"
    assert body["verified"] is False


def test_download_stages_and_verifies_the_update(rig: Rig):
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 200
    state = rig.wait_for(UpdateStatus.READY)
    assert state.verified is True
    assert state.staged_dir is not None
    assert state.staged_dir.parent.parent.parent == rig.install_dir.parent
    body = rig.client.get("/api/update/status", headers=AUTH).json()
    assert body["status"] == "ready" and body["verified"] is True and body["percent"] == 100
    assert str(rig.install_dir.parent) not in str(body)


def test_download_ignores_a_url_in_the_request_body(rig: Rig):
    body = {"assetUrl": "http://evil.test/x.tar.gz", "assetName": "x.tar.gz"}
    assert rig.client.post("/api/update/download", headers=AUTH, json=body).status_code == 200
    rig.wait_for(UpdateStatus.READY)
    assert all("evil" not in name for name in rig.served.requests)


def test_download_with_a_bad_hash_ends_in_error_and_never_launches(rig: Rig):
    rig.served.files[ASSET] = rig.served.archive + b"tampered"
    rig.client.post("/api/update/download", headers=AUTH)
    state = rig.wait_for(UpdateStatus.ERROR)
    assert state.verified is False
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 400
    rig.spawn.assert_not_called()


def test_download_with_no_update_is_409(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LABRYNTH_APP_VERSION", "3.0.1-beta.17")
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 409


def test_download_outside_a_bundle_is_409(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_router, "find_install_dir", lambda: None)
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 409


def test_a_second_download_while_one_runs_is_409(rig: Rig):
    rig.state().begin(ASSET, rig.install_dir.parent / "busy")
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 409


def test_download_after_an_error_starts_again(rig: Rig):
    rig.state().mark_failed("old failure")
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 200
    assert rig.wait_for(UpdateStatus.READY).error is None


def test_download_maps_an_unreachable_release_list_to_503(rig: Rig):
    rig.list_error = ReleaseFetchError("down")
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 503


def make_ready(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(sys, "executable", str(rig.install_dir / "Labrynth"))
    staging = rig.install_dir.parent / ".stage"
    staged = staging / "unpacked" / "Labrynth"
    staged.mkdir(parents=True)
    (staged / "Labrynth").write_text("launcher")
    rig.state().begin(ASSET, staging)
    rig.state().mark_ready(staged)
    return staged


def test_launch_without_a_ready_update_is_400(rig: Rig):
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 400
    rig.spawn.assert_not_called()


def test_launch_hands_the_staged_folder_to_the_launcher(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    staged = make_ready(rig, monkeypatch)
    assert rig.client.post("/api/update/launch", headers=AUTH).json() == {"status": "launching"}
    command = rig.spawn.call_args.args[0]
    assert command[0] == str(staged / "Labrynth")
    assert command[1:4] == ["--apply-update", "--staged", str(staged)]
    assert command[command.index("--install") + 1] == str(rig.install_dir)
    assert "--relaunch" in command and command[command.index("--pid") + 1].isdigit()
    assert rig.state().status is UpdateStatus.LAUNCHING


def test_launch_then_shuts_the_backend_down(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_router, "SHUTDOWN_DELAY_SECONDS", 0.05)
    make_ready(rig, monkeypatch)
    rig.client.post("/api/update/launch", headers=AUTH)
    wait_until(lambda: rig.shutdown.called, timeout=4.0)
    rig.shutdown.assert_called_once()


@pytest.mark.parametrize("state_name", ["running", "paused", "uploading"])
def test_launch_is_refused_during_a_session(rig: Rig, monkeypatch: pytest.MonkeyPatch, state_name: str):
    make_ready(rig, monkeypatch)
    manager = rig.client.app.state.session_manager
    monkeypatch.setattr(manager, "list_sessions", lambda: [{"session_id": "s", "state": state_name}])
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 409
    rig.spawn.assert_not_called()


def test_launch_allows_idle_and_connected_sessions(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    make_ready(rig, monkeypatch)
    manager = rig.client.app.state.session_manager
    monkeypatch.setattr(manager, "list_sessions", lambda: [{"state": "connected"}, {"state": "idle"}])
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 200


def test_launch_refuses_when_the_staged_folder_is_gone(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    staged = make_ready(rig, monkeypatch)
    shutil.rmtree(staged)
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 400
    rig.spawn.assert_not_called()


def test_launch_reports_a_spawn_failure_and_does_not_shut_down(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_router, "SHUTDOWN_DELAY_SECONDS", 0.05)
    make_ready(rig, monkeypatch)
    rig.spawn.side_effect = OSError("no such file")
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 500
    rig.shutdown.assert_not_called()
    assert rig.state().status is UpdateStatus.READY


def test_an_unexpected_crash_in_the_background_task_ends_in_error(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(update_router, "stage_update", explode)
    assert rig.client.post("/api/update/download", headers=AUTH).status_code == 200
    state = rig.wait_for(UpdateStatus.ERROR)
    assert "boom" in (state.error or "")
    assert state.busy is False


def test_download_removes_the_previous_staging_folder(rig: Rig):
    rig.client.post("/api/update/download", headers=AUTH)
    first = rig.wait_for(UpdateStatus.READY).staging_root
    assert first is not None and first.is_dir()
    rig.client.post("/api/update/download", headers=AUTH)
    wait_until(lambda: not first.exists(), timeout=5.0)


def test_a_second_launch_while_launching_is_refused(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    make_ready(rig, monkeypatch)
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 200
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 400
    assert rig.spawn.call_count == 1


def test_launch_refuses_when_the_running_executable_is_outside_the_install_folder(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
):
    make_ready(rig, monkeypatch)
    monkeypatch.setattr(sys, "executable", str(rig.install_dir.parent / "elsewhere" / "python"))
    assert rig.client.post("/api/update/launch", headers=AUTH).status_code == 409
    rig.spawn.assert_not_called()
