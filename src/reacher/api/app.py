"""FastAPI application for REACHER.

Entry-point that wires up all routers, serves the React frontend as static
files, and manages the application lifespan (session cleanup on shutdown).
"""

import logging
import os
import sys
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..session_manager import SessionManager
from .middleware.auth import require_api_key, API_KEY
from .routers import data, file, firmware, hardware, lifecycle, program, serial, session, websocket

logger = logging.getLogger(__name__)

PORT = int(os.getenv("REACHER_PORT", "6229"))
HOST = os.getenv("REACHER_HOST", "127.0.0.1")


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup/shutdown."""
    sm = SessionManager(event_callback=broadcast_event)
    app.state.session_manager = sm
    logger.info("REACHER API v%s starting on port %d", __version__, PORT)
    yield
    logger.info("Shutting down — destroying all sessions")
    sm.destroy_all()


def create_app() -> FastAPI:
    app = FastAPI(
        title="REACHER API",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization"],
    )

    @app.get("/health", tags=["health"])
    async def health_check():
        sm = app.state.session_manager
        sessions = sm.list_sessions()
        return {
            "status": "ok",
            "version": __version__,
            "active_sessions": len(sessions),
            "dropped_events": websocket.dropped_events(),
        }

    # Localhost-only token endpoint (for same-origin web frontend)
    @app.get("/api/auth/token", tags=["auth"])
    async def get_auth_token():
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
        return
    webbrowser.open(f"http://localhost:{PORT}")
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
