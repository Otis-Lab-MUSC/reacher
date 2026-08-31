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
| `REACHER_HOST` | `127.0.0.1` | Bind address. Defaults to loopback. Set to `0.0.0.0` to accept LAN connections (exposes unauthenticated endpoints and makes the WS token network-visible). |
| `REACHER_STATIC_DIR` | `web/dist/` | React frontend directory |
| `REACHER_HEX_DIR` | package data (`src/reacher/hex/`) | Override dir for pre-compiled firmware hex files |
| `REACHER_CORS_ORIGINS` | None | Extra allowed CORS origins (comma-separated) |
| `REACHER_API_KEY` | auto-generated | Bearer token; auto-written to `~/.reacher/api_key` if unset |
| `REACHER_AVRDUDE_PATH` | system PATH | Path to `avrdude` binary (set during PyInstaller packaging) |
| `REACHER_LOG_DIR` | `~/REACHER/LOG/runs` | Diagnostic run-log directory |
| `REACHER_LOG_LEVEL` | `DEBUG` | Floor for the diagnostic log (`INFO` drops serial-wire records) |
| `REACHER_LOG_VERBOSE_DEPS` | unset | Keep third-party DEBUG chatter (httpx, zeroconf, …) out of the log |
| `REACHER_GITHUB_OWNER` | `Otis-Lab-MUSC` | GitHub org/user that owns the target issue repos. Used to build the pre-filled "New Issue" link. |

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

### Diagnostic Logging (`src/reacher/diagnostics/`)
Always-on structured NDJSON logging covering the whole stack — browser UI, HTTP,
WebSocket, session lifecycle, and raw serial in both directions — in one file per
process run at `~/REACHER/LOG/runs/<ts>_<run_id>/app.ndjson`. See `docs/logging.md`.

`configure_logging()` (called from `app.py:main()` and the lifespan) installs a
root `SinkHandler`, so **every existing `logger.*` call becomes durable without
touching call sites** — previously they went to a handler-less root logger and
were discarded entirely in frozen builds. It also installs crash capture:
`sys.excepthook`, `threading.excepthook`, `faulthandler` (native segfaults),
`atexit`, and chained SIGINT/SIGTERM handlers.

A `corr_id` minted in the browser rides the `X-Reacher-Corr-Id` header into a
`contextvar`, so a click can be traced through to the serial bytes it caused.
Correlation reaches FastAPI sync endpoints (anyio copies context) but **not** the
kernel's daemon threads — `serial.rx` carries `session_id` only.

The sink never blocks producers (bounded queue, drops and counts under pressure),
never raises (an unwritable disk degrades to counted drops), and never logs
through itself. `/api/logs/ingest` receives browser records; `/api/logs/export`
returns a run as a ZIP and relays through the proxy for paired hosts.

Log volume note: chatty third-party loggers are capped in `QUIET_LOGGERS` —
without that the subnet-scan fallback alone (~500 hosts/cycle via httpcore)
buries the signal.

### Registry Export (`src/reacher/schema.py`)
`python -m reacher.schema dump --json` emits every registry as one document:
commands, pin constraints, boards, paradigms, **plus** parsed `Commands.h`,
`Pins.h`, per-sketch `Cmd::` references, and the firmware device-name
namespaces. It is the **only** place those firmware files are parsed —
`test_command_parity.py` and the MCP checks both go through it, so a reformat
breaks one parser rather than three. Pure read + serialize: no network, no
writes. Reports `firmware.present: false` (never raises) on a wheel-only tree.
Every parser has a sanity floor that raises rather than returning an empty
result a comparison would read as "no drift".

It also owns two registries other layers derive from: `KNOWN_FIRMWARE_GAPS`
(commands the UI offers that firmware silently drops — **derive UI gates from
this, never hand-write them**) and `INTENTIONALLY_UNHANDLED` (by-design cases).
Tests assert the two stay disjoint and that neither goes stale.

**Device names are per-log-level, not global.** Firmware spells the lick circuit
`LICK` at level 000 and `LICK_CIRCUIT` at level 007; the operant scheduler emits
`CUE_1`/`PUMP_1` where the Pavlovian one emits `CUE`/`PUMP`. The kernel rewrites
some names before emitting, so **anything downstream of the kernel must be
checked against `device_names.post_kernel`**, never the raw firmware sets.
`POST_KERNEL_EVENT_REWRITES` and `EVENT_STREAM_CONTRIBUTORS` model that; the
latter is keyed by log level and covers level 009, which synthesizes an `SLM`
event that appears at no 007 print site.

### MCP Server (`src/reacher/mcp/`)
Cross-repo change tooling for a user's own coding agent — `pip install
"reacher2p[mcp]"`, then the `reacher-mcp` console script over stdio. Six
read-only tools plus `run_checks`; **no file-writing tools** (the agent writes,
under its own permission model). Never imported by `api/app.py`, so it stays out
of the frozen bundle. See `docs/mcp-server.md`.

Three invariants, each guarding a way to report success while verifying nothing:
- Ground truth is fetched by running `reacher.schema` as a **subprocess with
  `PYTHONPATH` at the target checkout's `src/`**, per call, never cached and
  never imported. Otherwise a user with both an installed wheel and a checkout
  reads the wheel while editing the checkout.
- Checks report `pass | fail | unavailable | error`, and `unavailable` is never
  a pass. `run_checks` reports `ran`/`trustworthy`/`verdict` separately from
  `exit_code` (`pass_with_skips`, `pass_with_warnings`).
- Every result declares `derived_from`; results whose remedy is a deletion carry
  a `before_removing` warning. Four times during development a contract was
  modelled from a partial view of its producer and *the correct code looked
  wrong* — provenance is the guardrail against acting on that.

`run_checks` uses a literal argv allowlist (`shell=False`, pinned cwd,
`clean_child_env()`). `firmware/compile.sh` is deliberately excluded: it
rewrites committed hex.

### Firmware Uploader (`src/reacher/uploader/`)
Wraps `avrdude` to flash Arduino firmware. Handles PyInstaller frozen mode path resolution (`_MEIPASS/hex/`) and streams upload progress via callback. `boards.py` is the board-profile registry — each entry maps a `board_id` to a display name, an Arduino CLI FQBN, and the `avrdude` argument tuple. Adding a new board is a single entry in `BOARD_PROFILES`. Hex resolution prefers package data (`src/reacher/hex/`) as canonical; the GitHub fallback fetches from this repo (`Otis-Lab-MUSC/reacher`, `src/reacher/hex/`) for bare `pip install` hosts.

### Firmware Source (`firmware/`)
Arduino firmware source, folded in from the archived `Otis-Lab-MUSC/reacher-firmware`. Five sketches (`fr/ pr/ vi/ omission/ pavlovian/`) share `libraries/REACHERDevices/`, and four ship a UNO-compatible `_lite` twin (`fr_lite/ pr_lite/ vi_lite/ omission_lite/`) with two-photon (Microscope + SLM) support stripped; Pavlovian has none because it overflows UNO flash even stripped. `firmware/libraries/REACHERDevices/src/Commands.h` is the firmware-side command list mirrored by `kernel/commands.py`; **edit both together** when adding a command — `tests/test_command_parity.py` enforces parity. `firmware/compile.sh` writes hex into the committed package-data tree `src/reacher/hex/<board>/` (run `arduino-cli core install arduino:avr` once, then `bash firmware/compile.sh`; commit the refreshed hex). Firmware version strings are stamped by `scripts/bump-version.py` — never hand-edit, and recompile hex after a bump. Target board is Mega 2560; the `uno/` hex set is the four `_lite` builds (the full-paradigm uno hex files are stale legacy artifacts that no longer compile). The microscope timestamp pin (INT0) is fixed in firmware and must not be exposed as remappable. See `firmware/CLAUDE.md` and `firmware/README.md` for paradigm/hardware detail.

### Issue Reporting (`src/reacher/issues/`)
`POST /api/issues/prefill` composes a title/body from the user's report plus a
capped, redacted diagnostic excerpt (`diagnostics/excerpt.py`), and returns a
pre-filled `github.com/{owner}/{repo}/issues/new?...` link — the user reviews
and submits it themselves, in their own browser, under their own GitHub
account. No token, no relay, no LLM, and no network or subprocess call happens
on this path; `prefill.py` is pure string composition. The binding constraint
is the practical URL length a browser/GitHub will accept
(`prefill.URL_BUDGET`, conservatively 6,000 chars), not GitHub's 65,536-char
issue-body cap — the diagnostic excerpt shrinks first to make room, and the
body is bluntly truncated as a last resort so this endpoint can never hand
back a link that gets rejected outright.

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
- `tests/test_logging.py` — diagnostic sink, rotation, redaction, crash hooks, ingest/export, end-to-end corr_id trace
- `tests/test_issues.py` — log excerpt builder and `POST /api/issues/prefill` (title/body composition, excerpt-shrink-to-fit-URL-budget, label filtering, repo validation)
- `tests/test_schema.py` — the registry export, with golden-negatives proving each parser's sanity floor fires
- `tests/test_firmware_parity.py` — C13 (lite twins carry every non-2P command; `Config.h` byte-identical) and C14 (declared paradigm support has a handler)
- `tests/test_device_names.py` — L8: kernel and simulator device names against the firmware namespaces, and that every `code_dict` level is classified
- `tests/test_frontend_parity.py` — C3–C9 against the labrynth checkout; **skips loudly** when absent
- `tests/test_mcp_*.py` — workspace discovery, the checkout-beats-wheel guarantee, the check engine's golden-negatives, and the tool surface
- `tests/conftest.py` — autouse fixture pointing `REACHER_LOG_DIR` at `tmp_path`; **required**, or tests write to the real `~/REACHER/`

**Golden-negatives are mandatory for a new check.** Every consistency rule ships
with a test driving deliberately drifted input, because a check that has never
failed is indistinguishable from one that cannot.

## Docs & Scripts

- `docs/setup-guide.md` — end-user setup walkthrough (host install, pairing, systemd).
- `docs/logging.md` — diagnostic log: record schema, correlation, `jq` recipes, redaction policy, retrieval.
- `docs/mcp-server.md` — the `reacher-mcp` cross-repo change server: setup, tools, how to read `UNAVAILABLE`/`verdict`/`derived_from`.
- `scripts/install.sh` — host-side installer.
- `scripts/bump-version.py` — single source of truth for the package version; updates `pyproject.toml`, `src/reacher/__init__.py`, the firmware version strings (`firmware/libraries/REACHERDevices/library.properties` + each sketch's `SendIdentification()`), and the `README.md` version badge + wheel-install example in one shot. Derived spellings are handled automatically — the badge is shields.io-escaped (`3.0.0-alpha.1` → `3.0.0--alpha.1`) and the wheel name is PEP 440 normalized (`3.0.0-alpha.1` → `3.0.0a1`); `--check` (which CI runs against the bare tag) validates all forms. After bumping, recompile firmware hex (`bash firmware/compile.sh`) so the shipped binaries report the new version.

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
