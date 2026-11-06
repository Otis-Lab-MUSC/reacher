"""Update routes: check for a newer release, fetch and verify it, then hand off the swap.

The routes stay thin. Version choice lives in ``reacher.update.offer``,
verification in ``reacher.update.download``, and the file swap in the launcher.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request

from ...update.download import DownloadPlan, stage_update
from ...update.handoff import build_command, create_staging_root, find_install_dir, spawn_helper, staged_launcher
from ...update.offer import Offer, build_offer, current_platform, current_version_text, releases_url
from ...update.releases import Channel, ReleaseFetchError, fetch_releases
from ...update.state import UpdateState, UpdateStatus
from .websocket import _trigger_shutdown as trigger_shutdown

logger = logging.getLogger(__name__)

router = APIRouter()

SHUTDOWN_DELAY_SECONDS = 2.0
BUSY_SESSION_STATES = frozenset({"running", "paused", "uploading"})
APPLY_MODE_RESTART = "restart"
APPLY_MODE_MANUAL = "manual"
HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409
HTTP_SERVER_ERROR = 500
HTTP_UNAVAILABLE = 503

ChannelName = Literal["stable", "prerelease"]


def get_update_state(app: FastAPI) -> UpdateState:
    """Return the app's update job state, creating it on first use."""
    state = getattr(app.state, "update_state", None)
    if not isinstance(state, UpdateState):
        state = UpdateState()
        app.state.update_state = state
    return state


async def check_for_update(request: Request, channel: Channel) -> Offer:
    """Fetch the release list and compare it with the running version."""
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        releases = await fetch_releases(client, releases_url())
    except ReleaseFetchError as exc:
        logger.warning("update check failed: %s", exc)
        raise HTTPException(status_code=HTTP_UNAVAILABLE, detail=f"Could not check for updates: {exc}") from exc
    system, machine = current_platform()
    return build_offer(releases, current_version_text(), channel, system, machine)


def describe_offer(offer: Offer, channel: Channel, install_dir: Path | None) -> dict[str, str | bool | None]:
    """Return the JSON the frontend reads for an update check."""
    plan = offer.plan
    return {
        "currentVersion": offer.current_text,
        "latestVersion": str(offer.latest.version) if offer.latest else None,
        "updateAvailable": offer.update_available,
        "channel": channel.value,
        "assetName": plan.asset.name if plan else None,
        "assetUrl": plan.asset.url if plan else None,
        "sha256Url": plan.sums.url if plan else None,
        "applyMode": APPLY_MODE_RESTART if install_dir else APPLY_MODE_MANUAL,
    }


async def run_stage_job(client: httpx.AsyncClient, plan: DownloadPlan, state: UpdateState) -> None:
    """Run the download job and record any failure on state."""
    try:
        await stage_update(client, plan, state)
    except Exception as exc:  # noqa: BLE001 - top-level boundary of a background job; the failure is recorded on state
        logger.exception("update job crashed")
        state.mark_failed(f"unexpected failure: {exc}")


def require_offered_plan(offer: Offer, install_dir: Path | None) -> DownloadPlan:
    """Return the plan to download, or raise 409 when nothing can be installed."""
    if install_dir is None:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="This install cannot update itself")
    if not offer.update_available or offer.plan is None:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="No update is available for this platform")
    return offer.plan


def session_is_busy(request: Request) -> bool:
    """Return True while any session is running, paused or uploading firmware."""
    sessions = request.app.state.session_manager.list_sessions()
    return any(session.get("state") in BUSY_SESSION_STATES for session in sessions)


@router.get("/info")
async def update_info(
    request: Request, channel: ChannelName | None = Query(default=None)
) -> dict[str, str | bool | None]:
    """Report the running version, the newest release on the channel, and the asset to fetch."""
    selected = Channel.from_text(channel)
    offer = await check_for_update(request, selected)
    return describe_offer(offer, selected, find_install_dir())


@router.post("/download")
async def start_download(
    request: Request, background: BackgroundTasks, channel: ChannelName | None = Query(default=None)
) -> dict[str, str]:
    """Fetch, verify and unpack the newest release in the background.

    The files come from the release list, never from the request, so a caller
    cannot point the updater at another URL.
    """
    state = get_update_state(request.app)
    if state.busy:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="An update is already in progress")
    install_dir = find_install_dir()
    plan = require_offered_plan(await check_for_update(request, Channel.from_text(channel)), install_dir)
    if state.busy:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="An update is already in progress")
    assert install_dir is not None
    state.reset()
    try:
        staging = create_staging_root(install_dir)
    except OSError as exc:
        raise HTTPException(status_code=HTTP_SERVER_ERROR, detail=f"Could not create a staging folder: {exc}") from exc
    state.begin(plan.asset.name, staging)
    background.add_task(run_stage_job, request.app.state.http_client, plan, state)
    return {"status": "started"}


@router.get("/status")
async def download_status(request: Request) -> dict[str, str | int | bool | None]:
    """Report download progress and whether the update has been verified."""
    return get_update_state(request.app).to_payload()


@router.post("/launch")
async def launch_update(request: Request) -> dict[str, str]:
    """Start the launcher's swap helper, then shut the backend down so the swap can proceed."""
    state = get_update_state(request.app)
    staged = state.staged_dir
    if state.status is not UpdateStatus.READY or staged is None or not staged.is_dir():
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail="No verified update is ready")
    install_dir = find_install_dir()
    if install_dir is None:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="This install cannot update itself")
    if session_is_busy(request):
        raise HTTPException(status_code=HTTP_CONFLICT, detail="Stop the running session before updating")
    try:
        helper = staged_launcher(Path(sys.executable).resolve(), install_dir, staged)
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="This install cannot update itself") from exc
    command = build_command(helper, staged, install_dir, os.getpid())
    try:
        spawn_helper(command)
    except OSError as exc:
        logger.warning("could not start the update helper: %s", exc)
        raise HTTPException(status_code=HTTP_SERVER_ERROR, detail=f"Could not start the update helper: {exc}") from exc
    state.mark_launching()
    asyncio.get_running_loop().call_later(SHUTDOWN_DELAY_SECONDS, trigger_shutdown)
    return {"status": "launching"}
