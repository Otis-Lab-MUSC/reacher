"""FastAPI application for REACHER.

Entry-point that wires up all routers, serves the React frontend as static
files, and manages the application lifespan (session cleanup on shutdown).
"""

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__, discovery, machines, pairing, pin_overrides
from ..device_id import DEVICE_ID
from ..session_manager import SessionManager
from .middleware.auth import require_api_key, API_KEY
from .routers import data, file, firmware, hardware, lifecycle, program, serial, session, websocket
from .routers import discovery as discovery_router, pairing as pairing_router, proxy as proxy_router
from .routers import update as update_router, validate as validate_router

logger = logging.getLogger(__name__)

PORT = int(os.getenv("REACHER_PORT", "6229"))
HOST = os.getenv("REACHER_HOST", "127.0.0.1")
WS_PING_INTERVAL = int(os.getenv("REACHER_WS_PING_INTERVAL", "20"))
WS_PING_TIMEOUT = int(os.getenv("REACHER_WS_PING_TIMEOUT", "60"))


def _open_browser(url: str) -> None:
    """Open *url* in a browser, using incognito/private mode when requested.

    Checks the ``REACHER_INCOGNITO`` environment variable (set by launcher.py
    when the ``--incognito`` flag is passed).  Tries known browsers with their
    private-mode flags in order; falls back to ``webbrowser.open`` if none are
    on PATH.
    """
    if not os.getenv("REACHER_INCOGNITO"):
        webbrowser.open(url)
        return

    _INCOGNITO_BROWSERS = [
        ("google-chrome", ["--incognito"]),
        ("google-chrome-stable", ["--incognito"]),
        ("chromium-browser", ["--incognito"]),
        ("chromium", ["--incognito"]),
        ("firefox", ["--private-window"]),
        ("firefox-esr", ["--private-window"]),
        ("msedge", ["--inprivate"]),
        ("microsoft-edge", ["--inprivate"]),
    ]
    for binary, flags in _INCOGNITO_BROWSERS:
        if shutil.which(binary):
            try:
                subprocess.Popen([binary, *flags, url],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue

    # No supported browser found — fall back to default browser without incognito
    logger.warning("No incognito-capable browser found; opening default browser")
    webbrowser.open(url)


async def _post_broker_registration(
    broker_url: str,
    http_client: httpx.AsyncClient,
    device_id: str,
    port: int,
    version: str,
) -> None:
    """POST self-registration to the broker (primary machine) on startup.

    Called when ``REACHER_BROKER_URL`` is set — used on networks where mDNS
    multicast is blocked (e.g. university managed switches).  The local
    outbound IP is determined via the routing table so the primary can reach
    this device.  When ``REACHER_HOST`` is loopback (the default) the
    registered URL will be unreachable from the primary; combine
    ``REACHER_BROKER_URL`` with ``REACHER_HOST=0.0.0.0`` for LAN access.
    """
    from .. import discovery as _discovery
    local_ip = _discovery._get_local_ip() or "127.0.0.1"
    url = f"http://{local_ip}:{port}"
    try:
        await http_client.post(
            f"{broker_url}/api/discovery/register",
            json={
                "device_id": device_id,
                "url": url,
                "hostname": socket.gethostname(),
                "version": version,
            },
            timeout=10.0,
        )
        logger.info("Registered with broker at %s", broker_url)
    except Exception as exc:
        logger.warning("Could not register with broker %s: %s", broker_url, exc)


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

    # Load persisted per-port Arduino pin overrides
    pin_overrides.load()

    # Restore persisted paired state before starting code rotation so the
    # first _rotate() call respects whether this device is already paired.
    pairing.load()

    # Only start pairing code rotation on peripheral devices (no frontend bundled).
    # The main Labrynth machine has a bundled frontend and should not show codes.
    if not _resolve_static_dir():
        pairing.start_rotation()

    # Register mDNS service and start peer browser (blocking ~100ms, run off-thread)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, discovery.start, DEVICE_ID, PORT, __version__)

    # Unicast registration fallback: POST presence to a known broker when
    # REACHER_BROKER_URL is set (e.g. on university networks where mDNS is blocked).
    broker_url = os.getenv("REACHER_BROKER_URL", "").rstrip("/")
    if broker_url:
        asyncio.create_task(_post_broker_registration(broker_url, http_client, DEVICE_ID, PORT, __version__))

    # Subnet scan fallback: finds peers even when mDNS/zeroconf is unavailable
    scan_task = asyncio.create_task(discovery.run_scan_loop(http_client, PORT, DEVICE_ID))

    logger.info("REACHER API v%s listening on %s:%d", __version__, HOST, PORT)
    if HOST == "127.0.0.1" and not _resolve_static_dir():
        logger.warning(
            "Bound to loopback — remote peers cannot connect. "
            "Set REACHER_HOST=0.0.0.0 for LAN access."
        )
    if broker_url and HOST == "127.0.0.1":
        logger.warning(
            "REACHER_BROKER_URL is set but REACHER_HOST is loopback; "
            "the registered URL will be unreachable from the primary. "
            "Set REACHER_HOST=0.0.0.0 for LAN access."
        )
    # Only open the browser if a frontend is actually available — avoids opening
    # to a blank 404 on headless Pis running the bare API without a bundled UI.
    if _resolve_static_dir():
        _open_browser(f"http://localhost:{PORT}")
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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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
    app.include_router(firmware.diagnostics_router, prefix="/api/firmware", tags=["firmware"], dependencies=api_deps)
    app.include_router(hardware.router, prefix="/api/hardware", tags=["hardware"], dependencies=api_deps)
    app.include_router(program.router, prefix="/api/program", tags=["program"], dependencies=api_deps)
    app.include_router(data.router, prefix="/api/data", tags=["data"], dependencies=api_deps)
    app.include_router(file.router, prefix="/api/file", tags=["file"], dependencies=api_deps)
    app.include_router(websocket.router, tags=["websocket"])
    app.include_router(lifecycle.router, prefix="/api/lifecycle", tags=["lifecycle"])
    # Zero-config machine discovery: pairing + register endpoints are auth-free;
    # other discovery routes require auth.
    app.include_router(pairing_router.router, prefix="/api/pairing", tags=["pairing"])
    # /register is auth-free — peripheral devices call it before pairing
    app.include_router(discovery_router.register_router, prefix="/api/discovery", tags=["discovery"])
    app.include_router(discovery_router.router, prefix="/api/discovery", tags=["discovery"], dependencies=api_deps)
    app.include_router(proxy_router.router, prefix="/api/proxy", tags=["proxy"], dependencies=api_deps)
    # Proxy WebSocket relay — registered WITHOUT HTTP auth deps (WebSocket
    # upgrades are incompatible with HTTPBearer); auth is handled via
    # verify_ws_token() inside the endpoint.
    app.include_router(proxy_router.ws_router, prefix="/api/proxy", tags=["proxy"])
    app.include_router(validate_router.router, prefix="/api/validate", tags=["validate"], dependencies=api_deps)
    app.include_router(update_router.router, prefix="/api/update", tags=["update"], dependencies=api_deps)

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
            _open_browser(f"http://localhost:{PORT}")
        return
    # Only peripheral devices (no bundled frontend) show pairing codes.
    # The main Labrynth machine has the frontend and discovers Pis, not the reverse.
    # start_rotation() already prints the code via _rotate(); no duplicate print needed.
    if not _resolve_static_dir():
        pairing.load()
        pairing.start_rotation()
    print(f"  API key      : {API_KEY}")
    uvicorn.run(
        "reacher.api.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        log_config=None if getattr(sys, "frozen", False) else uvicorn.config.LOGGING_CONFIG,
        ws_ping_interval=WS_PING_INTERVAL,
        ws_ping_timeout=WS_PING_TIMEOUT,
    )


if __name__ == "__main__":
    main()
