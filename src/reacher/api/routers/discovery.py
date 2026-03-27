"""Discovery and pairing endpoints for zero-config machine setup.

GET    /api/discovery               — list mDNS peers + paired machines
POST   /api/discovery/manual        — store a manually-supplied API key
POST   /api/discovery/pair-by-code  — pair with any discovered device using just a pairing code
POST   /api/discovery/pair-by-url   — pair with a device at a known URL using a pairing code
POST   /api/discovery/{id}/pair     — pair with a specific mDNS-discovered device using a pairing code
DELETE /api/discovery/{id}          — unpair and remove a previously paired machine
"""

import asyncio
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


class PairByUrlRequest(BaseModel):
    url: str
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


@router.post("/pair-by-url")
async def pair_by_url(body: PairByUrlRequest, request: Request) -> dict:
    """Pair with a REACHER device at a known URL using its pairing code.

    Used when the device is not visible via mDNS (different subnet, firewall,
    or zeroconf not installed).  The caller supplies the URL directly instead of
    relying on automatic discovery.
    """
    url = body.url.rstrip("/")
    http_client: httpx.AsyncClient = request.app.state.http_client

    # Probe health to obtain the device_id
    try:
        resp = await http_client.get(f"{url}/health", timeout=5.0)
        health = resp.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Cannot reach device at that URL")

    if health.get("service") != "reacher":
        raise HTTPException(status_code=400, detail="No REACHER device found at that URL")

    device_id: str = health["device_id"]
    if machines.get(device_id):
        raise HTTPException(status_code=409, detail="Machine is already paired")

    # Forward pairing code to the remote device
    try:
        resp = await http_client.post(
            f"{url}/api/pairing/claim",
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
    hostname: str = health.get("hostname", device_id)
    name = body.name or hostname
    machines.upsert(device_id, url, remote_key, hostname, name)
    logger.info("Paired with device %s (%s) at %s via URL", device_id[:8], hostname, url)

    return {"device_id": device_id, "hostname": hostname, "url": url, "name": name}


@router.post("/pair-by-code")
async def pair_by_code(body: PairRequest, request: Request) -> dict:
    """Pair with any discovered REACHER device using only a pairing code.

    Tries the code against every discovered peer (mDNS + subnet scan) in
    parallel.  The first device that accepts the code is paired.  This is
    the simplest UX — the user only needs the code shown on the device.
    """
    peers = discovery.get_peers()
    already_paired = machines.get_all()

    # Filter to unpaired peers only
    candidates = {
        did: peer for did, peer in peers.items()
        if did not in already_paired
    }

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="No unpaired REACHER devices found on the network. "
            "Ensure the device is running and on the same network.",
        )

    http_client: httpx.AsyncClient = request.app.state.http_client

    async def try_peer(device_id: str, peer: dict) -> dict | None:
        url = f"http://{peer['host']}:{peer['port']}"
        try:
            resp = await http_client.post(
                f"{url}/api/pairing/claim",
                json={"code": body.code},
                timeout=5.0,
            )
            if resp.status_code == 200:
                return {
                    "device_id": device_id,
                    "url": url,
                    "hostname": peer["hostname"],
                    "api_key": resp.json()["api_key"],
                }
        except Exception:
            pass
        return None

    results = await asyncio.gather(*[
        try_peer(did, peer) for did, peer in candidates.items()
    ])
    matched = [r for r in results if r is not None]

    if not matched:
        raise HTTPException(
            status_code=401,
            detail="No device accepted that pairing code. "
            "Check the code and try again.",
        )

    # Use the first match (only one device should accept the code)
    hit = matched[0]
    name = body.name or hit["hostname"]
    machines.upsert(hit["device_id"], hit["url"], hit["api_key"], hit["hostname"], name)
    logger.info(
        "Paired with device %s (%s) at %s via code broadcast",
        hit["device_id"][:8], hit["hostname"], hit["url"],
    )
    return {
        "device_id": hit["device_id"],
        "hostname": hit["hostname"],
        "url": hit["url"],
        "name": name,
    }


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


@router.delete("/{device_id}")
async def remove_machine(device_id: str, request: Request) -> dict:
    """Unpair and remove a previously paired machine.

    Best-effort notifies the remote device to clear its paired state so it
    can accept new pairing attempts.  If the remote device is unreachable the
    local entry is still removed.
    """
    machine = machines.get(device_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not paired")

    # Best-effort: tell the remote Pi to unpair
    http_client: httpx.AsyncClient = request.app.state.http_client
    try:
        await http_client.post(
            f"{machine['url']}/api/pairing/unpair",
            headers={"Authorization": f"Bearer {machine['api_key']}"},
            timeout=5.0,
        )
        logger.info("Remote device %s acknowledged unpairing", device_id[:8])
    except Exception:
        logger.warning("Could not notify device %s of unpairing (may be offline)", device_id[:8])

    machines.remove(device_id)
    logger.info("Removed paired machine %s", device_id[:8])
    return {"status": "removed", "device_id": device_id}
