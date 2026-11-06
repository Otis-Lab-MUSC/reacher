"""Choose the update archive, checksum list and signature a release offers a platform."""

from __future__ import annotations

from .download import DownloadPlan
from .releases import Release, ReleaseAsset

SUMS_NAME = "SHA256SUMS"
SIGNATURE_NAME = "SHA256SUMS.sig"
ARCHIVE_SUFFIX_BY_PLATFORM = {
    ("linux", "x86_64"): "linux-x64.tar.gz",
    ("linux", "amd64"): "linux-x64.tar.gz",
    ("win32", "amd64"): "windows-x64.zip",
    ("win32", "x86_64"): "windows-x64.zip",
    ("darwin", "arm64"): "macos-arm64.tar.gz",
}


def find_asset(release: Release, name: str) -> ReleaseAsset | None:
    """Return the release asset called name, or None."""
    return next((asset for asset in release.assets if asset.name == name), None)


def select_plan(release: Release, system: str, machine: str) -> DownloadPlan | None:
    """Return the files to fetch for this platform, or None when the release lacks any of them.

    The archive name must carry the release's own version, so a mislabelled
    asset from another release is never offered.
    """
    suffix = ARCHIVE_SUFFIX_BY_PLATFORM.get((system, machine.lower()))
    if suffix is None:
        return None
    asset = find_asset(release, f"labrynth-{release.version}-{suffix}")
    sums = find_asset(release, SUMS_NAME)
    signature = find_asset(release, SIGNATURE_NAME)
    if asset is None or sums is None or signature is None:
        return None
    return DownloadPlan(asset=asset, sums=sums, signature=signature)
