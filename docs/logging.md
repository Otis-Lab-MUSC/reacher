# Diagnostic Logging

REACHER writes one structured log per process run, covering everything from
launch to exit or crash: browser interactions, HTTP and WebSocket traffic,
session state changes, and the raw serial wire in both directions. It exists so
that a bug reported after the fact is already captured — no reproduction needed.

Logging is **always on**. There is no flag to enable it.

## Where the log lives

```
~/REACHER/LOG/runs/<YYYY-MM-DD_HH-MM-SS>_<run_id>/
├── app.ndjson      # the log itself (rotates: 32 MB × 5 segments)
├── meta.json       # versions, platform, argv, REACHER_* env (secrets removed)
└── crash.txt       # native fault tracebacks (segfaults), via faulthandler
~/REACHER/LOG/runs/latest -> most recent run
```

This sits **beside** the per-session experiment directories
(`~/REACHER/LOG/<timestamp>/`), which are unchanged — `controller_log.json`,
`event_log.jsonl` and the CSV exports remain the scientific data path.

Old runs are pruned at startup: newest 20 runs, nothing older than 30 days.

## Record format

One JSON object per line:

```json
{"ts":"2026-08-24T14:22:31.482Z","mono":1234.567,"seq":10432,"run_id":"a1b2c3d4",
 "tier":"wire","lvl":"debug","evt":"serial.tx","src":"reacher.kernel",
 "session_id":"9f2c…","corr_id":"7b1e…","msg":"…","data":{"line":"{\"cmd\":371}"}}
```

| Field | Meaning |
|---|---|
| `ts` | Wall clock, ISO-8601 UTC, millisecond precision |
| `mono` | Seconds since process start, monotonic — immune to NTP steps |
| `seq` | Process counter giving a **total order** independent of any clock |
| `run_id` | Identifies this process; distinguishes runs inside a rotated file |
| `tier` | `app`, `api`, `kernel`, `wire`, or `ui` |
| `lvl` | `debug`, `info`, `warn`, `error`, `fatal` |
| `evt` | Machine-readable event name (`ui.click`, `serial.rx`, `session.state`, …) |
| `corr_id` | Correlation ID — see below |

Sort by `seq`, not `ts`. UI records are stamped with *browser* time and arrive
batched and late; `seq` is assigned by the backend as records are emitted, so it
reflects the real order. A browser clock more than 2 s off is flagged with
`data.client_clock_skew_s`.

## Correlation: tracing a click to the wire

A UI interaction mints a `corr_id`, sends it as the `X-Reacher-Corr-Id` header,
and the backend binds it for the life of that request. Everything logged while
handling it inherits the ID — including the bytes written to the Arduino.

```bash
# What did pressing that button actually do?
jq -c 'select(.corr_id=="7b1e…")' ~/REACHER/LOG/runs/latest/app.ndjson
```

```
ui      ui.click        Set cue frequency   {"field":"frequency","value":"8000"}
api     http.request    POST /api/hardware/…/command → 200 (53.7ms)
kernel  session.state   idle → connected
wire    serial.tx       {"cmd": 371, "frequency": 8000}
```

**Limit:** serial *receive* runs on a long-lived daemon thread with no request
context, and the firmware cannot echo an ID back, so `serial.rx` records carry
`session_id` and timestamps but no `corr_id`. Correlate RX to TX by session and
time proximity.

## Useful queries

```bash
L=~/REACHER/LOG/runs/latest/app.ndjson

jq -c 'select(.lvl=="error" or .lvl=="fatal")' "$L"        # what went wrong
jq -c 'select(.tier=="wire") | .data.line' "$L"            # the serial conversation
jq -c 'select(.evt=="session.state") | .msg' "$L"          # lifecycle
jq -c 'select(.evt|startswith("ui.")) | [.evt,.msg]' "$L"  # what the user did
jq -c 'select(.data.duration_ms>500)' "$L"                 # slow requests
jq -s 'group_by(.evt)|map({evt:.[0].evt,n:length})|sort_by(-.n)' "$L"
```

## What is and isn't recorded

Field values are recorded **verbatim** — subject IDs, doses, durations, file
paths — because those are what make a bug reproducible. Consequently **the log
is as sensitive as the experiment data**; treat it accordingly when sharing.

Redacted everywhere (client, server, and env snapshot): any key matching
`api_key`, `secret`, `password`, `token`, `bearer`, `authorization`,
`credential`, `pairing_code`, `private_key`. Password inputs are never read at
all. Redaction is re-applied server-side, so the browser is never trusted.

Not recorded: keystrokes (only Enter/Escape/modifier chords; `change` already
carries the committed value), and query-string values (recorded by length only,
since they may carry `?token=`).

## Collecting a bug report

- **UI:** About → *Download diagnostics* → a ZIP of the current run.
- **HTTP:** `GET /api/logs/export` (add `?run=<name>`; `GET /api/logs/runs` lists them).
- **Remote host:** the same routes through the proxy —
  `GET /api/proxy/{device_id}/api/logs/export`. Each machine keeps its own log;
  the primary pulls on demand.
- **By hand:** `~/REACHER/LOG/runs/latest/`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `REACHER_LOG_DIR` | `~/REACHER/LOG/runs` | Where run logs are written |
| `REACHER_LOG_LEVEL` | `DEBUG` | Floor for the file; `INFO` drops wire records |
| `REACHER_LOG_VERBOSE_DEPS` | unset | Keep third-party DEBUG chatter (httpx, zeroconf, …) |

Chatty third-party loggers are capped by default; the subnet-scan fallback alone
probes ~500 hosts per cycle and would otherwise bury the signal.

## Design guarantees

- **Never blocks a producer.** Wire logging sits in the serial read loop; the
  queue is bounded and non-blocking, dropping and counting under pressure rather
  than stalling acquisition. Drops are visible on `/health`.
- **Never crashes the app.** An unwritable or full disk degrades to counted
  drops with a single stderr notice, and re-probes every 30 s.
- **Never logs through itself.** Sink failures bypass the logging system
  entirely, so a disk problem cannot recurse.
- **Survives a hard kill.** Records are flushed to the OS as soon as the writer
  catches up, so `SIGKILL` loses nothing already emitted; `fsync` is batched.
- **Volume is bounded** by both rotation and the 115200-baud serial ceiling
  (~11 KB/s), so even a saturated wire log fills a segment in about an hour.
