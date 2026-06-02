"""Update checking and installation router.

Downloads platform-specific installers from GitHub releases and launches them.
"""

import asyncio
import logging
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level download state (single in-memory job)
_download_state: dict = {
    "status": "idle",  # idle | downloading | ready | error
    "percent": 0,
    "local_path": None,
    "asset_name": None,
    "error": None,
}

_download_temp_dir: str | None = None


def _reset_download_state() -> None:
    """Clear download state and old temp dir."""
    global _download_temp_dir
    if _download_temp_dir and os.path.isdir(_download_temp_dir):
        try:
            import shutil
            shutil.rmtree(_download_temp_dir)
        except Exception:
            pass
    _download_state["status"] = "idle"
    _download_state["percent"] = 0
    _download_state["local_path"] = None
    _download_state["asset_name"] = None
    _download_state["error"] = None
    _download_temp_dir = None


def _resolve_asset(assets: list[dict]) -> dict | None:
    """Pick the best asset for the current platform from the GitHub releases asset list."""
    plat = sys.platform
    machine = platform.machine().lower()

    if plat == "win32":
        suffixes = ["-windows-x64.exe"]
    elif plat == "darwin":
        suffixes = ["-macos-arm64.dmg", "-macos-x86_64.dmg"]
    else:  # linux
        if "aarch64" in machine or "arm64" in machine:
            suffixes = ["-linux-arm64.deb", "-linux-arm64.tar.gz", "-linux-arm64.AppImage"]
        else:
            suffixes = ["-linux-amd64.deb", "-linux-amd64.tar.gz", "-linux-amd64.AppImage"]

    for suffix in suffixes:
        for asset in assets:
            if asset.get("name", "").endswith(suffix):
                return asset
    return None


async def _do_download(client: httpx.AsyncClient, asset_url: str, asset_name: str) -> None:
    """Stream download the asset to a temp file, updating _download_state."""
    global _download_temp_dir
    try:
        _download_temp_dir = tempfile.mkdtemp(prefix="labrynth-update_")
        local_path = os.path.join(_download_temp_dir, asset_name)

        async with client.stream("GET", asset_url) as response:
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="Failed to download asset")

            content_length = int(response.headers.get("content-length", 0))
            bytes_downloaded = 0

            with open(local_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        bytes_downloaded += len(chunk)
                        if content_length > 0:
                            _download_state["percent"] = min(100, int(100 * bytes_downloaded / content_length))

        _download_state["local_path"] = local_path
        _download_state["status"] = "ready"
        _download_state["percent"] = 100
    except Exception as exc:
        logger.exception("Download failed: %s", exc)
        _download_state["status"] = "error"
        _download_state["error"] = str(exc)


def _launch(path: str) -> None:
    """Launch the installer using OS-appropriate method."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, None, 1)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        # Linux
        if path.endswith(".deb"):
            subprocess.Popen(["pkexec", "dpkg", "-i", path])
        elif path.endswith(".AppImage"):
            os.chmod(path, 0o755)
            subprocess.Popen([path])
        else:
            # tar.gz — cannot auto-install
            raise HTTPException(status_code=202, detail=f"Manual install required. Archive saved to: {path}")


@router.get("/info")
async def update_info(request: Request) -> dict:
    """Fetch GitHub releases API and resolve platform-specific asset."""
    from reacher import __version__

    client: httpx.AsyncClient = request.app.state.http_client
    try:
        res = await client.get(
            "https://api.github.com/repos/Otis-Lab-MUSC/labrynth/releases/latest",
            timeout=10.0,
            headers={"Accept": "application/vnd.github+json"},
        )
        res.raise_for_status()
    except Exception as exc:
        logger.warning("GitHub API unreachable: %s", exc)
        raise HTTPException(status_code=503, detail=f"GitHub unreachable: {exc}")

    data = res.json()
    asset = _resolve_asset(data.get("assets", []))
    latest = data.get("tag_name", "").lstrip("v")

    return {
        "currentVersion": __version__,
        "latestVersion": latest,
        "assetUrl": asset["browser_download_url"] if asset else None,
        "assetName": asset["name"] if asset else None,
    }


class DownloadRequest(BaseModel):
    assetUrl: str
    assetName: str


@router.post("/download")
async def start_download(body: DownloadRequest, request: Request) -> dict:
    """Start downloading the platform-specific installer."""
    if _download_state["status"] == "downloading":
        raise HTTPException(status_code=409, detail="Download already in progress")

    _reset_download_state()
    _download_state["status"] = "downloading"
    _download_state["asset_name"] = body.assetName

    client: httpx.AsyncClient = request.app.state.http_client
    asyncio.get_running_loop().create_task(_do_download(client, body.assetUrl, body.assetName))

    return {"status": "started"}


@router.get("/status")
async def download_status() -> dict:
    """Poll current download progress."""
    return _download_state.copy()


@router.post("/launch")
async def launch_installer() -> dict:
    """Launch the downloaded installer and trigger graceful backend shutdown."""
    path = _download_state.get("local_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="No installer ready")

    try:
        _launch(path)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to launch installer: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to launch installer: {exc}")

    # Signal backend shutdown so the installer doesn't fight a running process
    from .websocket import _trigger_shutdown

    asyncio.get_running_loop().call_later(2.0, _trigger_shutdown)

    return {"status": "launching"}
