"""Decide whether an update is on offer for the running app."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass

from .. import __version__
from .assets import select_plan
from .download import DownloadPlan
from .releases import RELEASES_URL, Channel, Release, pick_latest
from .versions import VersionError, parse_version

APP_VERSION_ENV = "LABRYNTH_APP_VERSION"
UPDATE_API_URL_ENV = "LABRYNTH_UPDATE_API_URL"


@dataclass(frozen=True)
class Offer:
    """What an update check found for this install."""

    current_text: str
    latest: Release | None
    update_available: bool
    plan: DownloadPlan | None


def current_version_text() -> str:
    """Return the running app's version: the launcher's value, else the backend's."""
    value = os.environ.get(APP_VERSION_ENV, "").strip()
    return value or __version__


def releases_url() -> str:
    """Return the release list URL; the environment may point it at a test server."""
    return os.environ.get(UPDATE_API_URL_ENV, "").strip() or RELEASES_URL


def current_platform() -> tuple[str, str]:
    """Return this host's platform name and lowercase machine type."""
    return sys.platform, platform.machine().lower()


def build_offer(releases: list[Release], current_text: str, channel: Channel, system: str, machine: str) -> Offer:
    """Compare the running version with the newest release on the channel."""
    latest = pick_latest(releases, channel)
    try:
        current = parse_version(current_text)
    except VersionError:
        return Offer(current_text, latest, False, None)
    if latest is None or latest.version <= current:
        return Offer(current_text, latest, False, None)
    return Offer(current_text, latest, True, select_plan(latest, system, machine))
