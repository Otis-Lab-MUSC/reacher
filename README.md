# REACHER — Python Backend

**FastAPI server, serial communication kernel, and session manager for the REACHER ecosystem**

[![Version](https://img.shields.io/badge/version-2.0.0-blue)](https://github.com/Otis-Lab-MUSC/REACHER)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

*Written by*: Joshua Boquiren

[![](https://img.shields.io/badge/@thejoshbq-grey?style=flat&logo=github)](https://github.com/thejoshbq)

---

## Overview

The Python backend is the core of the REACHER system. It provides:

- A **FastAPI REST API** for session management, hardware control, experiment execution, and data export
- A **WebSocket server** for real-time event streaming to the browser UI
- A **multi-threaded serial communication kernel** for bidirectional JSON messaging with Arduino hardware
- A **session manager** coordinating multiple simultaneous experiment sessions with port locking
- A **firmware uploader** for flashing Arduino `.hex` files via `avrdude`

When running as a standalone executable, the backend also serves the React frontend as static files and opens a browser window automatically.

---

## Role in the REACHER Ecosystem

The Python backend is the bridge between the Arduino hardware and the browser-based UI. It:

1. Communicates with one or more Arduinos over USB serial (115200 baud, JSON messages)
2. Exposes a REST API and WebSocket endpoint on port 6229
3. Serves the React frontend as static files at the root URL
4. Manages experiment sessions — starting, stopping, pausing, and collecting data
5. Handles firmware uploads to Arduino boards via `avrdude`
6. Logs all serial events and behavioral data for post-experiment analysis

```
Arduino ◄──USB Serial──► REACHER Kernel ◄──► FastAPI ◄──► React Frontend
                          (threads)          (REST+WS)    (browser)
```

---

## Architecture

### Project Structure

```
reacher/
├── pyproject.toml              # Package metadata and dependencies
├── src/reacher/
│   ├── __init__.py             # Exports: REACHER, COMMAND_REGISTRY, CommandCode, PARADIGMS
│   ├── __main__.py             # Entry point for `python -m reacher`
│   ├── session_manager.py      # Multi-session coordinator with port locking
│   ├── api/
│   │   ├── app.py              # FastAPI app, CORS, lifespan, static file mount
│   │   └── routers/
│   │       ├── session.py      # Session CRUD
│   │       ├── serial.py       # Port listing and serial connections
│   │       ├── firmware.py     # Paradigm listing and firmware upload
│   │       ├── hardware.py     # Command dispatch and config retrieval
│   │       ├── program.py      # Start/stop/pause and limit configuration
│   │       ├── data.py         # Behavior events, frames, CSV export
│   │       ├── file.py         # Filename and destination configuration
│   │       ├── lifecycle.py    # Graceful shutdown
│   │       └── websocket.py    # Real-time event streaming
│   ├── kernel/
│   │   ├── reacher.py          # Core REACHER class (serial I/O, threading, data)
│   │   └── commands.py         # CommandCode enum, CommandSpec, COMMAND_REGISTRY
│   └── uploader/
│       └── uploader.py         # FirmwareUploader (avrdude wrapper)
└── tests/
    ├── test_commands.py
    ├── test_session_manager.py
    ├── test_api.py
    └── core/
```

### Kernel — Multi-Threaded Serial I/O

The `REACHER` class manages all communication with a single Arduino. Each instance runs three daemon threads:

| Thread | Target | Purpose |
|---|---|---|
| `serial_thread` | `read_serial()` | Continuously reads incoming serial data and queues it |
| `queue_thread` | `handle_queue()` | Processes queued messages, delegates to event handlers |
| `time_check_thread` | `monitor_time_limit()` | Enforces time and infusion limits during experiments |

Thread coordination uses `threading.Event` flags:
- `serial_flag` — cleared to read, set to stop
- `program_flag` — cleared when running, set when paused/stopped
- `time_check_flag` — monitors limit conditions

### Session Manager

The `SessionManager` coordinates multiple independent `REACHER` instances:

- **Port locking** — prevents two sessions from binding to the same COM port
- **Session states** — `idle` → `uploading` → `connected` → `running` → `paused` → `stopped`
- **Session IDs** — 12-character hexadecimal identifiers (from `uuid4`)
- **Event broadcasting** — state changes are forwarded to connected WebSocket clients

### Firmware Uploader

The `FirmwareUploader` wraps `avrdude` to flash compiled `.hex` files onto Arduino UNOs:

- Target: ATmega328P, programmer: `arduino`, baud: 115200
- Async subprocess execution with progress parsing from `avrdude` stderr
- Hex file resolution: PyInstaller bundle (`_MEIPASS/hex/`) or development path (`../firmware/hex/`)

---

## API Reference

### REST Endpoints

#### Sessions (`/api/sessions`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sessions` | List all active sessions |
| `POST` | `/api/sessions` | Create a new session (body: `{port, paradigm?}`) |
| `GET` | `/api/sessions/{id}` | Get session details |
| `POST` | `/api/sessions/{id}/reset` | Reset a session instance |
| `DELETE` | `/api/sessions/{id}` | Destroy a session |

#### Serial (`/api/serial`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/serial/ports` | List available COM/serial ports |
| `POST` | `/api/serial/{id}/connect` | Connect session to its serial port |
| `POST` | `/api/serial/{id}/disconnect` | Disconnect serial |

#### Firmware (`/api/firmware`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/firmware/paradigms` | List available paradigm hex files |
| `POST` | `/api/firmware/upload/{id}` | Upload firmware to Arduino (body: `{paradigm}`) |

#### Hardware (`/api/hardware`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/hardware/{id}/command` | Send command by code (body: `{code, value?}`) |
| `GET` | `/api/hardware/{id}/commands` | List commands available for the current paradigm |
| `GET` | `/api/hardware/{id}/config` | Get firmware info and hardware settings |

#### Program (`/api/program`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/program/{id}/start` | Start the experiment |
| `POST` | `/api/program/{id}/stop` | Stop the experiment |
| `POST` | `/api/program/{id}/pause` | Toggle pause/resume |
| `POST` | `/api/program/{id}/limit` | Set limits (body: `{type, time_limit?, infusion_limit?, delay?}`) |

#### Data (`/api/data`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/data/{id}/behavior` | Get behavioral events (supports `?since=` for pagination) |
| `GET` | `/api/data/{id}/frames` | Get frame timestamps |
| `GET` | `/api/data/{id}/export/csv` | Export behavior data as CSV download |

#### File (`/api/file`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/file/{id}/config` | Set output filename and destination (body: `{filename?, destination?}`) |
| `POST` | `/api/file/{id}/create_folder` | Create data output folder |

#### Lifecycle (`/api/lifecycle`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/lifecycle/shutdown` | Graceful shutdown (3-second grace period) |

### WebSocket

| Endpoint | Description |
|---|---|
| `ws://localhost:6229/ws/{session_id}` | Real-time event stream for a session |

**Event types sent over WebSocket:**

| Type | Description |
|---|---|
| `event` | Behavioral event (lever press, pump infusion, lick, etc.) |
| `frame` | Microscope frame timestamp |
| `config` | Firmware identification and hardware settings |
| `upload_progress` | Firmware upload progress (`{percent, stage}`) |
| `session_state` | Session state change notification |

---

## Serial Protocol

Communication with Arduino hardware uses the following protocol:

| Parameter | Value |
|---|---|
| Baud rate | 115200 |
| Encoding | UTF-8 |
| Message format | Newline-delimited JSON |
| Identification query | `*IDN?` (SCPI-style) |

### Event code meanings (firmware → backend)

| Code | Meaning |
|---|---|
| `000` | Configuration / firmware identification |
| `001` | Log messages (arm/disarm state changes) |
| `006` | Error messages |
| `007` | Behavioral events (lever presses, pump activations, licks, etc.) |
| `008` | Microscope frame timestamps |

### Command code ranges (backend → firmware)

| Range | Target |
|---|---|
| 100–105 | Controller (start, stop, identify, pause) |
| 201–220 | Session setup (ratio, paradigm parameters, Pavlovian settings) |
| 300–382 | Cue/speaker control (primary and secondary) |
| 400–482 | Pump control (primary and secondary) |
| 500–501 | Lick circuit (arm/disarm) |
| 600–682 | Laser control |
| 900–903 | Microscope control |
| 1000–1081 | Right-hand lever control |
| 1300–1381 | Left-hand lever control |

The backend's `COMMAND_REGISTRY` contains 71 `CommandSpec` entries with paradigm filtering — commands are only exposed for paradigms that use them.

---

## Installation

### From a wheel file

```bash
pip install reacher-2.0.0-py3-none-any.whl
```

### From source

```bash
git clone https://github.com/otis-lab-musc/reacher.git
pip install -e reacher/
```

---

## Running

### CLI command

```bash
reacher
```

### Module invocation

```bash
python -m reacher
```

Both start the FastAPI server on `http://localhost:6229` and open a browser window. Set the `REACHER_STATIC_DIR` environment variable to point to a built frontend directory, or run from the [labrynth](https://github.com/Otis-Lab-MUSC/labrynth) root where `web/dist/` will be found automatically.

### Port configuration

Set the `REACHER_PORT` environment variable to change the default port:

```bash
REACHER_PORT=8080 reacher
```

---

## Development

### Setting up a development environment

```bash
git clone https://github.com/otis-lab-musc/reacher.git
cd reacher
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows
pip install -e ".[dev]"
```

### Running tests

```bash
pytest
```

### Linting

```bash
ruff check .
```

---

## Building the Standalone Executable

See [labrynth](https://github.com/Otis-Lab-MUSC/labrynth) for standalone packaging via PyInstaller.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REACHER_PORT` | `6229` | HTTP/WebSocket server port |
| `REACHER_STATIC_DIR` | (CWD/web/dist) | Path to built React frontend directory |
| `REACHER_HEX_DIR` | (CWD/firmware/hex) | Path to pre-compiled firmware hex files |
| `REACHER_AVRDUDE_PATH` | (system PATH) | Path to `avrdude` binary (set during build/packaging, not runtime) |

### Data Directory

REACHER stores logs and data under `~/REACHER/`:

```
~/REACHER/
├── LOG/
│   └── YYYY-MM-DD_HH-MM-SS/
│       ├── controller_log.json    # JSON events from firmware
│       └── interface_log.log      # Python logging output
└── DATA/                          # Default data export destination
```

The data export destination can be customized per session via the File API.

---

## Dependencies

### Runtime

| Package | Version | Purpose |
|---|---|---|
| pyserial | ≥3.5 | Serial port communication |
| fastapi | ≥0.110 | REST API framework |
| uvicorn[standard] | ≥0.29 | ASGI server |
| websockets | ≥12.0 | WebSocket protocol support |

### Optional (tray extra: `pip install reacher[tray]`)

| Package | Version | Purpose |
|---|---|---|
| pystray | ≥0.19 | System tray icon (standalone mode) |
| Pillow | ≥10.0 | Image support for tray icon |

### Development

| Package | Version | Purpose |
|---|---|---|
| pytest | ≥8.0 | Test runner |
| pytest-asyncio | ≥0.23 | Async test support |
| httpx | ≥0.27 | HTTP test client |
| ruff | ≥0.4 | Linter and formatter |

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## Contact

Joshua Boquiren — [thejoshbq@proton.me](mailto:thejoshbq@proton.me)

[GitHub: otis-lab-musc/reacher](https://github.com/otis-lab-musc/reacher)
