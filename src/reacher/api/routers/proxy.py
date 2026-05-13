"""Transparent HTTP + WebSocket proxy for paired remote REACHER machines.

All REST calls to a remote machine are routed through the local REACHER
server via /api/proxy/{device_id}/... — the browser never talks directly to
the remote machine, eliminating any CORS configuration requirement.

WebSocket events are relayed through this server as well.  The browser
connects to /api/proxy/{device_id}/ws/{session_id} on the local server,
which opens an upstream WebSocket to the Pi and relays messages
bidirectionally.  The token returned by GET /{device_id}/ws-token is the
*local* API key so the browser authenticates against the local server.
"""

import asyncio
import base64
import json
import logging

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from ... import machines
from ...uploader.uploader import FirmwareUploader
from ..middleware.auth import API_KEY, verify_ws_token

router = APIRouter()
# Separate router for WebSocket endpoints — must NOT have HTTP-only auth
# dependencies (HTTPBearer fails on WebSocket upgrade requests).  Auth is
# handled manually via verify_ws_token() inside the endpoint.
ws_router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{device_id}/ws-token")
async def get_ws_token(device_id: str, request: Request) -> dict:
    """Return the local API key and relay WebSocket base URL for a paired machine.

    The browser uses this to connect to the local WS relay at
    /api/proxy/{device_id}/ws/{session_id}?token=<local_key>.
    The relay then connects upstream to the Pi using the stored Pi API key.
    """
    machine = machines.get(device_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not paired")

    # Build local relay base URL from the request's Host header
    host = request.headers.get("host", "localhost:6229")
    scheme = "wss" if request.headers.get("x-forwarded-proto") == "https" else "ws"
    ws_url = f"{scheme}://{host}/api/proxy/{device_id}"

    return {"token": API_KEY, "ws_url": ws_url}


@ws_router.websocket("/{device_id}/ws/{session_id}")
async def ws_relay(ws: WebSocket, device_id: str, session_id: str):
    """Relay WebSocket messages between the browser and a remote REACHER device.

    Browser → local server (this endpoint) → Pi REACHER WebSocket.
    Events flow back: Pi → local server → browser.
    """
    if not verify_ws_token(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return

    machine = machines.get(device_id)
    if not machine:
        await ws.close(code=4004, reason="Machine not paired")
        return

    upstream_base = machine["url"].replace("http://", "ws://").replace("https://", "wss://")
    upstream_url = f"{upstream_base}/ws/{session_id}?token={machine['api_key']}"

    # Connect upstream BEFORE accepting the browser — a failure here surfaces as
    # a real WS error to the browser (code 1011) so reconnectAttempt accumulates
    # and the client's give-up logic fires instead of looping silently forever.
    try:
        upstream = await websockets.connect(upstream_url, open_timeout=10)
    except Exception as exc:
        logger.error(
            "WS relay upstream connect failed for %s/%s url=%s: %s",
            device_id[:8], session_id, upstream_url, exc, exc_info=True,
        )
        await ws.close(code=1011, reason=f"upstream {type(exc).__name__}")
        return

    await ws.accept()

    try:
        async def browser_to_pi():
            try:
                while True:
                    data = await ws.receive_text()
                    await upstream.send(data)
            except (WebSocketDisconnect, websockets.ConnectionClosed):
                pass

        async def pi_to_browser():
            try:
                async for data in upstream:
                    if isinstance(data, str):
                        await ws.send_text(data)
                    else:
                        await ws.send_bytes(data)
            except (websockets.ConnectionClosed, WebSocketDisconnect):
                pass

        tasks = [
            asyncio.create_task(browser_to_pi()),
            asyncio.create_task(pi_to_browser()),
        ]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass


@router.api_route("/{device_id}/{rest_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_request(device_id: str, rest_path: str, request: Request) -> Response:
    """Forward a REST request to a paired remote REACHER machine.

    Adds the stored Bearer token, preserves Content-Type, and streams the
    response back to the browser.  Errors from the upstream machine are
    forwarded with their original status codes.
    """
    machine = machines.get(device_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not paired")

    upstream_url = f"{machine['url']}/{rest_path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    headers: dict[str, str] = {"Authorization": f"Bearer {machine['api_key']}"}
    ct = request.headers.get("content-type")
    if ct:
        headers["Content-Type"] = ct

    body = await request.body()

    # Enrich firmware upload requests with local hex data so the remote Pi
    # doesn't need to have the files pre-installed.
    if "api/firmware/upload/" in rest_path and request.method == "POST" and body:
        try:
            payload = json.loads(body)
            if isinstance(payload, dict) and "hex_data" not in payload:
                paradigm = payload.get("paradigm", "")
                board = payload.get("board", "uno")
                try:
                    uploader = FirmwareUploader()
                    hex_path = uploader.get_hex_path(paradigm, board)
                    with open(hex_path, "rb") as f:
                        hex_bytes = f.read()
                    payload["hex_data"] = base64.b64encode(hex_bytes).decode("ascii")
                    body = json.dumps(payload).encode()
                    headers["Content-Type"] = "application/json"
                    logger.info("Injected hex_data for %s/%s into proxied firmware upload", board, paradigm)
                except FileNotFoundError:
                    logger.warning(
                        "Proxy hex enrichment skipped for %s/%s — hex file not found locally",
                        board, paradigm,
                    )
                except ValueError as exc:
                    logger.warning(
                        "Proxy hex enrichment skipped for %s/%s — %s",
                        board, paradigm, exc,
                    )
        except (json.JSONDecodeError, KeyError):
            pass  # not JSON or unexpected shape — forward as-is

    http_client: httpx.AsyncClient = request.app.state.http_client

    try:
        upstream = await http_client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Cannot reach remote machine")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Remote machine did not respond")

    # Preserve Content-Disposition/Content-Length for binary file downloads
    # (e.g. /api/file/{sid}/export/download).  Deliberately drop
    # Content-Encoding: httpx has already decoded the body on this side, so
    # forwarding a stale "gzip" would mislead the browser.
    passthrough: dict[str, str] = {}
    for h in ("content-disposition", "content-length"):
        v = upstream.headers.get(h)
        if v:
            passthrough[h] = v

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
