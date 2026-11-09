"""Download, verify and stage an update, with a throwaway signing key and no network."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from reacher.update import download
from reacher.update.download import DownloadPlan, stage_update
from reacher.update.releases import ReleaseAsset
from reacher.update.state import UpdateState, UpdateStatus

ASSET = "labrynth-3.0.1-beta.17-linux-x64.tar.gz"
BASE = "https://example.test/dl/"


def build_archive() -> bytes:
    """Return a tar.gz holding one top-level Labrynth folder."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        payload = b"#!/bin/sh\n"
        info = tarfile.TarInfo("Labrynth/Labrynth")
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class Release:
    """A fake release: an archive, its SHA256SUMS and a signature, served from memory."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.archive = build_archive()
        self.files: dict[str, bytes] = {}
        self.status: dict[str, int] = {}
        self.requests: list[str] = []
        self.set_archive(self.archive)

    def set_archive(self, archive: bytes, listed_hash: str | None = None) -> None:
        digest = listed_hash or hashlib.sha256(archive).hexdigest()
        sums = f"{digest}  {ASSET}\n".encode()
        self.files = {ASSET: archive, "SHA256SUMS": sums, "SHA256SUMS.sig": self.key.sign(sums)}

    def handler(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        self.requests.append(name)
        if name in self.status:
            return httpx.Response(self.status[name])
        if name not in self.files:
            return httpx.Response(404)
        return httpx.Response(200, content=self.files[name])

    def plan(self) -> DownloadPlan:
        return DownloadPlan(
            asset=ReleaseAsset(ASSET, BASE + ASSET),
            sums=ReleaseAsset("SHA256SUMS", BASE + "SHA256SUMS"),
            signature=ReleaseAsset("SHA256SUMS.sig", BASE + "SHA256SUMS.sig"),
        )


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> Release:
    fake = Release()
    monkeypatch.setattr(download, "load_release_public_key", lambda: fake.key.public_key())
    return fake


def run_stage(release: Release, tmp_path: Path) -> UpdateState:
    import asyncio

    state = UpdateState()
    staging = tmp_path / "staging"
    staging.mkdir()
    state.begin(ASSET, staging)

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(release.handler)) as client:
            await stage_update(client, release.plan(), state)

    asyncio.run(go())
    return state


def test_a_good_release_is_staged_and_marked_verified(release: Release, tmp_path: Path):
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.READY
    assert state.verified is True
    assert state.staged_dir is not None
    assert (state.staged_dir / "Labrynth").read_bytes() == b"#!/bin/sh\n"


def test_the_staged_folder_is_inside_the_staging_root(release: Release, tmp_path: Path):
    state = run_stage(release, tmp_path)
    assert state.staging_root is not None and state.staged_dir is not None
    assert state.staging_root in state.staged_dir.parents


def test_a_bad_signature_is_refused_and_the_archive_is_never_downloaded(release: Release, tmp_path: Path):
    release.files["SHA256SUMS.sig"] = b"\x00" * 64
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert state.verified is False
    assert ASSET not in release.requests


def test_a_signature_from_another_key_is_refused(release: Release, tmp_path: Path):
    other = Ed25519PrivateKey.generate()
    release.files["SHA256SUMS.sig"] = other.sign(release.files["SHA256SUMS"])
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert ASSET not in release.requests


def test_a_tampered_sums_file_is_refused(release: Release, tmp_path: Path):
    release.files["SHA256SUMS"] = release.files["SHA256SUMS"].replace(b"a", b"b", 1) + b"\n"
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR


def test_an_archive_that_does_not_match_the_signed_hash_is_refused(release: Release, tmp_path: Path):
    release.files[ASSET] = release.archive + b"tampered"
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert "hash" in (state.error or "")
    assert state.staged_dir is None


def test_an_asset_missing_from_the_signed_list_is_refused(release: Release, tmp_path: Path):
    sums = f"{'0' * 64}  some-other-file.tar.gz\n".encode()
    release.files["SHA256SUMS"] = sums
    release.files["SHA256SUMS.sig"] = release.key.sign(sums)
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert ASSET not in release.requests


def test_a_server_error_on_the_archive_is_reported_not_raised(release: Release, tmp_path: Path):
    release.status[ASSET] = 500
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert state.error


def test_a_404_on_the_signature_is_reported_not_raised(release: Release, tmp_path: Path):
    del release.files["SHA256SUMS.sig"]
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR


def test_a_hostile_archive_with_a_good_hash_is_still_refused(release: Release, tmp_path: Path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    release.set_archive(buffer.getvalue())
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert not (tmp_path / "escape").exists()


def test_an_asset_name_that_is_a_path_is_refused_before_any_request(release: Release, tmp_path: Path):
    plan = release.plan()
    bad = DownloadPlan(ReleaseAsset("../evil.tar.gz", BASE + "evil"), plan.sums, plan.signature)
    state = UpdateState()
    staging = tmp_path / "staging"
    staging.mkdir()
    state.begin("../evil.tar.gz", staging)

    import asyncio

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(release.handler)) as client:
            await stage_update(client, bad, state)

    asyncio.run(go())
    assert state.status is UpdateStatus.ERROR
    assert release.requests == []


def test_an_archive_larger_than_the_cap_is_refused(release: Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(download, "MAX_ASSET_BYTES", 10)
    state = run_stage(release, tmp_path)
    assert state.status is UpdateStatus.ERROR
    assert "larger" in (state.error or "")


def test_progress_reaches_one_hundred_when_ready(release: Release, tmp_path: Path):
    state = run_stage(release, tmp_path)
    assert state.percent == 100
