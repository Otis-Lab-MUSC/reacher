# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [3.0.0-beta.3] - 2026-06-15

### Fixed
- Infusion-limit counter (`_infusion_count`) now increments for operant-paradigm sessions (FR/PR/VI/Omission); the counter checked only for `device == "PUMP"` but operant firmware emits `device: "PUMP_1"`, so `check_limit_met()` was permanently unsatisfied for these paradigms ([labrynth#45](https://github.com/Otis-Lab-MUSC/labrynth/issues/45))

---

## [3.0.0-alpha.1] - 2026-06-12

_First release of the **v3** line and the first public release since `v1.1.1`.
The v2 series lived only on the `develop` branch (tagged `*-dev`) and was never
published as a stable release; this alpha consolidates all of that work into the
new `main`-based release flow. Pre-stable, intended for lab/internal testing._

### Added
- Version bumped to **3.0.0-alpha.1**, the first cut of the v3 major line under the new semver prerelease policy (`alpha → beta → rc → stable`).
- **Firmware source folded into this repo** at `firmware/` (sketches, `libraries/REACHERDevices/`, `compile.sh`, Doxyfile) — imported from the now-archived `Otis-Lab-MUSC/reacher-firmware` at `5c63fa7`. `firmware/compile.sh` writes hex into the committed package-data tree `src/reacher/hex/<board>/`
- `tests/test_command_parity.py` — parses `firmware/libraries/REACHERDevices/src/Commands.h` and asserts parity with the `CommandCode` enum (skips when firmware source is absent, e.g. installed-wheel runs); `KNOWN_BACKEND_ONLY` whitelists the `CUE_SET_PULSE_*` codes (374/375/384/385) that the Pavlovian UI sends but no sketch parses yet
- `GET /api/serial/ports` now includes a `portBoards` map (`{device: board_id | null}`) alongside the `ports` list; uses `detect_board_from_port()` USB VID/PID lookup so Labrynth can auto-fill the firmware upload board selector without a separate API call
- Validation rules 37–40 in `_check_temporal()`: warn when a lever's `timeout` is shorter than a contingent cue's onset delay + duration; when `timeout == 0` the warning is unconditional — back-to-back presses are guaranteed to overlap cue playback

### Changed
- Firmware version is now coupled to the package version: `scripts/bump-version.py` also stamps `firmware/libraries/REACHERDevices/library.properties` and each sketch's `SendIdentification()` string (recompile hex after a bump). Fixed the prior sketch-vs-library version mismatch — all firmware version strings now track the package version (this release: `v3.0.0-alpha.1`)
- Firmware uploader hex resolution: dropped the stale monorepo cwd candidates (`labrynth/firmware/hex`, `reacher-firmware/hex`); the GitHub recovery fetch now targets `Otis-Lab-MUSC/reacher` (`src/reacher/hex`) since `reacher-firmware` was archived. Package data remains the canonical source

---

## [2.3.2-dev] - 2026-06-09

### Added
- `CUE_SET_LEVER_FILTER (378)`, `CUE2_SET_LEVER_FILTER (388)`, `PUMP_SET_LEVER_FILTER (478)`, `PUMP2_SET_LEVER_FILTER (488)` registered in `CommandCode` and `COMMAND_REGISTRY` with `payload_key="filter"`, `payload_type="int"`, paradigm filter `["fr", "pr", "vi", "omission"]`; supports the new per-device lever routing UI in Labrynth
- Laser `delay` parameter (command 673) added to `_VALUE_RANGES` validation; laser `rh_lever` mode (684) added to `_COMMAND_STATE_MAP`
- Session config validation endpoint (`POST /api/validate/config`): validates the assembled session config against 43 deterministic rules before `start_program()` fires; returns structured warnings (`field`, `message`, `severity`) grouped into five rule categories — paradigm required fields, hardware device checks, session limit conflicts, temporal ordering constraints, and Pavlovian-specific rules; degrades gracefully to empty warnings on any internal error so session start is never blocked
- `validators.py` — pure-Python rule engine; rules cover all five paradigms (FR, PR, VI, Omission, Pavlovian), pump/cue/laser duration-zero detection, temporal ordering (trace interval and lever timeout vs session time limit with ms↔s unit conversion), Pavlovian CS-tone frequency identity, trial count firmware limit (128) enforcement, cue + trace interval vs ITI-min overlap, and cue pulse misconfiguration
- `CUE_SET_ONSET_DELAY (377)`, `CUE2_SET_ONSET_DELAY (387)`, `PUMP_SET_ONSET_DELAY (477)`, `PUMP2_SET_ONSET_DELAY (487)` registered in `CommandCode` and `COMMAND_REGISTRY` with `payload_key="delay"`, `payload_type="int"`, paradigm filter `["fr", "pr", "vi", "omission"]`; supports per-device onset delay UI in Labrynth

### Changed
- Validation is now a synchronous, deterministic rule engine — the Ollama/LLM backend and its `REACHER_OLLAMA_URL` / `REACHER_OLLAMA_MODEL` env vars have been removed; `httpx` is no longer a validation dependency
- `DEFAULT_BOARD` in `uploader/boards.py` changed from `"uno"` to `"mega"`; UNO board profile retained for backward-compatible session playback but is no longer the default for new sessions or unrecognized hardware

### Fixed
- In-app update download: Linux asset suffix patterns corrected to match CI-produced filenames (`_amd64.deb`, `-linux-x64.tar.gz`, `-linux-x64.AppImage`) — previous patterns (`-linux-amd64.*`) never matched any release asset, causing "No download link available for this platform" on all Linux installs
- In-app update download: `follow_redirects=True` added to the `httpx` streaming call in `_do_download` — GitHub `browser_download_url` returns a 302 to the CDN; without this the download failed immediately with `302: Failed to download asset`
- CORS `allow_methods` now includes `PUT` (hardware pin-assignment endpoint was missing this method for browser clients)

---

## [2.3.1-dev] - 2026-06-02

### Added
- SLM (Spatial Light Modulator) timestamp device support: new serial event level `009` (`{"level":"009","device":"SLM","timestamp":<ms>}`) emitted by firmware on PCINT0 rising edge; backend stores timestamps in `slm_data` list and exports `slm_timestamps.csv` in session ZIP when data is present
- `GET /api/data/{session_id}/slm` endpoint returning collected SLM timestamps and count
- PCINT0 pin-group constraint in `pin_overrides.py` (`requires_pcint: bool`); SLM pin validated against pins 8–13 on both UNO and Mega; `UNO_PCINT0` / `MEGA_PCINT0` frozensets added
- Command codes `SLM_DISARM=1100`, `SLM_ARM=1101`, `SLM_SET_PIN=1176` registered in `CommandCode` and `COMMAND_REGISTRY`
- `metadata.json` in export ZIP now includes `slm_event_count`
- `_COMMAND_STATE_MAP` entries for SLM arm/disarm/pin state tracking

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
