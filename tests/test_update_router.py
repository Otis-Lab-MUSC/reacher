"""Behaviour of ``/api/update/*`` with all network and process execution patched.

The router downloads a platform installer from GitHub and launches it. Here the
HTTP client on ``app.state`` is a fake, ``tempfile.mkdtemp`` lands under
``tmp_path``, and every launch goes through a mock, so nothing is fetched and
nothing is executed. The one exception is ``_launch`` itself, which is called
directly on Linux with ``subprocess.Popen`` mocked.
"""

import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import reacher
import reacher.api.routers.websocket as websocket_router
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.api.routers import update as update_router
from tests.sim_harness import stub_discovery, wait_until

AUTH = {"Authorization": f"Bearer {API_KEY}"}
LATEST_URL = "https://api.github.com/repos/Otis-Lab-MUSC/labrynth/releases/latest"


class _FakeStream:
    def __init__(self, status_code, headers, chunks):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size=8192):
        for chunk in self._chunks:
            yield chunk


class FakeGitHub:
    """Stands in for ``app.state.http_client``; records every call."""

    def __init__(self):
        self.get_response = None
        self.get_exc = None
        self.stream_status = 200
        self.stream_headers = {}
        self.stream_chunks = []
        self.get_calls = []
        self.stream_calls = []

    async def get(self, url, **kwargs):
        self.get_calls.append(url)
        if self.get_exc is not None:
            raise self.get_exc
        return self.get_response

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url))
        yield _FakeStream(self.stream_status, self.stream_headers, self.stream_chunks)


def _gh_response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", LATEST_URL))


@pytest.fixture
def client(monkeypatch):
    stub_discovery(monkeypatch)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def github(client):
    fake = FakeGitHub()
    client.app.state.http_client = fake
    return fake


@pytest.fixture(autouse=True)
def _clean_download_state(tmp_path, monkeypatch):
    """The download job is module-level state; reset it around every test and keep temp dirs in tmp_path."""
    update_router._reset_download_state()
    counter = iter(range(1000))

    def fake_mkdtemp(prefix="tmp", **kwargs):
        path = tmp_path / f"{prefix}{next(counter)}"
        path.mkdir()
        return str(path)

    monkeypatch.setattr(update_router.tempfile, "mkdtemp", fake_mkdtemp)
    yield
    update_router._reset_download_state()


@pytest.fixture
def launcher(monkeypatch):
    """Mocked ``_launch`` plus a mocked graceful-shutdown trigger, so nothing real runs."""
    mock_launch = Mock()
    monkeypatch.setattr(update_router, "_launch", mock_launch)
    monkeypatch.setattr(websocket_router, "_trigger_shutdown", Mock())
    return mock_launch


def _ready_installer(tmp_path, name="reacher-linux-x64.AppImage"):
    path = tmp_path / "ready" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"installer")
    update_router._download_state.update(status="ready", percent=100, local_path=str(path), asset_name=name)
    return path


# ---------------------------------------------------------------------------
# Auth: every update route is behind the bearer gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/update/info"),
        ("POST", "/api/update/download"),
        ("GET", "/api/update/status"),
        ("POST", "/api/update/launch"),
    ],
)
def test_update_routes_require_api_key(client, github, launcher, method, path):
    resp = client.request(method, path, json={"assetUrl": "u", "assetName": "n"})

    assert resp.status_code == 401
    assert github.get_calls == [] and github.stream_calls == []
    launcher.assert_not_called()


# ---------------------------------------------------------------------------
# GET /info
# ---------------------------------------------------------------------------


def test_info_reports_versions_and_the_resolved_asset(client, github, monkeypatch):
    github.get_response = _gh_response({"tag_name": "v3.5.0", "assets": []})
    asset = {"name": "labrynth-linux-x64.AppImage", "browser_download_url": "https://dl/x"}
    monkeypatch.setattr(update_router, "_resolve_asset", lambda assets: asset)

    resp = client.get("/api/update/info", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {
        "currentVersion": reacher.__version__,
        "latestVersion": "3.5.0",
        "assetUrl": "https://dl/x",
        "assetName": "labrynth-linux-x64.AppImage",
    }
    assert github.get_calls == [LATEST_URL]


def test_info_without_a_matching_asset_returns_null_asset_fields(client, github, monkeypatch):
    github.get_response = _gh_response({"tag_name": "v3.5.0", "assets": [{"name": "notes.txt"}]})
    monkeypatch.setattr(update_router, "_resolve_asset", lambda assets: None)

    resp = client.get("/api/update/info", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["assetUrl"] is None
    assert resp.json()["assetName"] is None
    assert resp.json()["latestVersion"] == "3.5.0"


def test_info_maps_unreachable_github_to_503(client, github):
    github.get_exc = httpx.ConnectError("no route to host")

    resp = client.get("/api/update/info", headers=AUTH)

    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("GitHub unreachable")


def test_info_maps_github_error_status_to_503(client, github):
    github.get_response = _gh_response({"message": "rate limited"}, status=403)

    resp = client.get("/api/update/info", headers=AUTH)

    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("GitHub unreachable")


# ---------------------------------------------------------------------------
# Asset resolution (pure function; platform pinned via monkeypatch)
# ---------------------------------------------------------------------------

ASSETS = [
    {"name": "labrynth-3.5.0-windows-x64.exe"},
    {"name": "labrynth-3.5.0-macos-x86_64.dmg"},
    {"name": "labrynth-3.5.0-macos-arm64.dmg"},
    {"name": "labrynth_3.5.0_amd64.deb"},
    {"name": "labrynth-3.5.0-linux-x64.tar.gz"},
    {"name": "labrynth-3.5.0-linux-x64.AppImage"},
    {"name": "labrynth-3.5.0-linux-arm64.tar.gz"},
]


@pytest.mark.parametrize(
    "platform_name, machine, expected",
    [
        ("win32", "amd64", "labrynth-3.5.0-windows-x64.exe"),
        ("darwin", "arm64", "labrynth-3.5.0-macos-arm64.dmg"),
        pytest.param(
            "darwin",
            "x86_64",
            "labrynth-3.5.0-macos-x86_64.dmg",
            marks=pytest.mark.xfail(
                strict=True,
                reason="darwin tries the arm64 DMG first whatever the machine, so Intel Macs get the arm64 build",
            ),
        ),
        ("linux", "x86_64", "labrynth_3.5.0_amd64.deb"),
        ("linux", "aarch64", "labrynth-3.5.0-linux-arm64.tar.gz"),
    ],
)
def test_resolve_asset_prefers_the_platform_installer(monkeypatch, platform_name, machine, expected):
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(update_router.platform, "machine", lambda: machine)

    assert update_router._resolve_asset(ASSETS)["name"] == expected


def test_resolve_asset_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(update_router.platform, "machine", lambda: "amd64")

    assert update_router._resolve_asset([{"name": "labrynth-linux-x64.AppImage"}]) is None


# ---------------------------------------------------------------------------
# POST /download and GET /status
# ---------------------------------------------------------------------------


def test_status_is_idle_before_any_download(client):
    resp = client.get("/api/update/status", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"status": "idle", "percent": 0, "local_path": None, "asset_name": None, "error": None}


def test_download_rejects_a_body_missing_fields(client):
    resp = client.post("/api/update/download", json={"assetUrl": "https://dl/x"}, headers=AUTH)

    assert resp.status_code == 422


def test_download_streams_the_asset_and_reports_ready(client, github):
    github.stream_headers = {"content-length": "6"}
    github.stream_chunks = [b"abc", b"def"]

    resp = client.post(
        "/api/update/download",
        json={"assetUrl": "https://dl/reacher.AppImage", "assetName": "reacher.AppImage"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "started"}

    wait_until(lambda: client.get("/api/update/status", headers=AUTH).json()["status"] != "downloading")
    body = client.get("/api/update/status", headers=AUTH).json()
    assert body["status"] == "ready", body
    assert body["percent"] == 100
    assert body["asset_name"] == "reacher.AppImage"
    assert body["error"] is None
    assert github.stream_calls == [("GET", "https://dl/reacher.AppImage")]
    with open(body["local_path"], "rb") as fh:
        assert fh.read() == b"abcdef"


def test_download_upstream_failure_sets_error_state(client, github):
    github.stream_status = 404

    client.post(
        "/api/update/download",
        json={"assetUrl": "https://dl/gone", "assetName": "gone.AppImage"},
        headers=AUTH,
    )

    wait_until(lambda: client.get("/api/update/status", headers=AUTH).json()["status"] == "error")
    body = client.get("/api/update/status", headers=AUTH).json()
    assert "404" in body["error"]
    assert body["local_path"] is None


def test_download_while_one_is_running_is_409(client, github, monkeypatch):
    monkeypatch.setitem(update_router._download_state, "status", "downloading")

    resp = client.post("/api/update/download", json={"assetUrl": "u", "assetName": "n"}, headers=AUTH)

    assert resp.status_code == 409
    assert github.stream_calls == []


@pytest.mark.xfail(
    strict=True,
    reason="assetName is joined into the temp path unchecked, so '../' escapes the download dir",
)
def test_download_asset_name_cannot_escape_the_temp_dir(client, github):
    github.stream_headers = {"content-length": "1"}
    github.stream_chunks = [b"x"]

    client.post("/api/update/download", json={"assetUrl": "u", "assetName": "../escaped.AppImage"}, headers=AUTH)

    wait_until(lambda: client.get("/api/update/status", headers=AUTH).json()["status"] != "downloading")
    body = client.get("/api/update/status", headers=AUTH).json()
    temp_dir = os.path.normpath(update_router._download_temp_dir)
    assert body["status"] == "error" or os.path.normpath(body["local_path"]).startswith(temp_dir + os.sep)


# ---------------------------------------------------------------------------
# POST /launch
# ---------------------------------------------------------------------------


def test_launch_without_a_download_is_400(client, launcher):
    resp = client.post("/api/update/launch", headers=AUTH)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "No installer ready"
    launcher.assert_not_called()


def test_launch_refuses_a_recorded_path_that_is_gone(client, launcher, tmp_path):
    update_router._download_state.update(status="ready", local_path=str(tmp_path / "vanished.AppImage"))

    resp = client.post("/api/update/launch", headers=AUTH)

    assert resp.status_code == 400
    launcher.assert_not_called()


def test_launch_refuses_a_directory_in_place_of_the_installer(client, launcher, tmp_path):
    update_router._download_state.update(status="ready", local_path=str(tmp_path))

    resp = client.post("/api/update/launch", headers=AUTH)

    assert resp.status_code == 400
    launcher.assert_not_called()


def test_launch_hands_the_ready_installer_to_the_launcher(client, launcher, tmp_path):
    installer = _ready_installer(tmp_path)

    resp = client.post("/api/update/launch", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"status": "launching"}
    launcher.assert_called_once_with(str(installer))


def test_launch_surfaces_a_launcher_failure_as_500(client, launcher, tmp_path):
    _ready_installer(tmp_path)
    launcher.side_effect = RuntimeError("pkexec missing")

    resp = client.post("/api/update/launch", headers=AUTH)

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Failed to launch installer: pkexec missing"


def test_launch_passes_a_manual_install_refusal_through(client, launcher, tmp_path):
    _ready_installer(tmp_path, name="reacher-linux-x64.tar.gz")
    launcher.side_effect = HTTPException(status_code=202, detail="Manual install required. Archive saved to: x")

    resp = client.post("/api/update/launch", headers=AUTH)

    assert resp.status_code == 202
    assert resp.json()["detail"].startswith("Manual install required")


def test_launch_schedules_graceful_shutdown_after_two_seconds(client, launcher, tmp_path, monkeypatch):
    shutdown = Mock()
    monkeypatch.setattr(websocket_router, "_trigger_shutdown", shutdown)
    _ready_installer(tmp_path)

    client.post("/api/update/launch", headers=AUTH)

    wait_until(lambda: shutdown.called, timeout=4.0)
    shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# _launch dispatch (Linux branch; Popen mocked, so nothing executes)
# ---------------------------------------------------------------------------

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="exercises the Linux branch of _launch")


@linux_only
def test_launch_deb_goes_through_pkexec_dpkg(monkeypatch, tmp_path):
    popen = Mock()
    monkeypatch.setattr(update_router.subprocess, "Popen", popen)
    deb = tmp_path / "labrynth_3.5.0_amd64.deb"

    update_router._launch(str(deb))

    assert popen.call_args.args[0] == ["pkexec", "dpkg", "-i", str(deb)]


@linux_only
def test_launch_appimage_is_made_executable_and_run(monkeypatch, tmp_path):
    popen = Mock()
    monkeypatch.setattr(update_router.subprocess, "Popen", popen)
    appimage = tmp_path / "labrynth-linux-x64.AppImage"
    appimage.write_bytes(b"\x7fELF")

    update_router._launch(str(appimage))

    assert appimage.stat().st_mode & 0o777 == 0o755
    assert popen.call_args.args[0] == [str(appimage)]


@linux_only
def test_launch_tarball_is_never_executed(monkeypatch, tmp_path):
    popen = Mock()
    monkeypatch.setattr(update_router.subprocess, "Popen", popen)
    archive = tmp_path / "labrynth-linux-x64.tar.gz"

    with pytest.raises(HTTPException) as exc:
        update_router._launch(str(archive))

    assert exc.value.status_code == 202
    assert str(archive) in exc.value.detail
    popen.assert_not_called()
