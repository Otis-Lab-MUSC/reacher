"""reacher-monitor — live terminal dashboard for a REACHER API instance.

Displays pairing code (with countdown), server health, and session state in a
full-screen terminal UI.  Designed to run on the device itself (Raspberry Pi,
laptop) so the information is visible on the local display independent of any
SSH session used to start the server.

Usage
-----
    reacher-monitor                        # connects to localhost:6229
    reacher-monitor --url http://pi:6229   # target a specific host
    reacher-monitor --refresh 5            # poll every 5 s (default 3 s)

The API key is read from the ``REACHER_API_KEY`` environment variable or
``~/.reacher/api_key``.  Without a key the server health is still shown, but
pairing and session panels are unavailable.
"""

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)
_CRASH_LOG = Path("/tmp/reacher-monitor.log")

_DEFAULT_PORT = int(os.getenv("REACHER_PORT", "6229"))
_DEFAULT_URL = f"http://localhost:{_DEFAULT_PORT}"
_API_KEY_FILE = Path.home() / ".reacher" / "api_key"

_STATE_STYLES: dict[str, str] = {
    "running": "bold green",
    "paused": "yellow",
    "stopped": "red",
    "uploading": "cyan",
    "connected": "blue",
    "idle": "dim",
}

# ---------------------------------------------------------------------------
# Mouse animation — shown when a session is running
# ---------------------------------------------------------------------------

_MOUSE_RIGHT = "~~(:>"
_MOUSE_LEFT = "<:)~~"
_MOUSE_TRACK = 16  # character positions of travel


def _mouse_frame(tick: int) -> str:
    """Return one frame of the bouncing-mouse animation."""
    period = _MOUSE_TRACK * 2 - 2  # 30 ticks per full bounce
    cycle = tick % period if period > 0 else 0
    if cycle < _MOUSE_TRACK:
        return " " * cycle + _MOUSE_RIGHT
    return " " * (period - cycle) + _MOUSE_LEFT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_api_key() -> str:
    key = os.getenv("REACHER_API_KEY", "").strip()
    if not key and _API_KEY_FILE.exists():
        key = _API_KEY_FILE.read_text().strip()
    return key


def _fmt_countdown(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Shared state — written by the poll loop, read by the render loop
# ---------------------------------------------------------------------------

class _State:
    __slots__ = ("health", "pairing", "sessions", "error", "frame")

    def __init__(self) -> None:
        self.health: dict | None = None
        self.pairing: dict | None = None
        self.sessions: list[dict] = []
        self.error: str = ""
        self.frame: int = 0


# ---------------------------------------------------------------------------
# Rich panel builders
# ---------------------------------------------------------------------------

def _header(state: _State, base_url: str) -> Panel:
    h = state.health
    t = Text(justify="center")
    t.append("REACHER MONITOR", style="bold white")
    if h:
        t.append(f"  v{h.get('version', '?')}", style="dim white")
        t.append("  |  ", style="dim")
        t.append(h.get("hostname", "?"), style="cyan")
        t.append("  |  ", style="dim")
        t.append(h.get("device_id", "?")[:12], style="dim white")
    else:
        t.append(f"  {base_url}", style="dim")
    return Panel(t, box=box.HEAVY_HEAD, style="bold")


def _status_panel(state: _State) -> Panel:
    h = state.health
    if h is None:
        body = Text()
        body.append("  OFFLINE\n", style="bold red")
        if state.error:
            body.append(f"\n  {state.error}", style="dim red")
    else:
        body = Text()
        body.append("  ONLINE\n\n", style="bold green")
        body.append(f"  Sessions:       ", style="dim")
        body.append(str(h.get("active_sessions", 0)), style="white")
        dropped = h.get("dropped_events", 0)
        body.append(f"\n  Dropped events: ", style="dim")
        body.append(str(dropped), style="yellow" if dropped else "dim")
    return Panel(body, title="[bold]Server[/bold]", box=box.ROUNDED, padding=(0, 1))


def _pairing_panel(state: _State) -> Panel:
    p = state.pairing
    if p is None:
        body = Text("\n  Unavailable\n", style="dim")
        return Panel(body, title="[bold]Pairing[/bold]", box=box.ROUNDED, padding=(0, 1))

    body = Text()
    if p.get("paired"):
        body.append("\n  PAIRED\n", style="bold green")
        body.append("\n  Code rotation continues silently.", style="dim")
    else:
        code = p.get("code", "------")
        fmt = f"{code[:3]}-{code[3:]}" if len(code) == 6 else code
        secs = p.get("seconds_until_rotation", 0.0)
        body.append("\n  UNPAIRED\n\n", style="bold yellow")
        body.append("  Code:  ", style="dim")
        body.append(fmt, style="bold cyan")
        body.append("\n\n  Rotates in:  ", style="dim")
        body.append(_fmt_countdown(secs), style="white")
    return Panel(body, title="[bold]Pairing[/bold]", box=box.ROUNDED, padding=(0, 1))


def _sessions_panel(state: _State) -> Panel:
    any_running = any(s.get("state") == "running" for s in state.sessions)

    if not state.sessions:
        body: object = Text("\n  No active sessions.", style="dim")
    else:
        t = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False, padding=(0, 1))
        t.add_column("Session ID", style="dim", no_wrap=True, min_width=12)
        t.add_column("Port", no_wrap=True)
        t.add_column("Board", no_wrap=True)
        t.add_column("Paradigm")
        t.add_column("State")

        for s in state.sessions:
            state_str = s.get("state", "?")
            style = _STATE_STYLES.get(state_str, "white")
            t.add_row(
                s.get("session_id", "?")[:12],
                s.get("port", "?"),
                s.get("board") or "—",
                s.get("paradigm") or "—",
                Text(state_str, style=style),
            )
        body = t

    if any_running:
        mouse = Text("  " + _mouse_frame(state.frame), style="dim green")
        body = Group(body, Text(), mouse)

    return Panel(body, title="[bold]Sessions[/bold]", box=box.ROUNDED, padding=(0, 0))


def _build_display(state: _State, base_url: str) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="middle", size=9),
        Layout(name="sessions"),
    )
    layout["middle"].split_row(
        Layout(name="status"),
        Layout(name="pairing"),
    )
    layout["header"].update(_header(state, base_url))
    layout["status"].update(_status_panel(state))
    layout["pairing"].update(_pairing_panel(state))
    layout["sessions"].update(_sessions_panel(state))
    return layout


# ---------------------------------------------------------------------------
# Poll loop — runs concurrently with the render loop
# ---------------------------------------------------------------------------

async def _poll(state: _State, base_url: str, api_key: str, interval: float) -> None:
    auth = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            # /health requires no auth — always attempt
            try:
                r = await client.get(f"{base_url}/health")
                state.health = r.json() if r.status_code == 200 else None
                state.error = "" if r.status_code == 200 else f"HTTP {r.status_code}"
            except httpx.ConnectError:
                state.health = None
                state.error = "Connection refused"
            except Exception as exc:
                state.health = None
                state.error = str(exc)[:60]

            if auth:
                try:
                    r = await client.get(f"{base_url}/api/pairing/status", headers=auth)
                    state.pairing = r.json() if r.status_code == 200 else None
                except Exception:
                    state.pairing = None

                try:
                    r = await client.get(f"{base_url}/api/sessions", headers=auth)
                    if r.status_code == 200:
                        state.sessions = r.json().get("sessions", [])
                except Exception:
                    pass

            await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _run(base_url: str, api_key: str, refresh: float) -> None:
    state = _State()
    # force_terminal=True ensures rich renders even on bare /dev/tty1 where
    # capability detection may fail.
    console = Console(force_terminal=True)

    poll_task = asyncio.create_task(_poll(state, base_url, api_key, refresh))

    with Live(
        _build_display(state, base_url),
        refresh_per_second=2,
        screen=True,
        console=console,
    ) as live:
        try:
            while True:
                live.update(_build_display(state, base_url))
                state.frame += 1
                await asyncio.sleep(0.5)
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    # Ensure TERM is set — bare tty1 after autologin may lack it, which causes
    # rich (and curses) to fail immediately.
    if not os.environ.get("TERM"):
        os.environ["TERM"] = "linux"

    parser = argparse.ArgumentParser(
        description="Live terminal dashboard for a REACHER API instance.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=_DEFAULT_URL,
        metavar="URL",
        help="Base URL of the REACHER API",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=3.0,
        metavar="SECS",
        help="Polling interval in seconds",
    )
    args = parser.parse_args()

    # Retry loop — if the dashboard crashes (e.g. terminal not ready yet after
    # boot), log the error and retry after a delay instead of exiting
    # immediately, which would trigger a getty restart storm.
    while True:
        try:
            asyncio.run(_run(args.url, _load_api_key(), args.refresh))
            break  # clean exit (shouldn't happen normally)
        except KeyboardInterrupt:
            break
        except Exception:
            import traceback

            msg = traceback.format_exc()
            try:
                _CRASH_LOG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n")
            except OSError:
                pass
            # Wait before retrying to avoid rapid restart loops
            time.sleep(5)


if __name__ == "__main__":
    main()
