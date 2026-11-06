"""Deciding whether an update is on offer for the running version."""

import pytest

import reacher
from reacher.update import offer as offer_module
from reacher.update.offer import build_offer, current_version_text, releases_url
from reacher.update.releases import RELEASES_URL, Channel, Release, ReleaseAsset
from reacher.update.versions import parse_version

SIGNED = ("SHA256SUMS", "SHA256SUMS.sig")


def make_release(tag: str, prerelease: bool, with_assets: bool = True) -> Release:
    version = parse_version(tag)
    names = (f"labrynth-{version}-linux-x64.tar.gz", *SIGNED) if with_assets else ()
    return Release(tag, version, prerelease, tuple(ReleaseAsset(n, f"https://example.test/{n}") for n in names))


RELEASES = [
    make_release("v3.0.0", False),
    make_release("v3.0.1-beta.16", True),
    make_release("v3.0.1-beta.17", True),
]


def offer_for(current: str, channel=Channel.PRERELEASE, releases=RELEASES):
    return build_offer(releases, current, channel, "linux", "x86_64")


def test_a_newer_beta_is_offered_with_a_plan():
    offer = offer_for("3.0.1-beta.16")
    assert offer.update_available is True
    assert offer.latest is not None and offer.latest.tag == "v3.0.1-beta.17"
    assert offer.plan is not None


def test_the_same_version_is_not_offered():
    offer = offer_for("3.0.1-beta.17")
    assert offer.update_available is False
    assert offer.plan is None


def test_an_older_latest_is_not_offered():
    assert offer_for("3.1.0").update_available is False


def test_the_stable_channel_ignores_prereleases():
    offer = offer_for("2.9.0", Channel.STABLE)
    assert offer.latest is not None and offer.latest.tag == "v3.0.0"


def test_a_beta_does_not_see_the_older_stable_as_an_update():
    assert offer_for("3.0.1-beta.16", Channel.STABLE).update_available is False


def test_a_current_version_that_will_not_parse_offers_nothing():
    offer = offer_for("3.4.0.dev0+local")
    assert offer.update_available is False
    assert offer.current_text == "3.4.0.dev0+local"


def test_a_newer_release_with_no_assets_for_this_platform_has_no_plan():
    releases = [make_release("v3.0.1-beta.17", True, with_assets=False)]
    offer = offer_for("3.0.1-beta.16", releases=releases)
    assert offer.update_available is True
    assert offer.plan is None


def test_no_releases_means_no_update():
    offer = offer_for("3.0.1-beta.16", releases=[])
    assert offer.latest is None and offer.update_available is False


def test_current_version_prefers_the_launcher_environment(monkeypatch):
    monkeypatch.setenv("LABRYNTH_APP_VERSION", "3.0.1-beta.16")
    assert current_version_text() == "3.0.1-beta.16"


def test_current_version_falls_back_to_the_backend_version(monkeypatch):
    monkeypatch.delenv("LABRYNTH_APP_VERSION", raising=False)
    assert current_version_text() == reacher.__version__


def test_a_blank_launcher_version_is_ignored(monkeypatch):
    monkeypatch.setenv("LABRYNTH_APP_VERSION", "  ")
    assert current_version_text() == reacher.__version__


def test_releases_url_defaults_to_github(monkeypatch):
    monkeypatch.delenv("LABRYNTH_UPDATE_API_URL", raising=False)
    assert releases_url() == RELEASES_URL


def test_releases_url_can_be_overridden(monkeypatch):
    monkeypatch.setenv("LABRYNTH_UPDATE_API_URL", "http://127.0.0.1:9/releases")
    assert releases_url() == "http://127.0.0.1:9/releases"


@pytest.mark.parametrize("system, machine", [("linux", "x86_64"), ("win32", "AMD64")])
def test_current_platform_reports_lowercase_machine(monkeypatch, system, machine):
    monkeypatch.setattr(offer_module.sys, "platform", system)
    monkeypatch.setattr(offer_module.platform, "machine", lambda: machine)
    assert offer_module.current_platform() == (system, machine.lower())
