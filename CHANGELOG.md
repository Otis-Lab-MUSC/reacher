# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [3.2.0] - 2026-07-24

### Added
- Firmware: `firmware/fr_lite/` — a new Fixed Ratio sketch targeting Arduino UNO
  (ATmega328P, 32KB flash / 2KB RAM). Drops Microscope + SLM two-photon sync (neither
  fits the UNO's budget); every other shareable device (levers, cue/cue2, pump/pump2,
  lick circuit, laser) and command code is unchanged from `fr.ino`. `ds.microscope` is
  now null-guarded in `ReacherHelpers.cpp` (mirroring the existing `ds.laser` pattern)
  so the lite sketch can pass `nullptr` safely. `compile.sh` builds `fr_lite` for `uno`
  → `src/reacher/hex/uno/fr_lite.hex`, alongside the existing legacy `uno/*.hex`
  artifacts; `uploader.py`'s `PARADIGMS` gains `fr_lite` as a `board=uno` upload target.
  Compiles at 94% flash / 62% RAM on `arduino:avr:uno`
  ([#51](https://github.com/Otis-Lab-MUSC/reacher/issues/51))

### Fixed
- Firmware: `fr/Config.h`'s `DEFAULT_CUE_FREQUENCY`/`DURATION`, `DEFAULT_PUMP_DURATION`,
  `DEFAULT_LASER_FREQUENCY`/`DURATION`, and `DEFAULT_TIMEOUT_INTERVAL` still held their
  old non-zero values from before the per-device onset-delay refactor, so a
  freshly-flashed board fired real cue/pump/laser output the moment a device was armed,
  before any experimenter configuration. All six are now zero, so the board ships inert
  until explicitly configured. `firmware/README.md`'s FR section is also rewritten — it
  still described the old cue-offset-relative "trace interval" chaining model, no longer
  present in code, instead of the current per-device onset-delay model
  ([#49](https://github.com/Otis-Lab-MUSC/reacher/issues/49))
- Firmware: `Laser::UpdateHalfCycle()` divided by `frequency` unguarded; zeroing
  `DEFAULT_LASER_FREQUENCY` (above) meant a fresh board's laser hit a `1.0f / 0`
  division whose result was then cast to `uint32_t` — undefined behavior — the moment
  `LASER_TEST` fired or a reward chain activated an unconfigured laser. Now guarded:
  frequency `0` keeps the laser off instead of computing a half-cycle
- Firmware: `MICROSCOPE_ARM`/`DISARM`/`TEST`/`SET_TRIG_PIN` silently reported success
  on boards without a microscope (e.g. `fr_lite` on UNO) instead of erroring. Now
  falls through to the sketch's "Command not found" response, matching how `SLM_*`
  commands (absent from the shared handler) already behave

---

## [3.1.1] - 2026-07-22

_SLM SYNC never captured a timestamp on the Mega 2560: the pin-change interrupt was armed on the wrong port bit._

### Fixed
- Firmware: `Slm` derived its PCINT registers with hand-rolled `timestampPin - 8`
  arithmetic, which is only valid on the ATmega328 (UNO), where PORTB is PB0–PB5 ==
  pins 8–13. On the Mega 2560 PORTB is PB4–PB7 == pins 10–13, so the default pin 11
  armed `PCMSK0` bit 3 — physical pin 50 — and the ISR polled that same wrong bit.
  No edge on the actual SLM input was ever observed, so `HandleTimestampSignal()`
  never emitted a level-`009` event and the SLM timestamp stream was silently empty.
  The register/bit/mask are now derived from the Arduino core macros
  (`digitalPinToPort` / `portInputRegister` / `digitalPinToBitMask` /
  `digitalPinToPCMSKbit`), mirroring how `Microscope` uses `digitalPinToInterrupt`
  to stay board-portable ([#47](https://github.com/Otis-Lab-MUSC/reacher/issues/47))
- Firmware: `Slm::SetPin` now rejects any pin outside the PCINT0 group with a
  level-`006` `pin_not_pcint0` error and keeps the current pin. Previously each
  sketch pre-clamped the request with `constrain(pin, 10, 13)`, which made the guard
  unreachable and silently mapped an out-of-group request onto a different valid pin
  — a request for pin 8 became pin 10 (the right-hand lever) and was reported back as
  success ([#47](https://github.com/Otis-Lab-MUSC/reacher/issues/47))
- Firmware: `Slm`'s ISR-shared `pinInputReg` and `pinBitMask` are now correctly
  `volatile`-qualified. They are written from `SetPin()` in main context and read
  inside `PCINT0_vect`, and this core builds with `-flto`, so the missing qualifiers
  were a live miscompilation risk. `ISR(PCINT0_vect)` also null-guards the singleton,
  matching `Microscope::TimestampISR`
- Backend: `MEGA_PCINT0` narrowed from `8–13` to `{10, 11, 12, 13}` — the Mega's
  non-SPI PORTB pins. Pins 8/9 are PORTH on that part and cannot raise `PCINT0_vect`
  at all, so the old range advertised two pins the firmware could never capture on
  ([#47](https://github.com/Otis-Lab-MUSC/reacher/issues/47))
- Backend: `board_sets()` now falls back to `MEGA_PCINT0` rather than `UNO_PCINT0`
  when the board is unknown. It remains UNO-first for the digital/PWM/interrupt sets
  (those are the narrower, safer default), but the PCINT0 relation is inverted —
  `UNO_PCINT0` is the *wider* set, and board detection legitimately returns `None`
  for clone/unrecognized USB IDs on real Mega hardware. `MEGA_PCINT0` is a subset of
  `UNO_PCINT0`, so it is the only choice valid on both boards
- Docs: the `SLM_SET_PIN` comment in `Commands.h`, the mirrored comment in
  `kernel/commands.py`, and the user-facing `CommandSpec` description all said
  "Arduino pins 8–13". The description in particular told operators 8/9 were legal
  when `validate_pin` now rejects them on a Mega. All three now state the board split:
  PCINT0/PORTB group — 10–13 on the Mega, 8–13 on the UNO

---

## [3.1.0] - 2026-07-17

### Fixed
- Firmware: PUMP and LASER onset timing in fr/pr/vi/omission was hardcoded relative to
  when CUE finished, not the lever press itself; each output device (CUE, PUMP, LASER)
  now fires at press onset + its own independent per-device delay only, with no
  dependency on another device's onset or duration. Fixes a pre-existing inconsistency
  where `vi.ino` only applied the laser onset delay in RH-only mode and `omission.ino`
  never applied it at all. Pavlovian's own scheduler/timing model is untouched
  ([#45](https://github.com/Otis-Lab-MUSC/reacher/issues/45))
- `_VALUE_RANGES["delay"]` validation bound was `(0, 60000)`, ten times too low versus
  firmware's actual `(0, 600000)` clamp and all three devices' frontend maximums
- Command 673 (`LASER_SET_ONSET_DELAY`) incorrectly excluded `pavlovian` from its
  paradigm list even though Pavlovian already sends it
- `omission.ino`/`vi.ino` still clamped incoming onset-delay values to 60000ms while
  the backend now accepts up to 600000ms (matching fr/pr/pavlovian's firmware clamp) —
  values above 60000ms would silently truncate on those two paradigms with no error
  reported back, caught during the v3.1.0 pre-release audit
- Removed an orphaned session-config validation rule that still checked a
  `paradigmSettings.traceInterval` field against the session time limit; the field
  (and the reward-chain semantics it validated) no longer exist after the
  `TRACE_INTERVAL` removal above, so the rule was dead code

### Removed
- `TRACE_INTERVAL` global and the `SET_TRACE_INTERVAL` command (220) from fr/pr/vi —
  superseded by per-device onset delays; any resulting cue-to-reward gap is now
  inferred and labeled in the Labrynth timeline rather than stored as a separate
  parameter (operant paradigms only; Pavlovian's own `PAV_TRACE_INTERVAL` is untouched)

### Added
- SLM SYNC: `SLM_SET_LASER_FREQUENCY` (1102) and `SLM_SET_LASER_DURATION` (1103)
  commands and inert storage fields on the `Slm` class, wired into all five sketches,
  so a session record captures what the laser's frequency/duration should have been —
  for post-hoc reconciliation, not tied to real laser control
  ([#45](https://github.com/Otis-Lab-MUSC/reacher/issues/45))

---

## [3.0.2] - 2026-06-26

### Fixed
- Firmware: lever source filter shadows (`CUE_SOURCE_FILTER`, `CUE2_SOURCE_FILTER`,
  `PUMP_SOURCE_FILTER`, `PUMP2_SOURCE_FILTER`) were unconditionally reset to `NONE`
  in `StartSession()`, silently discarding any per-device filter configuration sent
  via cmds 378/388/478/488 before session start and making the Lever Filter UI
  controls non-functional end-to-end across FR, PR, VI, and Omission paradigms;
  filters now survive `StartSession()` and are correctly baked into the reward chain
  by the subsequent `ReconfigureChain()` call ([#43](https://github.com/Otis-Lab-MUSC/reacher/issues/43))

---

## [3.0.1] - 2026-06-25

### Fixed
- `REACHER_HOST` now defaults to `127.0.0.1` (loopback); set to `0.0.0.0` to accept LAN connections. Prevents unintended exposure of unauthenticated endpoints on multi-host networks ([#37](https://github.com/Otis-Lab-MUSC/reacher/issues/37))
- `GET /api/firmware/diagnostics` leaked filesystem paths and a directory listing to unauthenticated callers; endpoint now requires the Bearer token ([#38](https://github.com/Otis-Lab-MUSC/reacher/issues/38))
- Lifecycle shutdown beacon now refuses to terminate the process while a session is in the `uploading` state, closing a mid-acquisition termination window alongside the existing `running`/`paused` guards ([#39](https://github.com/Otis-Lab-MUSC/reacher/issues/39))
- Microscope frame-drop counter (`FW-003`) was silently reset at every `ReconfigureChain()` call and never surfaced to the backend; counter now persists across reconfigurations and is emitted in status frames ([#40](https://github.com/Otis-Lab-MUSC/reacher/issues/40))
- Kernel log-write failures (disk full, permission denied) were swallowed silently; failures now surface as throttled WebSocket warnings so the operator is notified in real time ([#41](https://github.com/Otis-Lab-MUSC/reacher/issues/41))
- Lever source filter shadows were not reset at session start, causing stale filter state from the previous session to bleed into the next; CUE2 filter chain step added to FR/PR/VI/Omission schedules ([#42](https://github.com/Otis-Lab-MUSC/reacher/issues/42))

---

## [3.0.0] - 2026-06-24

_reacher v3.0.0 stable — first stable and first PyPI release of the v3 line. See each
beta section below for the full incremental change history._

### Added
- **Firmware source folded into this repo** at `firmware/` (sketches, `libraries/REACHERDevices/`, `compile.sh`) — imported from the archived `Otis-Lab-MUSC/reacher-firmware`; hex committed as package data at `src/reacher/hex/<board>/`
- Session config validation endpoint (`POST /api/validate/config`): 43-rule deterministic engine covering paradigm required fields, hardware device checks, session limit conflicts, temporal ordering, and Pavlovian-specific rules; structured warnings with severity levels surfaced in Labrynth's Start Modal
- Per-device onset-delay commands: `CUE_SET_ONSET_DELAY` (377/387), `PUMP_SET_ONSET_DELAY` (477/487)
- Per-device lever-routing commands: `CUE_SET_LEVER_FILTER` (378/388), `PUMP_SET_LEVER_FILTER` (478/488), `LASER_TRIGGER_LH_ONLY` (685)
- `GET /api/serial/ports` now includes a `portBoards` map (VID/PID auto-detect) so Labrynth can pre-fill the firmware upload board selector
- Temporal validation rules 37–40: warn when lever timeout is shorter than a contingent cue's onset delay + duration
- Multi-machine validation test suite for remote/proxy session control
- `pavlovian.ino` CS+/CS− pulse handlers (374/375/384/385)

### Changed
- Config validator is now a pure-Python rule engine — Ollama/LLM backend and `REACHER_OLLAMA_URL`/`REACHER_OLLAMA_MODEL` env vars removed
- Firmware version coupled to package version: `bump-version.py` stamps `library.properties` and each sketch's `SendIdentification()`; recompile hex after every bump
- FR/PR laser routing uses configurable `LASER_LEVER_FILTER` (RH or LH) instead of hardcoded RH; onset-delay clamp raised to 600 000 ms
- Pavlovian `counterbalance` (212), `consumption_window` (215), and `pulse_config` (219) params re-enabled in the command registry
- Default upload board changed from UNO to Mega 2560
- Rebranded umbrella project from "REACHER Suite" to "Phoxel Workbench" across documentation; package name, APIs, and serial protocol unchanged

### Fixed
- Infusion-limit counter increments for operant paradigms (`PUMP_1`) alongside legacy `PUMP` ([#3.0.0-beta.3](https://github.com/Otis-Lab-MUSC/reacher/issues/))
- Proxy WebSocket endpoint replays `session_state` on connect — late-connecting relays no longer stay stuck at `idle`
- Armed cue/secondaryCue with `frequency: 0` is now a pre-flight hard error (blocked before start)
- In-app update download: Linux asset suffix patterns and `follow_redirects=True` fixed

---

## [3.0.0-beta.7] - 2026-06-18

### Added
- `pavlovian.ino` now implements the CS+/CS- cue pulse command handlers (`CUE_SET_PULSE_ON`/`OFF` and `CUE2_SET_PULSE_ON`/`OFF` — codes 374/375/384/385) that the Pavlovian UI emits, reusing `Cue::SetPulsed` with per-cue/per-direction shadow globals. These codes were declared in the `CommandCode` enum + labrynth metadata but parsed by no sketch, so preset-driven Pavlovian sessions returned a `006` "command not found". `KNOWN_BACKEND_ONLY` is now empty, so `test_command_parity` enforces firmware coverage of every command code ([#24](https://github.com/Otis-Lab-MUSC/reacher/issues/24))

### Fixed
- An armed primary/secondary cue with `frequency: 0` is now a hard pre-flight validation **error** instead of an acknowledgeable warning, mirroring the existing armed-laser-frequency-0 rule. The config is blocked before session start rather than surfacing a raw HTTP 400 ("frequency must be between 1 and 65535") when the frequency command is emitted; the `hardware.py` per-command range check is left unchanged (defense-in-depth) ([#25](https://github.com/Otis-Lab-MUSC/reacher/issues/25))

---

## [3.0.0-beta.6] - 2026-06-18

### Added
- `LASER_TRIGGER_LH_ONLY` (685) — fire the isolated laser on LH-lever presses; mirrored into the Python `CommandCode` enum + registry ([#67](https://github.com/Otis-Lab-MUSC/labrynth/issues/67))

### Changed
- FR/PR laser routing now uses a configurable `LASER_LEVER_FILTER` (RH or LH) instead of a hardcoded RH source; `LASER_SET_ONSET_DELAY` (673) reconfigures the chain unconditionally so the onset applies in contingent mode too; onset-delay clamp raised to 600000 ms to match cue/pump ([#67](https://github.com/Otis-Lab-MUSC/labrynth/issues/67))
- Pavlovian scheduler honors the laser onset delay at the REWARD and CUE activation sites, clamped to the active phase window so onset can't bleed into a later phase/trial ([#69](https://github.com/Otis-Lab-MUSC/labrynth/issues/69))

### Fixed
- `pavlovian.ino` now handles `LASER_SET_ONSET_DELAY` (673) instead of returning a `006` "command not found" ([#69](https://github.com/Otis-Lab-MUSC/labrynth/issues/69))

---

## [3.0.0-beta.5] - 2026-06-17

### Fixed
- Re-enabled the Pavlovian `counterbalance` (212), `consumption_window` (215), and `pulse_config` (219) command params in the registry — they had been incorrectly marked deprecated, which hid them from Labrynth's Pavlovian panel. They are now live and carry their documented payload contract ([#21](https://github.com/Otis-Lab-MUSC/reacher/issues/21))

### Changed
- Rebranded the umbrella project from "REACHER Suite" to "Phoxel Workbench" across documentation. The package name, import name, APIs, and serial protocol are unchanged ([#19](https://github.com/Otis-Lab-MUSC/reacher/issues/19))

### Added
- Multi-machine validation test suite exercising remote/proxy session control across paired hosts ([#15](https://github.com/Otis-Lab-MUSC/reacher/issues/15), [labrynth#16](https://github.com/Otis-Lab-MUSC/labrynth/issues/16))

---

## [3.0.0-beta.4] - 2026-06-16

### Fixed
- Proxy/IoT-mode remote sessions no longer receive zero behavioral events. The WebSocket endpoint now replays the current `session_state` to each client immediately on connect, so a late-connecting proxy relay (or any reconnect) learns a running session instead of staying stuck at the browser's default `idle` — previously the Pi broadcast its `idle → connected → running` transitions to an empty client set before the relay connected, and the frontend silently dropped every event ([labrynth#15](https://github.com/Otis-Lab-MUSC/labrynth/issues/15))
- WebSocket relay upstream connection now disables the library-level ping (`ping_interval=None`), relying on uvicorn's server-side keepalive to avoid a second ping layer racing it into a silent teardown under load

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
