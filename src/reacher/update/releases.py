"""Fetch the Labrynth release list from GitHub and choose the newest for a channel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

import httpx

from .versions import Version, VersionError, parse_version

RELEASES_URL = "https://api.github.com/repos/Otis-Lab-MUSC/labrynth/releases"
RELEASES_PER_PAGE = 20
FETCH_TIMEOUT_SECONDS = 10.0
GITHUB_ACCEPT_HEADER = "application/vnd.github+json"


class ReleaseFetchError(RuntimeError):
    """Raised when the release list cannot be fetched or is not a list."""


class Channel(Enum):
    """Which releases an update check considers."""

    STABLE = "stable"
    PRERELEASE = "prerelease"

    @classmethod
    def from_text(cls, text: str | None) -> Channel:
        """Return the channel named by text; None selects the default, prerelease."""
        if text is None:
            return cls.PRERELEASE
        return cls(text)


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable file attached to a release."""

    name: str
    url: str


@dataclass(frozen=True)
class Release:
    """A published release with a parsed version."""

    tag: str
    version: Version
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]


def parse_assets(raw_assets: object) -> tuple[ReleaseAsset, ...]:
    """Return the well-formed assets from a release's asset list."""
    if not isinstance(raw_assets, list):
        return ()
    assets = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        url = raw.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            assets.append(ReleaseAsset(name, url))
    return tuple(assets)


def parse_release(raw: object) -> Release | None:
    """Return a Release for one API entry, or None for drafts and unusable entries."""
    if not isinstance(raw, dict) or raw.get("draft") is True:
        return None
    tag = raw.get("tag_name")
    if not isinstance(tag, str):
        return None
    try:
        version = parse_version(tag)
    except VersionError:
        return None
    prerelease = raw.get("prerelease") is True or version.is_prerelease
    return Release(tag, version, prerelease, parse_assets(raw.get("assets")))


def parse_releases(payload: object) -> list[Release]:
    """Parse a GitHub releases response into usable releases."""
    if not isinstance(payload, list):
        raise ReleaseFetchError("release list is not a JSON array")
    parsed = (parse_release(raw) for raw in payload)
    return [release for release in parsed if release is not None]


def pick_latest(releases: list[Release], channel: Channel) -> Release | None:
    """Return the highest-versioned release allowed on the channel, or None."""
    allowed = [release for release in releases if channel is Channel.PRERELEASE or not release.prerelease]
    return max(allowed, key=lambda release: release.version, default=None)


async def fetch_releases(client: httpx.AsyncClient) -> list[Release]:
    """Fetch and parse the most recent releases from GitHub."""
    try:
        response = await client.get(
            RELEASES_URL,
            params={"per_page": RELEASES_PER_PAGE},
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"Accept": GITHUB_ACCEPT_HEADER},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ReleaseFetchError(f"could not fetch releases: {exc}") from exc
    return parse_releases(payload)
