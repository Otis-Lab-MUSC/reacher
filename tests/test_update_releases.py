"""Release-list parsing, channel filtering and fetching for the update checker."""

import httpx
import pytest

from reacher.update.releases import (
    Channel,
    Release,
    ReleaseFetchError,
    fetch_releases,
    parse_releases,
    pick_latest,
)
from reacher.update.versions import parse_version


def _entry(tag, prerelease=False, draft=False, assets=()):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "assets": [{"name": name, "browser_download_url": f"https://example.test/{name}"} for name in assets],
    }


def test_parse_keeps_assets_and_flags():
    releases = parse_releases([_entry("v3.0.1-beta.16", prerelease=True, assets=["a.zip", "SHA256SUMS"])])
    assert len(releases) == 1
    release = releases[0]
    assert release.version == parse_version("3.0.1-beta.16")
    assert release.tag == "v3.0.1-beta.16"
    assert release.prerelease is True
    assert [(asset.name, asset.url) for asset in release.assets] == [
        ("a.zip", "https://example.test/a.zip"),
        ("SHA256SUMS", "https://example.test/SHA256SUMS"),
    ]


def test_parse_skips_drafts_and_unparseable_tags():
    releases = parse_releases(
        [_entry("v3.0.0"), _entry("v9.9.9", draft=True), _entry("nightly"), _entry("v3.0.1-gamma.1")]
    )
    assert [release.tag for release in releases] == ["v3.0.0"]


def test_parse_skips_malformed_entries():
    payload = [
        "not-a-dict",
        {"prerelease": False},
        {"tag_name": 7},
        {"tag_name": "v3.0.0", "assets": "nope"},
        {"tag_name": "v3.0.1", "assets": [{"name": "ok"}, {"name": 3, "browser_download_url": "x"}]},
    ]
    releases = parse_releases(payload)
    assert [release.tag for release in releases] == ["v3.0.0", "v3.0.1"]
    assert all(release.assets == () for release in releases)


def test_parse_rejects_non_list_payload():
    with pytest.raises(ReleaseFetchError):
        parse_releases({"message": "rate limited"})


def test_prerelease_flag_falls_back_to_tag_stage():
    release = parse_releases([_entry("v3.0.1-beta.2", prerelease=False)])[0]
    assert release.prerelease is True


def test_pick_latest_prerelease_channel_includes_prereleases():
    releases = parse_releases(
        [_entry("v3.0.0"), _entry("v3.0.1-beta.15", prerelease=True), _entry("v3.0.1-beta.16", prerelease=True)]
    )
    assert pick_latest(releases, Channel.PRERELEASE).tag == "v3.0.1-beta.16"


def test_pick_latest_stable_channel_filters_prereleases():
    releases = parse_releases([_entry("v3.0.0"), _entry("v3.0.1-beta.16", prerelease=True)])
    assert pick_latest(releases, Channel.STABLE).tag == "v3.0.0"


def test_pick_latest_ignores_api_order():
    releases = parse_releases([_entry("v3.0.1-beta.9", prerelease=True), _entry("v3.0.1-beta.16", prerelease=True)])
    assert pick_latest(releases, Channel.PRERELEASE).tag == "v3.0.1-beta.16"


def test_pick_latest_returns_none_when_nothing_matches():
    assert pick_latest([], Channel.PRERELEASE) is None
    only_beta = parse_releases([_entry("v3.0.1-beta.1", prerelease=True)])
    assert pick_latest(only_beta, Channel.STABLE) is None


def test_channel_parse_defaults_to_prerelease():
    assert Channel.from_text(None) is Channel.PRERELEASE
    assert Channel.from_text("stable") is Channel.STABLE
    assert Channel.from_text("prerelease") is Channel.PRERELEASE
    with pytest.raises(ValueError):
        Channel.from_text("nightly")


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_requests_release_list_and_parses():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[_entry("v3.0.1-beta.16", prerelease=True)])

    async with _client(handler) as client:
        releases = await fetch_releases(client)
    assert "per_page=20" in seen["url"]
    assert "/repos/Otis-Lab-MUSC/labrynth/releases" in seen["url"]
    assert [release.tag for release in releases] == ["v3.0.1-beta.16"]
    assert isinstance(releases[0], Release)


async def test_fetch_wraps_http_status_errors():
    async with _client(lambda request: httpx.Response(403, json={"message": "rate limit"})) as client:
        with pytest.raises(ReleaseFetchError):
            await fetch_releases(client)


async def test_fetch_wraps_transport_errors():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    async with _client(handler) as client:
        with pytest.raises(ReleaseFetchError):
            await fetch_releases(client)


async def test_fetch_wraps_invalid_json():
    async with _client(lambda request: httpx.Response(200, content=b"<html>")) as client:
        with pytest.raises(ReleaseFetchError):
            await fetch_releases(client)


async def test_fetch_can_target_another_release_list():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        await fetch_releases(client, "http://127.0.0.1:9/releases")
    assert seen["url"].startswith("http://127.0.0.1:9/releases")
