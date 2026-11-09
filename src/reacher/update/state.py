"""State of the single in-flight update job, held on ``app.state``."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

PERCENT_MIN = 0
PERCENT_MAX = 100


class UpdateStatus(Enum):
    """Where the update job stands; the values are what the frontend polls."""

    IDLE = "idle"
    DOWNLOADING = "downloading"
    READY = "ready"
    LAUNCHING = "launching"
    ERROR = "error"


@dataclass
class UpdateState:
    """One update job: download, verify, stage. Paths stay server-side."""

    status: UpdateStatus = UpdateStatus.IDLE
    percent: int = PERCENT_MIN
    asset_name: str | None = None
    verified: bool = False
    error: str | None = None
    staging_root: Path | None = None
    staged_dir: Path | None = None

    @property
    def busy(self) -> bool:
        """Return True while a download or a launch is in progress."""
        return self.status in (UpdateStatus.DOWNLOADING, UpdateStatus.LAUNCHING)

    def to_payload(self) -> dict[str, str | int | bool | None]:
        """Return the fields the status route reports; no filesystem paths."""
        return {
            "status": self.status.value,
            "percent": self.percent,
            "asset_name": self.asset_name,
            "verified": self.verified,
            "error": self.error,
        }

    def begin(self, asset_name: str, staging_root: Path) -> None:
        """Start a download; raise RuntimeError if one is already running."""
        if self.busy:
            raise RuntimeError("an update is already in progress")
        self.status = UpdateStatus.DOWNLOADING
        self.percent = PERCENT_MIN
        self.asset_name = asset_name
        self.verified = False
        self.error = None
        self.staging_root = staging_root
        self.staged_dir = None

    def set_progress(self, percent: int) -> None:
        """Record download progress, clamped to 0 through 100."""
        self.percent = max(PERCENT_MIN, min(PERCENT_MAX, percent))

    def mark_ready(self, staged_dir: Path) -> None:
        """Record a downloaded, verified and unpacked update."""
        self.status = UpdateStatus.READY
        self.percent = PERCENT_MAX
        self.verified = True
        self.staged_dir = staged_dir

    def mark_launching(self) -> None:
        """Record that the launcher helper has been started."""
        self.status = UpdateStatus.LAUNCHING

    def mark_failed(self, message: str) -> None:
        """Record why the job failed; nothing from it is trusted."""
        self.status = UpdateStatus.ERROR
        self.verified = False
        self.error = message
        self.staged_dir = None

    def reset(self) -> None:
        """Return to idle and delete the staging folder, if any."""
        if self.staging_root is not None:
            try:
                shutil.rmtree(self.staging_root)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("could not remove update staging at %s: %s", self.staging_root, exc)
        self.status = UpdateStatus.IDLE
        self.percent = PERCENT_MIN
        self.asset_name = None
        self.verified = False
        self.error = None
        self.staging_root = None
        self.staged_dir = None
