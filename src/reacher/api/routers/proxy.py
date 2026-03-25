"""Transparent HTTP proxy for paired remote REACHER machines.

All REST calls to a remote machine are routed through the local REACHER
server via /api/proxy/{device_id}/... — the browser never talks directly to
the remote machine, eliminating any CORS configuration requirement.

WebSocket connections still connect directly to the remote machine for
performance; the token is retrieved from GET /api/proxy/{device_id}/ws-token.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from ... import machines

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{device_id}/ws-token")
async def get_ws_token(device_id: str) -> dict:
    """Return the stored API key and WebSocket base URL for a paired machine.

    The browser uses this token for direct WebSocket connections to the
    remote machine (ws://{host}:{port}/ws/{session_id}?token=...).
    """
    machine = machines.get(device_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not paired")
    ws_url = machine["url"].replace("http://", "ws://").replace("https://", "wss://")
    return {"token": machine["api_key"], "ws_url": ws_url}


@router.api_route("/{device_id}/{rest_path:path}", methods=["GET", "POST", "DELETE"])
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

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
