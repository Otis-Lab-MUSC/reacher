"""Download an update, verify it against the signed checksum list, and unpack it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from .apply import ApplyError, extract_archive
from .release_key import load_release_public_key
from .releases import ReleaseAsset
from .state import UpdateState
from .verify import VerificationError, expected_hash, verify_file_hash, verify_signature

logger = logging.getLogger(__name__)

MAX_ASSET_BYTES = 4 * 1024**3
MAX_SMALL_FILE_BYTES = 1024 * 1024
CHUNK_BYTES = 256 * 1024
DOWNLOAD_DIR_NAME = "download"
UNPACK_DIR_NAME = "unpacked"
PERCENT_SCALE = 100


@dataclass(frozen=True)
class DownloadPlan:
    """The three release files an update needs: the archive, its checksums, and the signature."""

    asset: ReleaseAsset
    sums: ReleaseAsset
    signature: ReleaseAsset


def check_asset_name(name: str) -> None:
    """Raise VerificationError unless name is a plain file name."""
    if not name or "/" in name or "\\" in name or name in (".", "..") or ":" in name:
        raise VerificationError(f"refusing asset name {name!r}: it is not a plain file name")


async def stream_to_file(
    client: httpx.AsyncClient, url: str, target: Path, limit: int, state: UpdateState | None = None
) -> None:
    """Stream url into target, refusing more than limit bytes and reporting progress on state."""
    async with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        if total > limit:
            raise VerificationError(f"{target.name} is larger than the {limit} byte limit")
        received = 0
        with target.open("wb") as out:
            async for chunk in response.aiter_bytes(chunk_size=CHUNK_BYTES):
                received += len(chunk)
                if received > limit:
                    raise VerificationError(f"{target.name} is larger than the {limit} byte limit")
                out.write(chunk)
                if state is not None and total > 0:
                    state.set_progress(PERCENT_SCALE * received // total)


async def fetch_small_file(client: httpx.AsyncClient, asset: ReleaseAsset, folder: Path) -> bytes:
    """Download a small release file and return its bytes."""
    target = folder / asset.name
    await stream_to_file(client, asset.url, target, MAX_SMALL_FILE_BYTES)
    return target.read_bytes()


async def fetch_signed_hash(client: httpx.AsyncClient, plan: DownloadPlan, folder: Path) -> str:
    """Return the signed hash for the plan's archive, checking the signature first."""
    sums = await fetch_small_file(client, plan.sums, folder)
    signature = await fetch_small_file(client, plan.signature, folder)
    verify_signature(sums, signature, load_release_public_key())
    try:
        text = sums.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("SHA256SUMS is not valid text") from exc
    return expected_hash(text, plan.asset.name)


async def stage_update(client: httpx.AsyncClient, plan: DownloadPlan, state: UpdateState) -> None:
    """Fetch, verify and unpack the update into state's staging folder.

    The signature and the signed hash are checked before the archive is
    downloaded. Any failure is recorded on state and nothing is raised, so a
    background task cannot die silently.
    """
    root = state.staging_root
    if root is None:
        state.mark_failed("no staging folder is set")
        return
    try:
        check_asset_name(plan.asset.name)
        download_dir = root / DOWNLOAD_DIR_NAME
        download_dir.mkdir()
        digest = await fetch_signed_hash(client, plan, download_dir)
        archive = download_dir / plan.asset.name
        await stream_to_file(client, plan.asset.url, archive, MAX_ASSET_BYTES, state)
        await asyncio.to_thread(verify_file_hash, archive, digest)
        unpack_dir = root / UNPACK_DIR_NAME
        unpack_dir.mkdir()
        staged = await asyncio.to_thread(extract_archive, archive, unpack_dir)
    except (VerificationError, ApplyError) as exc:
        logger.warning("update refused: %s", exc)
        state.mark_failed(str(exc))
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("update download failed: %s", exc)
        state.mark_failed(f"the download failed: {exc}")
    else:
        state.mark_ready(staged)
