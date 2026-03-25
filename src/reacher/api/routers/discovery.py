"""Discovery and pairing endpoints for zero-config machine setup.

GET  /api/discovery            — list mDNS peers + paired machines
POST /api/discovery/manual     — store a manually-supplied credential
POST /api/discovery/{id}/pair  — use a pairing code to pair with a peer
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import discovery, machines

router = APIRouter()
logger = logging.getLogger(__name__)


class PairRequest(BaseModel):
    code: str
    name: str | None = None


class ManualPairRequest(BaseModel):
    url: str
    api_key: str
    name: str | None = None


@router.get("")
async def list_discovered(request: Request) -> dict:
    """Return all REACHER devices: mDNS-visible peers and previously-paired machines."""
    peers = discovery.get_peers()
    paired = machines.get_all()
    devices = []
    seen: set[str] = set()

    # mDNS-visible peers (may or may not already be paired)
    for device_id, peer in peers.items():
        seen.add(device_id)
        devices.append({
            "device_id": device_id,
            "hostname": peer["hostname"],
            "url": f"http://{peer['host']}:{peer['port']}",
            "paired": device_id in paired,
            "discovered": True,
            "active_sessions": None,
        })

    # Paired machines not currently visible via mDNS (e.g. offline but previously paired)
    for device_id, info in paired.items():
        if device_id not in seen:
            devices.append({
                "device_id": device_id,
                "hostname": info["hostname"],
                "url": info["url"],
                "paired": True,
                "discovered": False,
                "active_sessions": None,
            })

    return {"devices": devices}


@router.post("/manual")
async def manual_pair(body: ManualPairRequest, request: Request) -> dict:
    """Store a manually-supplied machine URL and API key server-side.

    The browser calls this when the user uses the "Add Manually" fallback
    dialog.  The key is stored in ~/.reacher/machines.json and never
    returned to the browser.
    """
    url = body.url.rstrip("/")
    http_client: httpx.AsyncClient = request.app.state.http_client

    try:
        resp = await http_client.get(f"{url}/health", timeout=5.0)
        health = resp.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Cannot reach device at that URL")

    if health.get("service") != "reacher":
        raise HTTPException(status_code=400, detail="No REACHER device found at that URL")

    device_id: str = health["device_id"]
    hostname: str = health.get("hostname", device_id)
    name = body.name or hostname
    machines.upsert(device_id, url, body.api_key, hostname, name)

    return {"device_id": device_id, "hostname": hostname, "url": url, "name": name}


@router.post("/{device_id}/pair")
async def pair_device(device_id: str, body: PairRequest, request: Request) -> dict:
    """Use a pairing code to authenticate with a discovered REACHER device.

    1. Looks up the device in the mDNS peer table.
    2. Forwards the pairing code to the remote device's /api/pairing/claim.
    3. On success, stores the returned API key in machines.json.
    """
    if machines.get(device_id):
        raise HTTPException(status_code=409, detail="Machine is already paired")

    peers = discovery.get_peers()
    peer = peers.get(device_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Device not found — it may have gone offline")

    remote_url = f"http://{peer['host']}:{peer['port']}"
    http_client: httpx.AsyncClient = request.app.state.http_client

    try:
        resp = await http_client.post(
            f"{remote_url}/api/pairing/claim",
            json={"code": body.code},
            timeout=10.0,
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=400, detail="Cannot reach device")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Device did not respond in time")

    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Too many attempts on the remote device — wait a minute")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired pairing code")

    remote_key: str = resp.json()["api_key"]
    hostname = peer["hostname"]
    name = body.name or hostname
    machines.upsert(device_id, remote_url, remote_key, hostname, name)
    logger.info("Paired with device %s (%s) at %s", device_id[:8], hostname, remote_url)

    return {"device_id": device_id, "hostname": hostname, "url": remote_url, "name": name}
