"""Choosing the update archive, checksum list and signature for a platform."""

import pytest

from reacher.update.assets import select_plan
from reacher.update.releases import Release, ReleaseAsset
from reacher.update.versions import parse_version

TAG = "v3.0.1-beta.17"
VERSION = "3.0.1-beta.17"
BASE = "https://example.test/dl/"
SIGNED = ("SHA256SUMS", "SHA256SUMS.sig")
EVERY_ASSET = (
    f"labrynth-{VERSION}-windows-x64.exe",
    f"labrynth-{VERSION}-windows-x64.zip",
    f"labrynth-{VERSION}-macos-arm64.dmg",
    f"labrynth-{VERSION}-macos-arm64.tar.gz",
    f"labrynth-{VERSION}-linux-x64.tar.gz",
    f"labrynth-{VERSION}-linux-x64.AppImage",
    f"labrynth_{VERSION}_amd64.deb",
    f"labrynth-cli-{VERSION}-linux-x64.tar.gz",
    f"labrynth-cli-{VERSION}-macos-arm64.tar.gz",
    f"labrynth-cli-{VERSION}-windows-x64.tar.gz",
    *SIGNED,
)


def make_release(names=EVERY_ASSET, tag=TAG) -> Release:
    assets = tuple(ReleaseAsset(name, BASE + name) for name in names)
    return Release(tag, parse_version(tag), True, assets)


@pytest.mark.parametrize(
    "system, machine, expected",
    [
        ("linux", "x86_64", f"labrynth-{VERSION}-linux-x64.tar.gz"),
        ("linux", "amd64", f"labrynth-{VERSION}-linux-x64.tar.gz"),
        ("win32", "amd64", f"labrynth-{VERSION}-windows-x64.zip"),
        ("darwin", "arm64", f"labrynth-{VERSION}-macos-arm64.tar.gz"),
    ],
)
def test_each_platform_gets_its_gui_update_archive(system, machine, expected):
    plan = select_plan(make_release(), system, machine)
    assert plan is not None
    assert plan.asset.name == expected
    assert plan.sums.name == "SHA256SUMS"
    assert plan.signature.name == "SHA256SUMS.sig"


def test_the_cli_tarball_is_never_chosen_for_the_gui():
    names = (f"labrynth-cli-{VERSION}-linux-x64.tar.gz", *SIGNED)
    assert select_plan(make_release(names), "linux", "x86_64") is None


@pytest.mark.parametrize(
    "system, machine",
    [("darwin", "x86_64"), ("linux", "aarch64"), ("linux", "arm64"), ("freebsd13", "amd64"), ("win32", "arm64")],
)
def test_platforms_ci_does_not_build_get_no_plan(system, machine):
    assert select_plan(make_release(), system, machine) is None


@pytest.mark.parametrize("missing", SIGNED)
def test_a_release_without_the_checksums_or_signature_gets_no_plan(missing):
    names = tuple(name for name in EVERY_ASSET if name != missing)
    assert select_plan(make_release(names), "linux", "x86_64") is None


def test_an_archive_named_for_another_version_is_not_chosen():
    names = ("labrynth-3.0.1-beta.16-linux-x64.tar.gz", *SIGNED)
    assert select_plan(make_release(names), "linux", "x86_64") is None


def test_urls_come_from_the_release_assets():
    plan = select_plan(make_release(), "linux", "x86_64")
    assert plan is not None
    assert plan.asset.url == BASE + f"labrynth-{VERSION}-linux-x64.tar.gz"
    assert plan.sums.url == BASE + "SHA256SUMS"
