# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

REACHER is a Python backend server that bridges Arduino hardware and a React browser UI for behavioral neuroscience experiments. It manages serial communication with Arduino devices, exposes a REST + WebSocket API, handles multi-session coordination, and supports firmware uploading.

## Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run the server
python -m reacher
# or after install:
reacher

# Run all tests
pytest

# Run a single test file
pytest tests/test_api.py

# Run a single test by name
pytest tests/test_api.py::test_function_name -v

# Lint
ruff check .

# Build wheel
python -m build
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `REACHER_PORT` | `6229` | HTTP/WebSocket port |
| `REACHER_HOST` | `0.0.0.0` | Bind address |
| `REACHER_STATIC_DIR` | `web/dist/` | React frontend directory |
| `REACHER_HEX_DIR` | `firmware/hex/` | Pre-compiled firmware hex files |
| `REACHER_CORS_ORIGINS` | None | Extra allowed CORS origins (comma-separated) |

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
- Auth: Bearer token (API key generated at startup); `/health` is exempt for mDNS discovery
- 13 routers under `api/routers/`: `session`, `serial`, `firmware`, `hardware`, `program`, `data`, `file`, `websocket`, `discovery`, `pairing`, `proxy`, `lifecycle`

### Firmware Uploader (`src/reacher/uploader/`)
Wraps `avrdude` to flash Arduino firmware. Handles PyInstaller frozen mode path resolution (`_MEIPASS/hex/`) and streams upload progress via callback. Board profiles in `boards.py`.

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

## Data Output

Experiments write to `~/REACHER/`:
```
~/REACHER/
├── LOG/YYYY-MM-DD_HH-MM-SS/
│   ├── controller_log.json
│   └── interface_log.log
└── DATA/
```
