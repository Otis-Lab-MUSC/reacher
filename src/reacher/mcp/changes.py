"""Intake form and change planning: the logic behind ``change_request_form``
and ``plan_change``.

Kept free of the MCP SDK so the test suite exercises it directly, and free of
writes: the plan is a list of edits for the agent to make with its own tools.

A plan is only as good as the request it is built from, so ``plan_change``
refuses to guess. Missing required fields come back as ``open_questions`` with
``required: true``; the agent asks the user, fills the form, and calls again.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Optional

from .guides import (
    DECISION_LIST, EXAMPLES, GUARD_PREFIXES, GUIDES, KINDS, SAFETY_DEVICES, TIMING_WORDS,
)

#: The fields of a change request. ``required_for`` lists the kinds that need
#: the field before a plan can be built ("*" = every code change).
FORM_FIELDS: list[dict[str, Any]] = [
    {"field": "request", "type": "string", "required_for": ["*"],
     "ask": "What does the user want, in their own words?"},
    {"field": "kind", "type": "enum", "values": list(KINDS), "required_for": ["*"],
     "ask": "Which kind of change is it? Use decision_list; ask the user if two kinds fit."},
    {"field": "paradigms", "type": "list[str]",
     "required_for": ["device_setting", "new_device", "preset"],
     "ask": "Which paradigms must support it? (fr, pr, vi, omission, pavlovian, and the _lite UNO twins)"},
    {"field": "boards", "type": "list[str]", "required_for": ["new_device", "pin_or_board"],
     "ask": "Which boards: mega, uno, or both?"},
    {"field": "device", "type": "string",
     "required_for": ["device_setting", "new_device", "pin_or_board"],
     "ask": "Which device (component key from get_hardware_map, e.g. pump, laser, aux)? "
            "For a new device, the key you will add."},
    {"field": "setting", "type": "string", "required_for": ["device_setting"],
     "ask": "Short name of the new setting, e.g. TIMEOUT_MODE."},
    {"field": "pins", "type": "dict[str,int]", "required_for": ["new_device", "pin_or_board"],
     "ask": "Which pin on each board?"},
    {"field": "reaches_board", "type": "bool", "required_for": ["device_setting"],
     "ask": "Does the board need to know about it (a firmware change), or is it host-only "
            "like counted pumps?"},
    {"field": "behavior", "type": "string", "required_for": ["device_setting", "new_device", "new_paradigm"],
     "ask": "What must the board do, step by step, including what happens when the session "
            "ends mid-action?"},
    {"field": "done_when", "type": "string", "required_for": ["*"],
     "ask": "How will the user know it works? One check they can run or watch."},
]

_KIND_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("new_device", ("new device", "new sensor", "add a sensor", "ttl", "shock", "new hardware",
                    "never driven", "box", "camera", "speaker for")),
    ("new_paradigm", ("new paradigm", "schedule", "random ratio", "random-ratio", "new task")),
    ("preset", ("preset",)),
    ("pin_or_board", ("default pin", "pin by default")),
    ("runtime_setting", ("use pin", "on pin", "on an uno", "on a mega", "switch board",
                         "change the ratio", "change the interval")),
    ("ui_only", ("label", "layout", "color", "colour", "help text", "tooltip", "wording")),
    ("device_setting", ("option", "mode", "only count", "only after", "lh only", "only on")),
]


def form(paradigms: Optional[list[str]] = None) -> dict[str, Any]:
    """The blank intake form, the decision list, and worked examples."""
    fields = copy.deepcopy(FORM_FIELDS)
    if paradigms:
        for f in fields:
            if f["field"] == "paradigms":
                f["values"] = paradigms
    return {
        "ok": True,
        "fields": fields,
        "decision_list": DECISION_LIST,
        "examples": EXAMPLES,
        "how_to_use": (
            "Ask the user only for fields that are required for the kind and not already "
            "clear from their words. Then call plan_change with the filled form. If two "
            "kinds fit, ask the user which; do not pick."
        ),
    }


def guess_kinds(text: str) -> list[str]:
    """Kinds the request text hints at, best first. A hint, never a decision."""
    low = f" {text.lower()} "
    hits = [kind for kind, words in _KIND_HINTS if any(w in low for w in words)]
    return list(dict.fromkeys(hits))


def _missing(fields: dict[str, Any], kind: Optional[str]) -> list[dict[str, Any]]:
    out = []
    for f in FORM_FIELDS:
        need = "*" in f["required_for"] or (kind is not None and kind in f["required_for"])
        if kind == "runtime_setting" and f["field"] != "request":
            need = False
        value = fields.get(f["field"])
        if need and (value is None or value == "" or value == [] or value == {}):
            out.append({"field": f["field"], "question": f["ask"], "required": True})
    return out


def _fill(template: str, ctx: dict[str, str]) -> str:
    for key, value in ctx.items():
        template = template.replace("{" + key + "}", value)
    return template


def _sketch_paths(doc: Optional[dict], paradigms: list[str]) -> list[tuple[str, str]]:
    if not doc:
        return []
    sketches = doc.get("firmware", {}).get("sketches", [])
    return [(s["name"], s["path"]) for s in sketches if s["name"] in paradigms]


def _anchor_status(roots: dict[str, Optional[Path]], repo: str, path: str, anchor: str) -> str:
    root = roots.get(repo)
    if root is None:
        return "unchecked: repo not present"
    target = root / path
    try:
        text = target.read_text(errors="replace")
    except OSError:
        return "missing: file not found"
    n = text.count(anchor)
    if n == 0:
        return "missing: anchor not found; the guide is stale, tell the user"
    return "found" if n == 1 else f"ambiguous: anchor occurs {n} times"


def plan(
    fields: dict[str, Any],
    *,
    doc: Optional[dict] = None,
    roots: Optional[dict[str, Optional[Path]]] = None,
) -> dict[str, Any]:
    """Turn a filled change request into an ordered edit list.

    *doc* is the schema dump (for sketch paths and paradigms); *roots* maps repo
    name to checkout path (for anchor checks). Both are optional so the plan
    still comes back, marked unchecked, when a repo is absent.
    """
    roots = roots or {}
    request = (fields.get("request") or "").strip()
    kind = fields.get("kind")

    if kind is not None and kind not in GUIDES:
        return {"ok": False, "error": "unknown_kind",
                "message": f"kind must be one of {list(KINDS)}, got {kind!r}"}

    if kind is None:
        return {
            "ok": True,
            "outcome": "needs_kind",
            "suggested_kinds": guess_kinds(request),
            "decision_list": DECISION_LIST,
            "open_questions": _missing(fields, None),
            "next": "Work down decision_list with the user, set `kind`, and call plan_change again. "
                    "suggested_kinds is a keyword hint only.",
        }

    guide = GUIDES[kind]
    if kind == "runtime_setting":
        return {
            "ok": True, "kind": kind, "outcome": "no_code_change",
            "title": guide["title"], "answers": guide["answers"],
            "next": "Tell the user which setting does this and where it is. Make no edits.",
        }

    open_q = _missing(fields, kind)
    paradigms = list(fields.get("paradigms") or [])
    known = (doc or {}).get("python", {}).get("paradigms", {}).get("all")
    if known and paradigms:
        unknown = [p for p in paradigms if p not in known]
        if unknown:
            open_q.append({"field": "paradigms", "required": True,
                           "question": f"Unknown paradigms {unknown}; known: {known}."})

    device = (fields.get("device") or "").strip()
    setting = (fields.get("setting") or "").strip()
    ctx = {
        "device": device.lower(),
        "DEVICE": re.sub(r"[^A-Z0-9]", "_", device.upper()),
        "setting": setting.lower(),
        "SETTING": re.sub(r"[^A-Z0-9]", "_", setting.upper()),
        "paradigms": ", ".join(paradigms) or "(paradigms not given)",
    }

    host_only = kind == "device_setting" and fields.get("reaches_board") is False
    steps: list[dict[str, Any]] = []
    for raw in guide["steps"]:
        if host_only and (raw.get("firmware_only") or raw["path"].endswith("simulator.py")):
            continue
        targets = [(None, raw["path"])]
        if raw.get("each_sketch"):
            targets = [(name, path) for name, path in _sketch_paths(doc, paradigms)] or [
                ("(each sketch for the listed paradigms)", "firmware/<paradigm>/<paradigm>.ino")]
        for sketch, path in targets:
            local = dict(ctx, sketch=sketch or "", sketch_path=path)
            step = {
                "repo": raw["repo"],
                "path": _fill(path, local),
                "anchor": _fill(raw["anchor"], local),
                "do": _fill(raw["do"], local),
                "guard": raw["guard"],
                "verify": _fill(raw["verify"], local),
            }
            step["anchor_status"] = (
                "unchecked: placeholder path" if "<" in step["path"]
                else _anchor_status(roots, step["repo"], step["path"], step["anchor"])
            )
            steps.append(step)
    for i, step in enumerate(steps, 1):
        step["n"] = i

    silent = [s["n"] for s in steps if s["guard"] == "none"]
    guarded = len(steps) - len(silent)
    stale = [s["n"] for s in steps if s["anchor_status"].startswith(("missing", "ambiguous"))]

    stop_reasons = list(guide.get("stop_if", []))
    behaviour = f"{request} {fields.get('behavior') or ''}".lower()
    if device.lower() in SAFETY_DEVICES and kind in ("device_setting", "new_device"):
        stop_reasons.append(
            f"{device} is a safety device ({SAFETY_DEVICES[device.lower()]}): a person reviews "
            "the firmware diff and runs one bench check before the change is used on an animal")
    if kind == "device_setting" and not host_only and any(w in behaviour for w in TIMING_WORDS):
        stop_reasons.append("changes firmware timing: needs a bench check on real hardware")
    if kind == "pin_or_board" and "board" in behaviour and "new" in behaviour:
        stop_reasons.append("adds a new board type")

    return {
        "ok": True,
        "kind": kind,
        "outcome": "needs_answers" if any(q["required"] for q in open_q) else "plan",
        "title": guide["title"],
        "summary": guide["summary"],
        "small_model_ok": guide["small_model_ok"] and not stop_reasons,
        "bench_test": guide["bench_test"],
        "stop_and_hand_off": stop_reasons,
        "open_questions": open_q,
        "steps": steps,
        "guard_summary": {
            "steps": len(steps),
            "guarded": guarded,
            "silent": len(silent),
            "silent_steps": silent,
            "meaning": "silent steps have NO automated guard: verify each one by hand as its "
                       "`verify` says, and list them in your report",
        },
        "stale_anchors": stale,
        "notes": [n for n in (guide.get("host_only_note") if host_only else None,
                              guide.get("if_some_paradigms_lack_it")) if n],
        "change_request": {k: fields.get(k) for k in (f["field"] for f in FORM_FIELDS)},
        "finish_with": "check_consistency, then run_checks(['pytest_parity', 'ruff', 'tsc', "
                       "'lint', 'web_tests', 'cli_parity']). Report pass, fail and UNAVAILABLE "
                       "separately, and list every silent step you checked by hand.",
    }


def validate_guides() -> list[str]:
    """Structural problems in GUIDES. Empty means well formed."""
    problems = []
    for kind, guide in GUIDES.items():
        for key in ("title", "code", "small_model_ok", "bench_test", "summary", "steps"):
            if key not in guide:
                problems.append(f"{kind}: missing {key}")
        if guide.get("code") and not guide.get("steps"):
            problems.append(f"{kind}: a code change with no steps")
        for i, step in enumerate(guide.get("steps", []), 1):
            if not step["guard"].startswith(GUARD_PREFIXES):
                problems.append(f"{kind} step {i}: bad guard {step['guard']!r}")
            if step["guard"] == "none" and not step["verify"].startswith("by hand"):
                problems.append(f"{kind} step {i}: a silent step must say how to verify by hand")
    return problems
