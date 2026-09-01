"""MCP tools for making coordinated cross-repository changes to REACHER.

The server supplies **ground truth, ordered checklists and verification**. It
does not write files: the user's own agent does that, under the permission model
the user already understands. A write tool here would duplicate a capability the
agent has while bypassing the prompts the user relies on.

Every tool returns a JSON-serializable dict with an ``ok`` field and never
raises, so a long-lived stdio server survives a mid-edit syntax error in the
tree it is reading.

Two properties are load-bearing throughout:

* Ground truth is re-read from the user's working tree on every call. Nothing is
  cached and nothing is imported from an installed wheel.
* "Could not check" is never reported as "checked and fine".
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from .. import __version__
from . import checks, runner
from .schema_client import SchemaUnavailable, fetch
from .workspace import discover

INSTRUCTIONS = """\
Tools for changing the REACHER experiment-control platform across its three
layers: Arduino firmware, the Python kernel, and the React frontend (labrynth).

Start with `describe_workspace`, then `plan_change` with the user's own words.

Three things about this codebase will otherwise cost you:

1. Most edit sites have NO automated guard. `plan_change` returns a
   `guard_summary`; when it says most steps are `silent`, the compiler and the
   tests will not catch an omission and you must verify each one by hand.
2. Some requests need an ANSWER, not a change. `outcome: "no_code_change"`
   means the thing the user wants is already a runtime setting.
3. A check that reports `UNAVAILABLE` did not run. It is not a pass. Never
   report work as verified on the strength of an unavailable check, and never
   trust a zero exit code over the `verdict` field.

Resolve every `required: true` entry in `open_questions` WITH THE USER before
making the first edit.
"""

mcp = MCPServer(
    name="reacher",
    version=__version__,
    instructions=INSTRUCTIONS,
)


def tool_result(fn):
    """Return structured errors instead of raising out of a tool call."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except SchemaUnavailable as exc:
            return {
                "ok": False,
                "error": "ground_truth_unavailable",
                "message": str(exc),
                "guidance": (
                    "This is UNAVAILABLE, not a pass. Do not report anything as verified. "
                    "Point the server at a reacher checkout with $REACHER_WORKSPACE."
                ),
            }
        except Exception as exc:  # noqa: BLE001 - a tool must never kill the server
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}

    return wrapper


def _workspace(workspace: Optional[str]):
    return discover(Path(workspace) if workspace else None)


@mcp.tool()
@tool_result
def describe_workspace(workspace: Optional[str] = None) -> dict[str, Any]:
    """Report the repos, versions and tooling available for a coordinated change.

    Read this first. The `warnings` and `tooling` blocks state what CANNOT be
    verified on this machine — including that labrynth's `npm run lint` exits 0
    while doing nothing, and which test files skip silently when a tree is
    absent. Both are ways to bank a false green.
    """
    ws = _workspace(workspace)
    out = ws.as_dict()
    # ok is False without a reacher checkout even though the description itself
    # succeeded. Without ground truth nothing here can be verified, and an agent
    # skimming for `ok` must not read that as "fine".
    out["ok"] = ws.reacher.present
    if not ws.reacher.present:
        out["error"] = "no_reacher_checkout"
        out["message"] = (
            "No reacher checkout found, so no check can run. This is UNAVAILABLE, "
            "not a clean result."
        )
    out["commands"] = runner.available(ws)
    if ws.reacher.present:
        try:
            doc = fetch(ws.reacher.path)
            fw = doc["firmware"]
            out["ground_truth"] = {
                "schema_version": doc["schema_version"],
                "version": doc["generated_from"]["version"],
                "commands": len(doc["python"]["commands"]),
                "components": len(doc["python"]["component_keys"]),
                "boards": [b["board_id"] for b in doc["python"]["board_profiles"]],
                "paradigms": doc["python"]["paradigms"]["all"],
                "firmware_present": fw.get("present", False),
                "sketches": [s["name"] for s in fw.get("sketches", [])],
            }
        except SchemaUnavailable as exc:
            out["ground_truth"] = {"available": False, "reason": str(exc)}
            out["warnings"].append(f"ground truth UNAVAILABLE: {exc}")
    return out


@mcp.tool()
@tool_result
def list_commands(
    paradigm: Optional[str] = None,
    name_contains: Optional[str] = None,
    code_min: Optional[int] = None,
    code_max: Optional[int] = None,
    include_deprecated: bool = False,
    workspace: Optional[str] = None,
) -> dict[str, Any]:
    """List serial commands, with which firmware sketches actually handle each.

    `handled_by` is the part worth reading: a command can be declared for a
    paradigm in COMMAND_REGISTRY while no sketch implements it, in which case the
    UI offers it and firmware silently drops it. `unhandled_paradigms` names any
    such case, and `free_codes` suggests unused codes in each range for a new
    command.
    """
    ws = _workspace(workspace)
    if not ws.reacher.present:
        raise SchemaUnavailable("no reacher checkout found")
    doc = fetch(ws.reacher.path)
    fw = doc["firmware"]
    sketches = {s["name"]: set(s["cmd_refs"]) for s in fw.get("sketches", [])}
    common = set(fw.get("common_dispatcher_cmd_refs", []))
    exempt = {**doc["python"].get("intentionally_unhandled", {}),
              **doc["python"].get("known_firmware_gaps", {})}

    rows = []
    for spec in doc["python"]["commands"]:
        if not include_deprecated and spec["deprecated"]:
            continue
        if paradigm and paradigm not in spec["paradigms"]:
            continue
        if name_contains and name_contains.upper() not in spec["name"]:
            continue
        if code_min is not None and spec["code"] < code_min:
            continue
        if code_max is not None and spec["code"] > code_max:
            continue

        handled = sorted(n for n, refs in sketches.items() if spec["name"] in refs)
        entry = exempt.get(spec["name"])
        unhandled = [
            p for p in spec["paradigms"]
            if p in sketches and p not in handled and spec["name"] not in common
            and not (entry and (entry["paradigms"] is None or p in entry["paradigms"]))
        ]
        rows.append({
            **spec,
            "in_firmware_header": spec["name"] in fw.get("commands_h", {}),
            "handled_by": handled,
            "handled_by_common_dispatcher": spec["name"] in common,
            "unhandled_paradigms": unhandled,
            "known_gap": doc["python"].get("known_firmware_gaps", {}).get(spec["name"]),
        })

    return {
        "ok": True,
        "count": len(rows),
        "commands": rows,
        "free_codes": _free_codes(doc) if fw.get("present") else None,
        "note": (
            "handled_by is derived from Cmd:: references in each sketch. There is no "
            "central dispatcher, so a new command may need editing all 9 sketches."
        ),
    }


def _free_codes(doc: dict) -> dict[str, list[int]]:
    """Suggest unused codes per device range, honouring the suffix convention.

    Codes are structured as <device base> + <suffix>: x00/x01 disarm/arm, x03
    test, x7N parameters, x76 SET_PIN. Suggesting an arbitrary free integer would
    break a convention firmware and frontend both rely on.
    """
    used = set(doc["python"]["command_code_enum"].values())
    ranges = {
        "1xx controller": (100, 199), "2xx session": (200, 299), "3xx cue": (300, 399),
        "4xx pump": (400, 499), "5xx lick": (500, 599), "6xx laser": (600, 699),
        "9xx microscope": (900, 999), "10xx lever RH": (1000, 1099),
        "11xx SLM": (1100, 1199), "13xx lever LH": (1300, 1399),
    }
    return {
        label: [c for c in range(lo, hi + 1) if c not in used][:8]
        for label, (lo, hi) in ranges.items()
    }


@mcp.tool()
@tool_result
def get_hardware_map(workspace: Optional[str] = None) -> dict[str, Any]:
    """Show every hardware component's Python, firmware and frontend view side by side.

    One call answers "is this component consistent across all three layers".
    Each component carries a `mirrors` block and an `in_sync` flag. `fixed_pins`
    names pins that are hardwired in firmware and must never be offered as
    remappable.
    """
    ws = _workspace(workspace)
    if not ws.reacher.present:
        raise SchemaUnavailable("no reacher checkout found")
    doc = fetch(ws.reacher.path)
    ctx = checks.build_context(ws)
    fw = doc["firmware"]
    meta = ctx.pin_meta

    pins_h = fw.get("pins_h", {})
    symbol_for = {v: k for k, v in fw.get("pin_symbol_to_component", {}).items()}

    components = []
    for constraint in doc["python"]["pin_constraints"]:
        key = constraint["component_key"]
        symbol = symbol_for.get(key)
        firmware_view = {
            "present": symbol is not None,
            "pins_h_symbol": symbol,
            "default_pin": pins_h.get(symbol) if symbol else None,
        }
        frontend_view = {"present": False} if meta is None else {
            "present": key in meta["component_keys"],
            "set_pin_code": meta["set_pin_code"].get(key),
            "default_pin": meta["default_pin"].get(key),
            "requires_pwm": meta["requires_pwm"].get(key),
            "requires_pcint": meta["requires_pcint"].get(key),
            "has_control_tsx": _has_control(ws, key),
        }
        in_sync = None
        if meta is not None and fw.get("present"):
            in_sync = (
                frontend_view["present"]
                and frontend_view["set_pin_code"] == constraint["code"]
                and frontend_view["default_pin"] == firmware_view["default_pin"]
                and frontend_view["requires_pwm"] == constraint["requires_pwm"]
                and frontend_view["requires_pcint"] == constraint["requires_pcint"]
            )
        components.append({
            "key": key,
            "mirrors": {"python": constraint, "firmware": firmware_view, "frontend": frontend_view},
            "in_sync": in_sync,
        })

    return {
        "ok": True,
        "components": components,
        "pin_sets": doc["python"]["pin_sets"],
        "fixed_pins": fw.get("fixed_pins", {}),
        "frontend_available": meta is not None,
        "firmware_available": fw.get("present", False),
        "notes": ctx.notes,
        "caveat": (
            "in_sync is null where a layer is unavailable — that is UNVERIFIED, not in sync."
        ),
    }


def _has_control(ws, component_key: str) -> Optional[bool]:
    if not ws.labrynth.present:
        return None
    hardware = ws.labrynth.path / "web" / "src" / "components" / "hardware"
    stem = component_key.replace("_", "").lower()
    return any(stem in p.stem.lower() for p in hardware.glob("*Control.tsx"))


@mcp.tool()
@tool_result
def explain_event_flow(device: Optional[str] = None, workspace: Optional[str] = None) -> dict[str, Any]:
    """Trace how a device's names travel from firmware to the browser.

    Firmware spells device names differently per log level — the lick circuit is
    LICK at level 000 and LICK_CIRCUIT at level 007 — and the kernel rewrites some
    before emitting. Anything downstream of the kernel must be checked against
    `post_kernel`, never against the raw firmware namespaces. Getting this wrong
    reports correct code as broken.
    """
    ws = _workspace(workspace)
    if not ws.reacher.present:
        raise SchemaUnavailable("no reacher checkout found")
    doc = fetch(ws.reacher.path)
    fw = doc["firmware"]
    if not fw.get("present"):
        return {"ok": False, "error": "firmware_unavailable",
                "message": "this reacher tree has no firmware/ (installed-wheel layout)"}

    names = fw["device_names"]
    out = {
        "ok": True,
        "namespaces": names,
        "kernel_rewrites": doc["python"].get("post_kernel_event_rewrites", {}),
        "contract": {
            "validate_reacher_against_firmware": ["config", "param", "event"],
            "validate_anything_downstream_against_reacher": ["post_kernel"],
        },
        "levels": {
            "000": "device config -> WS 'config'",
            "001": "parameter change -> logged",
            "006": "firmware error -> WS 'error'",
            "007": "behavioral event -> WS 'event' (names rewritten first)",
            "008": "microscope frame -> WS 'frame' (a separate message type)",
            "009": "SLM timestamp -> WS 'event', device synthesized as SLM",
        },
    }
    if device:
        d = device.upper()
        out["device"] = {
            "queried": d,
            "appears_at_levels": sorted(lvl for lvl, ns in names["by_level"].items() if d in ns),
            "reaches_frontend_as": sorted(
                set(doc["python"].get("post_kernel_event_rewrites", {}).get(d, (d,)))
                & set(names["post_kernel"])
            ),
            "in_post_kernel": d in names["post_kernel"],
        }
    return out


@mcp.tool()
@tool_result
def check_consistency(
    scopes: Optional[list[str]] = None,
    workspace: Optional[str] = None,
) -> dict[str, Any]:
    """Run the cross-layer consistency rules and report what did and did not verify.

    Read `counts.unavailable` before `counts.pass`. An unavailable check did not
    run, so "12 passed" alongside "6 unavailable" is not a clean bill of health.
    `ok` is true only when everything ran AND passed.

    Scopes: commands, firmware, pins, boards, device_names.
    """
    result = checks.check_workspace(_workspace(workspace), scopes=scopes)
    result.setdefault("ok", False)
    return result


@mcp.tool()
@tool_result
def run_checks(names: list[str], workspace: Optional[str] = None) -> dict[str, Any]:
    """Run allowlisted verification commands (pytest, ruff, tsc, ...).

    Read `verdict`, never `exit_code`. A command can exit 0 having done nothing:
    labrynth's `npm run lint` does exactly that, and pytest reports success with
    skipped tests that verified nothing. `verdict` accounts for both —
    `pass_with_skips` and `UNAVAILABLE` are not passes.

    `npm run lint` and `firmware/compile.sh` are deliberately not available: the
    first is broken, the second rewrites committed build artifacts.
    """
    ws = _workspace(workspace)
    results = [runner.run_command(ws, name) for name in names]
    verdicts = [r["verdict"] for r in results]
    return {
        "ok": all(v == "pass" for v in verdicts),
        "all_verified": all(v == "pass" for v in verdicts),
        "results": results,
        "available": runner.available(ws),
        "caveat": "Only verdict 'pass' means verified. pass_with_skips, UNAVAILABLE and fail do not.",
    }


@mcp.prompt()
def reacher_change(description: str) -> str:
    """Make a coordinated change across REACHER's firmware, kernel and frontend."""
    return f"""\
The user wants this change to the REACHER platform:

    {description}

Work it in this order:

1. `describe_workspace` — confirm both checkouts are present and read the
   `tooling` block. If labrynth is missing, say so before planning: any change
   touching the UI is incomplete without it.
2. `check_consistency` — establish a clean baseline. If something already fails,
   tell the user before adding to it.
3. Decide whether this needs a code change at all. Some requests (moving a rig
   between board types, for instance) are runtime settings.
4. `list_commands` / `get_hardware_map` / `explain_event_flow` for ground truth.
   Never assume a command code or a device name; every one of them is mirrored by
   hand in several places and the spellings differ per layer.
5. Make the edits yourself, with your own tools. Do the edits the typechecker can
   catch first, then work the unguarded ones from an explicit list — nothing will
   catch an omission there.
6. `check_consistency` and `run_checks` again. Report `verdict`, and state
   plainly anything that came back UNAVAILABLE: those verified nothing.

Ask the user about anything genuinely ambiguous before editing, not after.
"""


@mcp.prompt()
def reacher_verify() -> str:
    """Run the full cross-repo consistency gate and report honestly."""
    return """\
Run `describe_workspace`, then `check_consistency`, then
`run_checks(["pytest_parity", "ruff", "tsc"])`.

Report three separate numbers: what passed, what failed, and what could not be
checked. Do not fold the third into the first. If anything is UNAVAILABLE, name
it and say what would make it runnable.
"""


def main() -> None:
    """Entry point for the ``reacher-mcp`` console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
