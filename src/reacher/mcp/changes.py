"""Intake form, change planning and the change-request record: the logic
behind ``change_request_form``, ``plan_change`` and ``change_request_record``.

Kept free of the MCP SDK so the test suite exercises it directly, and free of
writes: the plan is a list of edits for the agent to make with its own tools,
and the record is text the agent writes to the path it is given.

A plan is only as good as the request it is built from, so ``plan_change``
refuses to guess. Missing required fields come back as ``open_questions`` with
``required: true``; the agent asks the user, fills the form, and calls again.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Optional

from .guides import (
    DECISION_LIST, DEVICES, EXAMPLES, GUARD_PREFIXES, GUIDES, KINDS, PLACEHOLDERS,
    SAFETY_DEVICES, SAFETY_WORDS, TIMING_WORDS,
)

#: Version of the change-request record's front matter. Bump when a key is
#: renamed or removed; adding a key is compatible.
RECORD_SCHEMA = "reacher-change-request/1"

#: The fields of a change request. ``required_for`` lists the kinds that need
#: the field before a plan can be built ("*" = every code change).
FORM_FIELDS: list[dict[str, Any]] = [
    {"field": "request", "type": "string", "required_for": ["*"],
     "ask": "What does the user want, in their own words?"},
    {"field": "kind", "type": "enum", "values": list(KINDS), "required_for": ["*"],
     "ask": "Which kind of change is it? Answer decision_list from the top; the first yes "
            "is the kind. If you are not sure of an answer, ask the user that question."},
    {"field": "paradigms", "type": "list[str]",
     "required_for": ["device_setting", "new_device", "preset", "new_paradigm"],
     "ask": "Which paradigms must support it? List each one, including the _lite UNO "
            "builds (fr_lite, pr_lite, vi_lite, omission_lite) that should get it."},
    {"field": "lite_twins", "type": "enum", "values": ["include", "exclude"], "required_for": [],
     "ask": "Only asked when a listed paradigm has a _lite UNO build that is not listed: "
            "should the UNO build get it too? 'include' adds the _lite builds; 'exclude' "
            "keeps them out on purpose."},
    {"field": "boards", "type": "list[str]", "required_for": ["new_device"],
     "ask": "Which boards: mega, uno, or both?"},
    {"field": "device", "type": "string",
     "required_for": ["device_setting", "new_device", "pin_or_board"],
     "ask": "Which device? Use a component key from get_hardware_map (for example pump, "
            "laser, aux, lever_rh). For a new device, the new key you will add."},
    {"field": "setting", "type": "string", "required_for": ["device_setting"],
     "ask": "Short name of the new setting, in words joined by underscores, e.g. timeout_mode."},
    {"field": "pins", "type": "dict[str,int]", "required_for": ["new_device", "pin_or_board"],
     "ask": "Which pin on each board? For example {\"mega\": 22}."},
    {"field": "reaches_board", "type": "bool", "required_for": ["device_setting"],
     "ask": "Does the board need to know about it (true: a firmware command), or does only "
            "the computer use it, like which pumps count toward the limit (false)?"},
    {"field": "behavior", "type": "string", "required_for": ["device_setting", "new_device", "new_paradigm"],
     "ask": "What must happen, written as the events the event log will show (for example "
            "'LEVER_RH ACTIVE press, then PUMP_1 INFUSION')? Include what happens when the "
            "session ends in the middle of an output."},
    {"field": "done_when", "type": "string", "required_for": ["*"],
     "ask": "How will the user know it works? One check they can run or watch."},
    {"field": "commands", "type": "list[str]", "required_for": [],
     "ask": "Optional: registry names (from list_commands) of existing commands this change "
            "uses or changes. New commands are filled in by plan_change."},
    {"field": "answers", "type": "list[{question, answer}]", "required_for": [],
     "ask": "Optional: every question you asked the user and their answer, word for word. "
            "They go into the change-request record."},
]

_FIELD_NAMES = tuple(f["field"] for f in FORM_FIELDS)

_KIND_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("new_device", ("new device", "new sensor", "add a sensor", "ttl", "shock", "new hardware",
                    "never driven", "shock box", "camera", "speaker for", "new board")),
    ("new_paradigm", ("new paradigm", "schedule", "random ratio", "random-ratio", "new task")),
    ("preset", ("preset",)),
    ("pin_or_board", ("default pin", "pin by default")),
    ("runtime_setting", ("use pin", "on pin", "on an uno", "on a mega", "switch board",
                         "change the ratio", "change the interval")),
    ("ui_only", ("label", "layout", "color", "colour", "help text", "tooltip", "wording")),
    ("device_setting", ("option", "mode", "only count", "only after", "lh only", "only on")),
]


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


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
        "devices": sorted(DEVICES),
        "how_to_use": (
            "1. Answer decision_list from the top; the first yes is the kind. "
            "2. Ask the user only for fields that are required for that kind and not already "
            "clear from their words. Ask one question at a time. "
            "3. Call plan_change with every field you have. "
            "4. If two kinds both seem right, ask the user which; do not pick."
        ),
    }


def guess_kinds(text: str) -> list[str]:
    """Kinds the request text hints at, best first. A hint, never a decision."""
    low = text.lower()
    hits = [kind for kind, words in _KIND_HINTS if any(_has_word(low, w) for w in words)]
    return list(dict.fromkeys(hits))


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _missing(fields: dict[str, Any], kind: Optional[str]) -> list[dict[str, Any]]:
    out = []
    for f in FORM_FIELDS:
        need = "*" in f["required_for"] or (kind is not None and kind in f["required_for"])
        if kind == "runtime_setting" and f["field"] != "request":
            need = False
        if need and _empty(fields.get(f["field"])):
            out.append({"field": f["field"], "question": f["ask"], "required": True})
    return out


def _fill(template: str, ctx: dict[str, str]) -> str:
    for key, value in ctx.items():
        template = template.replace("{" + key + "}", value)
    return template


def _camel(snake: str) -> str:
    head, *rest = snake.split("_") or [""]
    return head + "".join(w.capitalize() for w in rest)


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


def _device_ctx(device: str, doc: Optional[dict]) -> dict[str, str]:
    """Every per-layer name of *device*, from DEVICES and the schema dump."""
    row = DEVICES.get(device, {})
    prefix = row.get("PREFIX") or re.sub(r"[^A-Z0-9]", "_", device.upper())
    symbols = (doc or {}).get("firmware", {}).get("pin_symbol_to_component", {})
    pin_symbol = next((sym for sym, key in symbols.items() if key == device), f"PIN_{prefix}")
    ui_state = row.get("ui_state", "")
    return {
        "device": device,
        "PREFIX": prefix,
        "param_code": _free_param_code(row.get("param_base"), doc),
        "pin_symbol": pin_symbol,
        "sim_key": row.get("sim_key") or "",
        "code_range": row.get("code_range", ""),
        "ui_key": row.get("ui_key", ""),
        "ui_state": ui_state,
        "ui_anchor": row.get("ui_anchor") or f"export interface {ui_state} extends DeviceArmState {{",
        "control": row.get("control", ""),
        "cli_id": row.get("cli_id") or "",
    }


def _free_param_code(base: Optional[int], doc: Optional[dict]) -> str:
    """The lowest unused code in a device's x70-x79 parameter block, as text."""
    if base is None:
        return "<code: pick from list_commands().free_codes>"
    used = set((doc or {}).get("python", {}).get("command_code_enum", {}).values())
    if not used:
        return "<code: call list_commands to find a free one>"
    free = [c for c in range(base, base + 10) if c not in used]
    return str(free[0]) if free else "<code: the parameter block is full; stop and ask the user>"


def _param_paradigms_do(key: str, paradigms: list[str], doc: Optional[dict]) -> str:
    """The PARAM_PARADIGMS instruction: an exact entry, or none when every paradigm gets it."""
    bases = sorted({p.removesuffix("_lite") for p in paradigms})
    every = (doc or {}).get("python", {}).get("paradigms", {}).get("non_lite")
    if every and set(bases) >= set(every):
        return "Add nothing to PARAM_PARADIGMS: every paradigm gets it."
    return f"Also add `{key}: {json.dumps(bases)},` to PARAM_PARADIGMS in this file."


def _lite_question(fields: dict[str, Any], doc: Optional[dict], kind: str,
                   host_only: bool) -> Optional[dict[str, Any]]:
    """Ask about _lite twins a firmware change would otherwise skip without anyone deciding."""
    if kind not in ("device_setting", "new_device") or host_only or fields.get("lite_twins"):
        return None
    device = (fields.get("device") or "").strip().lower()
    if DEVICES.get(device, {}).get("mega_only"):
        return None
    paradigms = list(fields.get("paradigms") or [])
    capable = (doc or {}).get("python", {}).get("paradigms", {}).get("lite_capable") or []
    missing = [f"{p}_lite" for p in paradigms if p in capable and f"{p}_lite" not in paradigms]
    if not missing:
        return None
    return {"field": "lite_twins", "required": True,
            "question": f"These paradigms have UNO builds that are not listed: {missing}. "
                        "Should the UNO builds get it too? Answer lite_twins='include' to add "
                        "them, or lite_twins='exclude' to keep them out on purpose."}


def _safety_reasons(kind: str, device: str, fields: dict[str, Any], text: str) -> list[str]:
    if kind == "runtime_setting":
        return []
    found: dict[str, str] = {}
    if device in SAFETY_DEVICES:
        found[device] = SAFETY_DEVICES[device]
    for word, what in SAFETY_WORDS.items():
        if _has_word(text, word):
            found.setdefault(word, what)
    outputs = sorted(set(found.values()))
    if not outputs:
        return []
    return [f"touches a safety output ({'; '.join(outputs)}): a person reviews the diff and "
            "runs one bench check before the change is used on an animal"]


def plan(
    fields: dict[str, Any],
    *,
    doc: Optional[dict] = None,
    roots: Optional[dict[str, Optional[Path]]] = None,
) -> dict[str, Any]:
    """Turn a filled change request into an ordered edit list.

    *doc* is the schema dump (for sketch paths, paradigms and pin symbols);
    *roots* maps repo name to checkout path (for anchor checks). Both are
    optional so the plan still comes back, marked unchecked, when a repo is
    absent.
    """
    roots = roots or {}
    fields = dict(fields)
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
            "next": "Answer decision_list from the top with the user; the first yes is the kind. "
                    "Set `kind` and call plan_change again. suggested_kinds is a keyword hint, "
                    "not an answer.",
        }

    guide = GUIDES[kind]
    if kind == "runtime_setting":
        return {
            "ok": True, "kind": kind, "outcome": "no_code_change",
            "title": guide["title"], "answers": guide["answers"],
            "next": "Tell the user which setting does this and where it is. Make no edits.",
        }

    host_only = kind == "device_setting" and fields.get("reaches_board") is False
    if fields.get("lite_twins") == "include":
        paradigms = list(fields.get("paradigms") or [])
        capable = (doc or {}).get("python", {}).get("paradigms", {}).get("lite_capable") or []
        paradigms += [f"{p}_lite" for p in paradigms if p in capable and f"{p}_lite" not in paradigms]
        fields["paradigms"] = paradigms

    open_q = _missing(fields, kind)
    paradigms = list(fields.get("paradigms") or [])
    known = (doc or {}).get("python", {}).get("paradigms", {}).get("all")
    if known and paradigms:
        unknown = [p for p in paradigms if p not in known]
        if unknown:
            open_q.append({"field": "paradigms", "required": True,
                           "question": f"Unknown paradigms {unknown}; known: {known}."})
    lite_q = _lite_question(fields, doc, kind, host_only)
    if lite_q:
        open_q.append(lite_q)

    device = (fields.get("device") or "").strip().lower()
    existing = set((doc or {}).get("python", {}).get("component_keys") or DEVICES)
    if device and kind in ("device_setting", "pin_or_board") and device not in existing:
        open_q.append({"field": "device", "required": True,
                       "question": f"Unknown device {device!r}; existing devices: {sorted(existing)}. "
                                   "If it is new hardware, the kind is new_device."})
    if device and kind == "new_device" and device in existing:
        open_q.append({"field": "device", "required": True,
                       "question": f"{device!r} already exists. A new option on it is a "
                                   "device_setting; a new device needs a new key."})

    setting = re.sub(r"[^a-z0-9]+", "_", (fields.get("setting") or "").strip().lower()).strip("_")
    ctx = dict(
        _device_ctx(device, doc),
        setting=setting,
        SETTING=setting.upper(),
        camel_setting=_camel(setting),
        paradigms=json.dumps(paradigms) if paradigms else "(paradigms not given)",
        param_paradigms=_param_paradigms_do(_camel(setting), paradigms, doc),
    )

    if kind == "device_setting" and not host_only and setting and device:
        name = f"{ctx['PREFIX']}_SET_{ctx['SETTING']}"
        code = (doc or {}).get("python", {}).get("command_code_enum", {}).get(name)
        if code is not None:
            open_q.append({"field": "setting", "required": True,
                           "question": f"{name} already exists (code {code}). This guide adds a "
                                       "new setting. Give the new setting a different name, or "
                                       "stop: changing what an existing command does is not "
                                       "covered by a guide; hand it to a person."})

    steps: list[dict[str, Any]] = []
    for raw in guide["steps"]:
        when = raw.get("when")
        if (when == "board" and host_only) or (when == "host" and not host_only):
            continue
        if raw.get("needs") and not ctx.get(raw["needs"]):
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

    record_repos = sorted({s["repo"] for s in steps})
    steps.append({
        "repo": " and ".join(record_repos),
        "path": "changes/YYYY-MM-DD-<slug>.md",
        "anchor": None,
        "do": "Call change_request_record with the same fields plus `answers`, and write its "
              "`text` to each path in its `targets`, in the repo named there. Commit it with "
              "the change.",
        "guard": "none",
        "verify": "by hand: the file exists in every repo this plan edits and its front "
                  "matter lists the same kind, paradigms and devices as this plan.",
        "anchor_status": "new file",
    })
    for i, step in enumerate(steps, 1):
        step["n"] = i

    silent = [s["n"] for s in steps if s["guard"] == "none"]
    guarded = len(steps) - len(silent)
    stale = [s["n"] for s in steps if s["anchor_status"].startswith(("missing", "ambiguous"))]

    stop_reasons = list(guide.get("stop_if", []))
    text = " ".join(str(fields.get(k) or "") for k in ("request", "behavior", "setting", "done_when")).lower()
    stop_reasons += _safety_reasons(kind, device, fields, text)
    # Timing is judged from what the user asked for, not from the acceptance check,
    # which names event classes and durations it only observes.
    asked = " ".join(str(fields.get(k) or "") for k in ("request", "behavior", "setting")).lower()
    if kind == "device_setting" and not host_only and any(_has_word(asked, w) for w in TIMING_WORDS):
        stop_reasons.append("changes firmware timing: needs a bench check on real hardware")

    notes = []
    if host_only:
        notes.append("reaches_board is false: the plan has no firmware, command-code or "
                     "simulator steps.")
    if kind == "device_setting" and not host_only:
        notes.append(guide["if_some_paradigms_lack_it"])

    return {
        "ok": True,
        "kind": kind,
        "outcome": "needs_answers" if any(q["required"] for q in open_q) else "plan",
        "title": guide["title"],
        "summary": guide["summary"],
        "small_model_ok": guide["small_model_ok"] and not stop_reasons,
        "bench_test": guide["bench_test"],
        "stop_and_hand_off": stop_reasons,
        "stop_and_hand_off_means": (
            "If this list is not empty: tell the user every reason in it before the first edit. "
            "You may still make the edits if the user says so, but do not report the change "
            "as ready to use; report it as 'needs review and a bench check by a person'."
        ),
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
        "notes": notes,
        "change_request": {k: fields.get(k) for k in _FIELD_NAMES},
        "finish_with": "check_consistency, then run_checks(['pytest_parity', 'ruff', 'tsc', "
                       "'lint', 'web_tests', 'cli_parity', "
                       "'windows_paths']). Report pass, fail and UNAVAILABLE "
                       "separately, and list every silent step you checked by hand.",
    }


def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return "-".join(words[:8]) or "change"


def _yaml(value: Any) -> str:
    # JSON scalars and flow collections are valid YAML, and json.dumps quotes
    # every string, so a request containing ':' or '#' cannot break the header.
    return json.dumps(value, ensure_ascii=False)


def record(
    fields: dict[str, Any],
    *,
    doc: Optional[dict] = None,
    roots: Optional[dict[str, Optional[Path]]] = None,
    date: Optional[str] = None,
) -> dict[str, Any]:
    """Render the confirmed change request as ``changes/YYYY-MM-DD-<slug>.md``.

    Returns the text and one target per repo the plan edits. Writes nothing.
    Refuses while the plan still has required open questions: a record of an
    unconfirmed request would be committed as if it were confirmed.
    """
    planned = plan(fields, doc=doc, roots=roots)
    if not planned.get("ok"):
        return planned
    if planned["outcome"] != "plan":
        return {"ok": False, "error": "not_confirmed", "outcome": planned["outcome"],
                "open_questions": planned.get("open_questions", []),
                "message": "Resolve every required open question with the user and call again."}

    day = date or _dt.date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return {"ok": False, "error": "bad_date", "message": f"date must be YYYY-MM-DD, got {day!r}"}

    cr = planned["change_request"]
    kind = planned["kind"]
    device = (cr.get("device") or "").strip().lower()
    ctx = _device_ctx(device, doc) if device else {}
    commands = list(cr.get("commands") or [])
    setting = re.sub(r"[^a-z0-9]+", "_", (cr.get("setting") or "").lower()).strip("_")
    if kind == "device_setting" and cr.get("reaches_board") and setting:
        commands.append(f"{ctx['PREFIX']}_SET_{setting.upper()}")
    known = {c["name"] for c in (doc or {}).get("python", {}).get("commands", [])}
    boards = list(cr.get("boards") or [])
    if not boards and kind in ("device_setting", "new_paradigm", "preset"):
        paradigms = cr.get("paradigms") or []
        boards = sorted({"uno" if p.endswith("_lite") else "mega" for p in paradigms})

    front = {
        "schema": RECORD_SCHEMA,
        "date": day,
        "kind": kind,
        "request": cr.get("request"),
        "paradigms": cr.get("paradigms") or [],
        "boards": boards,
        "devices": [device] if device else [],
        "pins": cr.get("pins") or {},
        "commands": [{"name": c, "status": "existing" if c in known else "new"} for c in commands],
        "reaches_board": cr.get("reaches_board"),
        "safety_flags": planned["stop_and_hand_off"],
        "small_model_ok": planned["small_model_ok"],
        "bench_test": planned["bench_test"],
        "silent_steps": planned["guard_summary"]["silent"],
    }
    lines = ["---"] + [f"{k}: {_yaml(v)}" for k, v in front.items()] + ["---", ""]
    lines += [f"# {cr.get('request')}", "", "## Behaviour (observable events)", "",
              cr.get("behavior") or "(not required for this kind)", "",
              "## Acceptance", "", cr.get("done_when") or "", "", "## Questions and answers", ""]
    answers = cr.get("answers") or []
    if answers:
        for qa in answers:
            lines += [f"- Q: {qa.get('question', '')}", f"  A: {qa.get('answer', '')}"]
    else:
        lines.append("- None asked.")
    lines += ["", "## Safety", ""]
    lines += [f"- {r}" for r in planned["stop_and_hand_off"]] or ["- No safety flags."]
    text = "\n".join(lines) + "\n"

    name = f"changes/{day}-{_slug(cr.get('request') or '')}.md"
    repos = sorted({s["repo"] for s in planned["steps"][:-1]})
    return {
        "ok": True,
        "text": text,
        "targets": [{"repo": r, "path": name} for r in repos],
        "next": "Write `text` to `path` in each target repo with your own file tool, unchanged, "
                "and commit it in the same pull request as the change.",
    }


def validate_guides() -> list[str]:
    """Structural problems in GUIDES. Empty means well formed."""
    problems = []
    allowed = set(PLACEHOLDERS)
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
            if step.get("when") not in (None, "board", "host"):
                problems.append(f"{kind} step {i}: bad when {step['when']!r}")
            for key in ("path", "anchor", "do", "verify"):
                for name in re.findall(r"\{(\w+)\}", step[key]):
                    if name not in allowed:
                        problems.append(f"{kind} step {i}: unknown placeholder {{{name}}} in {key}")
            if re.search(r"\bas appropriate\b|\bif needed\b|\bas needed\b", step["do"], re.I):
                problems.append(f"{kind} step {i}: vague instruction")
    return problems
