# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

REACHER is a Python backend server that bridges Arduino hardware and a React browser UI for behavioral neuroscience experiments. It manages serial communication with Arduino devices, exposes a REST + WebSocket API, handles multi-session coordination, and supports firmware uploading.

## Commands

```bash
# Install for development
pip install -e ".[dev]"
pip install -e ".[tray]"          # adds pystray + Pillow for system-tray icon

# Run the server (FastAPI on REACHER_PORT)
python -m reacher
reacher                            # console script (entry: reacher.api.app:main)

# Run the read-only terminal dashboard against a running server
reacher-monitor                    # localhost:6229
reacher-monitor --url http://host:6229 --refresh 5

# Tests
pytest                             # all
pytest tests/test_api.py           # single file
pytest tests/test_api.py::test_function_name -v   # single test

# Lint / format (target py310, line-length 120)
ruff check .
ruff format .

# Build wheel
python -m build
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `REACHER_PORT` | `6229` | HTTP/WebSocket port |
| `REACHER_HOST` | `0.0.0.0` | Bind address (the parent `CLAUDE.md` lists `127.0.0.1` — code default is `0.0.0.0`) |
| `REACHER_STATIC_DIR` | `web/dist/` | React frontend directory |
| `REACHER_HEX_DIR` | package data (`src/reacher/hex/`) | Override dir for pre-compiled firmware hex files |
| `REACHER_CORS_ORIGINS` | None | Extra allowed CORS origins (comma-separated) |
| `REACHER_API_KEY` | auto-generated | Bearer token; auto-written to `~/.reacher/api_key` if unset |
| `REACHER_AVRDUDE_PATH` | system PATH | Path to `avrdude` binary (set during PyInstaller packaging) |

## Architecture

The system is organized in four layers:

```
Arduino ◄──USB Serial──► Kernel ◄──► Session Manager ◄──► FastAPI ◄──► React Frontend
```

### Kernel (`src/reacher/kernel/`)
The `REACHER` class manages a single Arduino instance. It runs three daemon threads:
1. `serial_thread` — reads incoming JSON-line data from serial (115200 baud)
2. `queue_thread` — processes queued messages and dispatches to event handlers
3. `time_check_thread` — enforces experiment time/infusion limits

Commands are defined in `commands.py` as a `COMMAND_REGISTRY` (71 entries), each with a `CommandSpec` that includes paradigm filtering (FR, PR, VI, Omission, Pavlovian). `simulator.py` provides a hardware-free test stub.

### Session Manager (`src/reacher/session_manager.py`)
Coordinates multiple independent `REACHER` instances. Enforces port locking (prevents two sessions from binding the same COM port). Session lifecycle: `idle → uploading → connected → running → paused → stopped`. Sessions are identified by 12-character hex strings.

### FastAPI App (`src/reacher/api/`)
- `app.py` — lifespan management, CORS, static file mounting, auth middleware
- `middleware/auth.py` — Bearer-token gate over `/api/*`; `/health` is exempt (used by mDNS discovery and `reacher-monitor`); WebSocket auth uses `?token=<key>` query param
- 12 routers under `api/routers/`: `session`, `serial`, `firmware`, `hardware`, `program`, `data`, `file`, `websocket`, `discovery`, `pairing`, `proxy`, `lifecycle`
- `routers/proxy.py` — transparent HTTP + WebSocket proxy for paired remote machines (`/api/proxy/{device_id}/...`). The browser always talks to the local server, eliminating CORS configuration; WebSockets authenticate against the *local* API key via a short-lived ws-token.

### Pin Overrides (`src/reacher/pin_overrides.py`)
Persistent per-port Arduino pin remapping at `~/.reacher/pin_overrides.json` (mode `0o600`), keyed by serial port path. Owns the single source of truth for board pin validation metadata (UNO/Mega digital/PWM/interrupt sets) and the component→`CommandCode` mapping, shared between the HTTP router and the serial-connect replay path that re-applies overrides on every reconnect.

### Discovery and Pairing (zero-config peer setup)
- `discovery.py` — advertises `_reacher._tcp.local.` over mDNS via `zeroconf` (soft dependency; degrades gracefully when missing). Also tracks unicast `/api/discovery/register` self-registrations as a fallback for networks that block multicast.
- `pairing.py` — rotating 6-digit code (5-min interval) printed to stdout and validated by `/api/pairing/claim`, so API keys never travel through mDNS or QR codes. State lives in `~/.reacher/paired`.
- `machines.py` — persistent paired-peer store at `~/.reacher/machines.json` (mode `0o600`), keyed by `device_id`.
- `device_id.py` — stable per-host identifier used by discovery/pairing.
- `monitor.py` (`reacher-monitor` script) — Rich-based terminal dashboard showing pairing code, health, and session state; designed to run on the host's local display independently of any SSH session.

### Firmware Uploader (`src/reacher/uploader/`)
Wraps `avrdude` to flash Arduino firmware. Handles PyInstaller frozen mode path resolution (`_MEIPASS/hex/`) and streams upload progress via callback. `boards.py` is the board-profile registry — each entry maps a `board_id` to a display name, an Arduino CLI FQBN, and the `avrdude` argument tuple. Adding a new board is a single entry in `BOARD_PROFILES`. Hex resolution prefers package data (`src/reacher/hex/`) as canonical; the GitHub fallback fetches from this repo (`Otis-Lab-MUSC/reacher`, `src/reacher/hex/`) for bare `pip install` hosts.

### Firmware Source (`firmware/`)
Arduino firmware source, folded in from the archived `Otis-Lab-MUSC/reacher-firmware`. Five sketches (`fr/ pr/ vi/ omission/ pavlovian/`) share `libraries/REACHERDevices/`. `firmware/libraries/REACHERDevices/src/Commands.h` is the firmware-side command list mirrored by `kernel/commands.py`; **edit both together** when adding a command — `tests/test_command_parity.py` enforces parity. `firmware/compile.sh` writes hex into the committed package-data tree `src/reacher/hex/<board>/` (run `arduino-cli core install arduino:avr` once, then `bash firmware/compile.sh`; commit the refreshed hex). Firmware version strings are stamped by `scripts/bump-version.py` — never hand-edit, and recompile hex after a bump. Target board is Mega 2560; `uno/` hex is legacy. The microscope timestamp pin (INT0) is fixed in firmware and must not be exposed as remappable. See `firmware/CLAUDE.md` and `firmware/README.md` for paradigm/hardware detail.

### systemd integration
`systemd/reacher@.service` and `systemd/reacher-monitor@.service` are templated unit files (`%i` = username) for running the API and the dashboard as services on Linux hosts (e.g. a lab Raspberry Pi).

## Serial Protocol

- **Format**: Newline-delimited JSON at 115200 baud
- **Identification**: SCPI-style `*IDN?` handshake on connect

**Firmware → backend event codes:**
- `000` — config/firmware ID
- `001` — log/state changes
- `006` — errors
- `007` — behavioral events (lever, pump, lick)
- `008` — microscope frame timestamps

**Backend → firmware command code ranges:**
- 100–105: Controller
- 201–220: Session setup
- 300–382: Cue/speaker
- 400–482: Pump
- 500–501: Lick circuit
- 600–682: Laser
- 900–903: Microscope
- 1000–1081: Right lever
- 1300–1381: Left lever

## Testing

Tests use `pytest` with `asyncio_mode=auto` (configured in `pyproject.toml`). The test suite relies on mocked serial/hardware via `simulator.py` and `pytest-mock`. Key test files:
- `tests/test_api.py` — FastAPI integration (uses `TestClient`)
- `tests/test_session_manager.py` — session lifecycle and port locking
- `tests/core/test_reacher.py` — kernel serial threading and event handling
- `tests/test_commands.py` — command registry validation
- `tests/test_websocket.py` — WebSocket event streaming
- `tests/test_pin_overrides.py` — pin override persistence, validation, and serial-reconnect replay

## Docs & Scripts

- `docs/setup-guide.md` — end-user setup walkthrough (host install, pairing, systemd).
- `scripts/install.sh` — host-side installer.
- `scripts/bump-version.py` — single source of truth for the package version; updates `pyproject.toml`, `src/reacher/__init__.py`, and the firmware version strings (`firmware/libraries/REACHERDevices/library.properties` + each sketch's `SendIdentification()`) in one shot. After bumping, recompile firmware hex (`bash firmware/compile.sh`) so the shipped binaries report the new version.

## Data Output

Live per-event logs write to `~/REACHER/LOG/`:
```
~/REACHER/
└── LOG/YYYY-MM-DD_HH-MM-SS/
    ├── controller_log.json
    └── interface_log.log
```

Export ZIPs write to the user-configured Destination (`POST /api/file/{id}/config`).
When no destination is configured, the fallback is `~/Downloads`. The fallback is
**not** persisted — `get_data_destination()` remains unset until the user explicitly
saves a destination via the UI or API.
