# The REACHER MCP server

`reacher-mcp` lets you point your own coding agent — Claude Code, Cursor, or
anything else that speaks MCP — at a REACHER workspace and ask for a change in
plain language. The agent gets ground truth about the system's registries, an
honest account of what can and cannot be verified on your machine, and checks
that catch the mirrors that fall out of sync.

## Why it exists

REACHER spans two repositories and three layers:

```
Arduino firmware  ──serial──▶  Python kernel  ──REST + WS──▶  React frontend
  (reacher/firmware)          (reacher/src)                  (labrynth/web)
```

Adding one new device touches roughly forty places, and most of them nothing
checks. The most recent new device (the SLM) took 7 commits and 19 files in the
frontend alone. Several of those places are hand-maintained copies of facts that
live authoritatively somewhere else — `pinMeta.ts` duplicates the backend's pin
constraints *and* the firmware's default pins; nine Arduino sketches each parse
the command set independently, with no central dispatcher.

The server does not try to write that code for you. It tells your agent where
the edits are, which ones a compiler will catch, and which ones nothing will.

## Install and configure

```bash
pip install "reacher2p[mcp]"
```

Then register it with your agent. For Claude Code, in `.mcp.json`:

```json
{
  "mcpServers": {
    "reacher": {
      "command": "reacher-mcp",
      "env": { "REACHER_WORKSPACE": "/path/to/dir/containing/the/checkouts" }
    }
  }
}
```

`REACHER_WORKSPACE` is optional. With the conventional layout — the `reacher`
and `labrynth` checkouts side by side — discovery finds both on its own.

For a non-default layout, drop a `.reacher-workspace.toml` at the workspace root:

```toml
[repos]
reacher  = "./backend/reacher"
labrynth = "./ui/labrynth"
```

That file carries locations only, deliberately. A file describing *which
constants mirror which* would be one more hand-maintained mirror with nothing
checking it — exactly the problem this server exists to solve.

## Tools

| Tool | What it answers |
|---|---|
| `describe_workspace` | Which repos and tools are present, and what cannot be verified here |
| `list_commands` | The command registry, plus which sketches actually handle each command |
| `get_hardware_map` | Every component's Python, firmware and frontend view side by side |
| `explain_event_flow` | How a device's name changes as it travels from firmware to the browser |
| `check_consistency` | The cross-layer rules |
| `run_checks` | Allowlisted verification commands |

Two prompts, `reacher_change` and `reacher_verify`, surface in Claude Code as
`/reacher:reacher_change` and `/reacher:reacher_verify`.

There are no file-writing tools. Your agent writes, under the permission model
you already understand — a write tool here would duplicate a capability the agent
has while bypassing the prompts you rely on.

## Reading the output

Three habits will save you.

**`UNAVAILABLE` is not a pass.** Checks report `pass`, `fail`, `unavailable` or
`error`. A summary reading *"12 passed, 0 failed, 6 UNAVAILABLE"* is not a clean
bill of health — six rules did not run. `check_consistency` reports `ok: true`
only when everything ran *and* passed.

**Read `verdict`, not `exit_code`.** A command can exit 0 having verified
nothing: pytest reports success with skipped tests, and ESLint can pass while
holding a backlog of warnings. `run_checks` reports `pass`, `pass_with_skips`,
`pass_with_warnings`, `fail` or `UNAVAILABLE` accordingly, alongside `ran` and
`trustworthy`.

**Read `derived_from` before deleting anything.** Every result names the sources
it consulted, and any result whose remedy is a deletion carries a
`before_removing` note. This exists because of a mistake made four times while
building this tooling: a contract modelled from a partial view of its producer,
where *the correct code was the thing that looked wrong*. A rule reporting "SLM
is missing from the event namespace" invites you to delete a working line; the
same rule saying it derived that from levels 007+009 invites the question that
saves you.

## What the checks cover

**Within this repo** — `Commands.h` against `CommandCode` (C1); every code has a
registry spec (C2); a `_lite` twin carries every non-two-photon command its base
does (C13); a command declaring support for a paradigm is actually handled by it
(C14); kernel device names match the namespace firmware emits (L8).

**Across the two repos** — SET_PIN codes, component keys, PWM and PCINT
constraints, firmware default pins, board pin sets, and the `BoardType` union,
all against `labrynth/web/src` (C3–C9).

C13 and C14 are the two that justify the exercise. The pre-existing parity test
proved only that `Commands.h` and `CommandCode` agreed — it said nothing about
whether any sketch *handled* a command, or whether a change reached the `_lite`
twin. With nine hand-maintained sketches, those are the omissions to expect.

## Things worth knowing about this codebase

**Device names differ by log level.** The lick circuit is `LICK` at level 000 and
`LICK_CIRCUIT` at level 007. The kernel rewrites some names before emitting, so
anything downstream must be checked against `post_kernel`, never against the raw
firmware namespaces. `explain_event_flow` spells this out. Getting it backwards
reports correct code as broken — which has happened, in both directions.

**A command declared for a paradigm may not be implemented by it.** Firmware
gaps are recorded in `reacher.schema.KNOWN_FIRMWARE_GAPS`, separately from
`INTENTIONALLY_UNHANDLED`. Derive any UI gate from that data rather than
hand-writing one, or the gate will outlive the gap.

**UNO `_lite` builds sit at 91–94 % of 32 KB.** Adding a command or a device can
overflow flash, and it only fails at `arduino-cli` compile time. If `arduino-cli`
is not installed, that risk is *unverified*, and `describe_workspace` says so.

**`firmware/compile.sh` is not exposed** through `run_checks`. It rewrites
committed hex artifacts; regenerating tracked build output stays a deliberate
human action.

## Extending it

Ground truth comes from `reacher.schema`, which is also runnable directly:

```bash
PYTHONPATH=/path/to/reacher/src python -m reacher.schema dump --json
```

`PYTHONPATH` precedence is the point. The server always reads your *working
tree*, never an installed `reacher2p` wheel, so it sees edits your agent made a
moment ago. Without that, a user with both would get answers from the wheel while
editing the checkout — stale, and silently so.

To add a rule, register it in `src/reacher/mcp/checks/`. Two obligations: declare
`requires` so it reports UNAVAILABLE rather than passing when its inputs are
missing, and give it a golden-negative in `tests/test_mcp_checks.py`. A check
that has never failed is indistinguishable from one that cannot.
