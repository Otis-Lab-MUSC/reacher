"""FastAPI application for REACHER.

Entry-point that wires up all routers, serves the React frontend as static
files, and manages the application lifespan (session cleanup on shutdown).
"""

import asyncio
import logging
import os
import socket
import sys
import webbrowser
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__, discovery, machines, pairing
from ..device_id import DEVICE_ID
from ..session_manager import SessionManager
from .middleware.auth import require_api_key, API_KEY
from .routers import data, file, firmware, hardware, lifecycle, program, serial, session, websocket
from .routers import discovery as discovery_router, pairing as pairing_router, proxy as proxy_router

logger = logging.getLogger(__name__)

PORT = int(os.getenv("REACHER_PORT", "6229"))
HOST = os.getenv("REACHER_HOST", "0.0.0.0")


def _is_already_running() -> bool:
    """Check if a REACHER server is already listening on PORT."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _resolve_static_dir():
    """Return the path to the built React frontend, or None."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = os.path.join(base, "static")
    else:
        candidate = os.environ.get("REACHER_STATIC_DIR", "")
        if not candidate:
            # Fallback: check CWD for web/dist (works when launched from labrynth root)
            candidate = os.path.join(os.getcwd(), "web", "dist")
    if os.path.isdir(candidate):
        return candidate
    return None


def broadcast_event(session_id: str, event_type: str, data: dict):
    """Enqueue an event for WebSocket delivery.

    Called from REACHER instances (running in background threads) via the
    ``event_callback`` parameter.  The actual sending happens in the WS
    router's background task.
    """
    websocket.enqueue_event(session_id, event_type, data)


class _HealthCORSMiddleware(BaseHTTPMiddleware):
    """Allow any origin to reach /health for device discovery.

    All other routes keep the configured CORS policy.  The /health endpoint
    returns only non-sensitive metadata (status, device_id, hostname) so
    wildcard access is safe for LAN deployments.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            if request.method == "OPTIONS":
                from starlette.responses import Response
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type",
                    },
                )
            response = await call_next(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup/shutdown."""
    sm = SessionManager(event_callback=broadcast_event)
    app.state.session_manager = sm

    # Shared httpx client for proxy calls and pairing handshakes
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0))
    app.state.http_client = http_client

    # Load previously paired machines from disk
    machines.load()

    # Start pairing code rotation (idempotent — may already be started by main())
    pairing.start_rotation()

    # Register mDNS service and start peer browser (blocking ~100ms, run off-thread)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, discovery.start, DEVICE_ID, PORT, __version__)

    # Subnet scan fallback: finds peers even when mDNS/zeroconf is unavailable
    scan_task = asyncio.create_task(discovery.run_scan_loop(http_client, PORT, DEVICE_ID))

    logger.info("REACHER API v%s starting on port %d", __version__, PORT)
    # Only open the browser if a frontend is actually available — avoids opening
    # to a blank 404 on headless Pis running the bare API without a bundled UI.
    if _resolve_static_dir():
        webbrowser.open(f"http://localhost:{PORT}")
    yield

    logger.info("Shutting down — destroying all sessions")
    sm.destroy_all()
    pairing.stop_rotation()
    scan_task.cancel()
    try:
        await scan_task
    except asyncio.CancelledError:
        pass
    await loop.run_in_executor(None, discovery.stop)
    await http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="REACHER API",
        version=__version__,
        lifespan=lifespan,
    )

    # Fix: PY-008 — Allow additional CORS origins via env var
    cors_origins = [f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"]
    extra_origins = os.getenv("REACHER_CORS_ORIGINS", "")
    if extra_origins:
        cors_origins.extend(o.strip() for o in extra_origins.split(",") if o.strip())

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization"],
    )
    # Outermost middleware: override CORS on /health so any origin can discover
    # this device before REACHER_CORS_ORIGINS is configured on remote machines.
    app.add_middleware(_HealthCORSMiddleware)

    @app.get("/health", tags=["health"])
    async def health_check():
        sm = app.state.session_manager
        sessions = sm.list_sessions()
        return {
            "status": "ok",
            "service": "reacher",
            "device_id": DEVICE_ID,
            "hostname": socket.gethostname(),
            "version": __version__,
            "active_sessions": len(sessions),
            "dropped_events": websocket.dropped_events(),
        }

    # Fix: PY-001 — Restrict token endpoint to same-origin requests
    _allowed_origins = set(cors_origins)

    @app.get("/api/auth/token", tags=["auth"])
    async def get_auth_token(request: Request):
        origin = request.headers.get("origin")
        if origin is not None and origin not in _allowed_origins:
            raise HTTPException(status_code=403, detail="Forbidden")
        return {"token": API_KEY}

    # Register routers — all /api/* routes require auth
    api_deps = [Depends(require_api_key)]
    app.include_router(session.router, prefix="/api/sessions", tags=["sessions"], dependencies=api_deps)
    app.include_router(serial.router, prefix="/api/serial", tags=["serial"], dependencies=api_deps)
    app.include_router(firmware.router, prefix="/api/firmware", tags=["firmware"], dependencies=api_deps)
    app.include_router(hardware.router, prefix="/api/hardware", tags=["hardware"], dependencies=api_deps)
    app.include_router(program.router, prefix="/api/program", tags=["program"], dependencies=api_deps)
    app.include_router(data.router, prefix="/api/data", tags=["data"], dependencies=api_deps)
    app.include_router(file.router, prefix="/api/file", tags=["file"], dependencies=api_deps)
    app.include_router(websocket.router, tags=["websocket"])
    app.include_router(lifecycle.router, prefix="/api/lifecycle", tags=["lifecycle"], dependencies=api_deps)
    # Zero-config machine discovery: pairing endpoint is auth-free; others require auth
    app.include_router(pairing_router.router, prefix="/api/pairing", tags=["pairing"])
    app.include_router(discovery_router.router, prefix="/api/discovery", tags=["discovery"], dependencies=api_deps)
    app.include_router(proxy_router.router, prefix="/api/proxy", tags=["proxy"], dependencies=api_deps)

    # Serve built React frontend at /
    static_dir = _resolve_static_dir()
    if static_dir:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
        logger.info("Serving frontend from %s", static_dir)

    return app


app = create_app()


def main():
    """CLI entry-point (``reacher`` command)."""
    if _is_already_running():
        print(f"REACHER is already running on port {PORT}.")
        print(f"Visit http://localhost:{PORT} in your browser.")
        if _resolve_static_dir():
            webbrowser.open(f"http://localhost:{PORT}")
        return
    # Generate pairing code before starting the server so it's printed immediately.
    # The lifespan start_rotation() call is idempotent and will not re-generate.
    pairing.start_rotation()
    code = pairing.get_current_code()
    print(f"  Pairing code : {code[:3]}-{code[3:]}  (rotates every 5 minutes)")
    print(f"  API key      : {API_KEY}")
    uvicorn.run(
        "reacher.api.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        ws_ping_interval=15,
        ws_ping_timeout=30,
    )


if __name__ == "__main__":
    main()
