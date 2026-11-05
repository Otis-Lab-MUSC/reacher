"""Parse and order release versions of the form ``vX.Y.Z[-stage.N]``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")


class VersionError(ValueError):
    """Raised when text is not a recognised release version."""


class Stage(IntEnum):
    """Release stage; a higher value is a later stage."""

    ALPHA = 0
    BETA = 1
    RC = 2
    STABLE = 3


STAGE_BY_NAME = {"alpha": Stage.ALPHA, "beta": Stage.BETA, "rc": Stage.RC}


@dataclass(frozen=True, order=True)
class Version:
    """A release version; ordering follows the alpha < beta < rc < stable ladder."""

    major: int
    minor: int
    patch: int
    stage: Stage = Stage.STABLE
    number: int = 0

    @property
    def is_prerelease(self) -> bool:
        """Return True for alpha, beta and rc versions."""
        return self.stage is not Stage.STABLE

    def __str__(self) -> str:
        """Return the tag form without the ``v`` prefix, e.g. ``3.0.1-beta.16``."""
        core = f"{self.major}.{self.minor}.{self.patch}"
        if not self.is_prerelease:
            return core
        return f"{core}-{self.stage.name.lower()}.{self.number}"


def parse_version(text: str) -> Version:
    """Parse a tag or version string such as ``v3.0.1-beta.16`` into a Version."""
    match = VERSION_PATTERN.match(text)
    if match is None:
        raise VersionError(f"not a release version: {text!r}")
    major, minor, patch, stage_name, number = match.groups()
    stage = STAGE_BY_NAME[stage_name] if stage_name else Stage.STABLE
    return Version(int(major), int(minor), int(patch), stage, int(number) if number else 0)
