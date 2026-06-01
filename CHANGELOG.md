# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- AI-assisted session config validation endpoint (`POST /api/validate/config`): forwards the assembled session config to a local Ollama model and returns structured warnings (field, message, severity) before `start_program()` fires; degrades gracefully to empty warnings when Ollama is unreachable
- `REACHER_OLLAMA_URL` and `REACHER_OLLAMA_MODEL` env vars for configuring the local Ollama endpoint (defaults: `http://localhost:11434`, `qwen2.5:7b`)

### Fixed
- CORS `allow_methods` now includes `PUT` (hardware pin-assignment endpoint was missing this method for browser clients)

---

## [2.0.2] - 2026-05-29

### Fixed
- CONTROLLER END event now reliably lands in `behavior_data` before export — `stop_program()` waits for the firmware's END acknowledgement (up to 8s) rather than a fixed 2s sleep, preventing the final session event from being silently dropped
- `program_running` guard relaxed for CONTROLLER device events so START/END markers are always persisted regardless of stop-sequence timing

---

## [2.0.1] - 2026-04-07

### Added
- `reacher-monitor` terminal dashboard (Rich) showing pairing code, health, and session state; bouncing ASCII animation while session is running
- mDNS service advertisement (`_reacher._tcp.local.`) via `zeroconf` for zero-config peer discovery
- Pairing system with rotating 6-digit codes and `/api/pairing/claim` endpoint; paired-peer store at `~/.reacher/machines.json`
- Transparent HTTP and WebSocket proxy router (`/api/proxy/{device_id}/...`) so the browser never holds remote API keys
- Per-port Arduino pin override system (`~/.reacher/pin_overrides.json`) with `*_SET_PIN` command replay on reconnect
- Board profile registry (`boards.py`) supporting Arduino UNO and Mega 2560
- `systemd/reacher@.service` and `systemd/reacher-monitor@.service` unit files for Linux host deployment; `TERM=linux` fix for bare-tty monitor
- Bearer-token authentication on all `/api/*` routes; auto-generated key at `~/.reacher/api_key`; WebSocket uses `?token=<key>`
- Session segmentation via SPLIT and RESTART commands
- `GET /browse` endpoint for native folder picker dialog (zenity on Linux, tkinter fallback)
- Firmware readiness gate: kernel holds command execution until firmware IDN handshake completes
- Mega 2560 hex files added alongside UNO artifacts
- PyPI publishing workflow and `scripts/install.sh` for Raspberry Pi hosts

### Changed
- File export default destination aligned to `~/Downloads`; persisted fallback destination removed
- Idle-timer and shutdown-timer configuration added to watchdog

### Fixed
- Watchdog no longer triggers shutdown before serial connection is established
- Controller log file handle kept open with batched fsync (Bug 4.9)
- Event callback failures counted and exposed on session endpoint (Bug 7.4)
- Throttled WebSocket warning emitted on serial queue overflow (Bug 2.6)
- Resilient wrapper added for kernel daemon threads to survive transient exceptions (Bug 7.1)
- Proxy websocket relay now connects upstream before accepting browser connection
- `PUT` and `PATCH` methods added to proxy route
- avrdude DLL dependencies bundled; `-C` flag passed in frozen mode; errors surfaced on firmware endpoint
- Lifecycle shutdown endpoint made auth-free for `sendBeacon` on tab close
- uvicorn `dictConfig` disabled in frozen (PyInstaller) mode to prevent crash
- Frame events gated on `program_flag` to prevent post-stop data capture
- ZIP export now includes all segment CSVs and event log
- Export archive suffix stripped from session filename
- `409` returned for hardware commands on idle or unconnected sessions
- Pavlovian laser state (691–695) tracked in command state map
- Paradigm and deprecation checks enforced on command dispatch
- `trial_type` passed through for Pavlovian `TRIAL_START` events
- Microscope frame CSV skipped when no data was captured (avoids empty file in export)

---

## [2.0.0] - 2026-02-23

_Changelog tracking started at this version. Earlier history not recorded._

### Added
- FastAPI backend with REST + WebSocket server on port 6229
- Multi-threaded serial communication kernel (`REACHER` class) with three daemon threads: `serial_thread`, `queue_thread`, `time_check_thread`
- `SessionManager` coordinating multiple simultaneous sessions with port locking
- `FirmwareUploader` wrapping `avrdude` for flashing `.hex` files to Arduino
- 71-entry `COMMAND_REGISTRY` with paradigm filtering (FR, PR, VI, Omission, Pavlovian)
- 12 REST routers: sessions, serial, firmware, hardware, program, data, file, websocket, discovery, pairing, proxy, lifecycle
- WebSocket real-time event streaming per session
- Newline-delimited JSON serial protocol at 115200 baud; `*IDN?` handshake; event level codes `000/001/006/007/008`
