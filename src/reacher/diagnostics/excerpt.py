"""Compact diagnostic excerpt for a bug report.

The full run ZIP can be tens of MB and a GitHub issue body is capped at 65,536
characters.  This builder picks the records a developer actually needs — process
meta, errors, recent UI actions, session lifecycle — and hard-caps the result
so it fits both a 1.5B context window and an issue body.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional

from . import get_sink
from .redact import redact

#: Hard cap on the excerpt text.  Leaves headroom for the user's description
#: and the model's JSON wrapper inside a ~8k-token window, and for GitHub.
EXCERPT_MAX_CHARS = 24_000

_ERROR_LEVELS = {"error", "warn", "fatal"}
_MAX_ERROR_RECORDS = 80
_MAX_UI_RECORDS = 40
_MAX_STATE_RECORDS = 20
_MAX_LINE = 400


def build_current_excerpt(max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """Flush the active run and return an excerpt, or empty string if none."""
    sink = get_sink()
    if sink is None or not os.path.isdir(sink.run_dir):
        return ""
    sink.flush_now()
    return build_excerpt(sink.run_dir, max_chars=max_chars)


def build_excerpt(run_dir: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """Build a capped markdown excerpt from a run directory."""
    sections: list[str] = []

    meta_text = _meta_block(os.path.join(run_dir, "meta.json"))
    if meta_text:
        sections.append(meta_text)

    records = list(_iter_records(run_dir))
    errors = [r for r in records if str(r.get("lvl", "")).lower() in _ERROR_LEVELS]
    ui = [r for r in records if str(r.get("evt", "")).startswith("ui.")]
    states = [r for r in records if r.get("evt") == "session.state"]

    if errors:
        sections.append(_record_block("Errors / warnings", errors[-_MAX_ERROR_RECORDS:]))
    if ui:
        sections.append(_record_block("Recent UI events", ui[-_MAX_UI_RECORDS:]))
    if states:
        sections.append(_record_block("Session lifecycle", states[-_MAX_STATE_RECORDS:]))

    if not sections:
        return "(no diagnostic records available)"

    text = "\n\n".join(sections)
    if len(text) <= max_chars:
        return text
    keep = max_chars - len("\n\n… [truncated]\n")
    return text[:keep] + "\n\n… [truncated]\n"


def _meta_block(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(meta, dict):
        return ""
    keys = ("run_id", "version", "python", "platform", "machine", "frozen")
    lines = [f"{k}: {meta[k]}" for k in keys if k in meta]
    argv = meta.get("argv")
    if isinstance(argv, list):
        lines.append("argv: " + " ".join(str(a) for a in argv[:8]))
    if not lines:
        return ""
    return "### Process\n" + "\n".join(lines)


def _iter_records(run_dir: str) -> Iterable[dict[str, Any]]:
    """Yield records oldest-first, including rotated ``app.ndjson.N`` segments."""
    current = os.path.join(run_dir, "app.ndjson")
    paths: list[str] = []
    for i in range(5, 0, -1):
        rotated = f"{current}.{i}"
        if os.path.isfile(rotated):
            paths.append(rotated)
    if os.path.isfile(current):
        paths.append(current)
    for path in paths:
        yield from _read_ndjson(path)


def _read_ndjson(path: str) -> Iterable[dict[str, Any]]:
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def _record_block(title: str, records: list[dict[str, Any]]) -> str:
    lines = [f"### {title}"]
    for rec in records:
        lines.append(_format_record(rec))
    return "\n".join(lines)


def _format_record(rec: dict[str, Any]) -> str:
    ts = rec.get("ts", "")
    lvl = rec.get("lvl", "")
    evt = rec.get("evt", "")
    msg = rec.get("msg", "")
    src = rec.get("src", "")
    data = rec.get("data")
    parts = [str(ts), str(lvl), str(evt)]
    if src:
        parts.append(str(src))
    if msg:
        parts.append(str(msg))
    if isinstance(data, dict) and data:
        safe = redact(data)
        dumped = json.dumps(safe, default=str, ensure_ascii=False)
        if len(dumped) > 180:
            dumped = dumped[:177] + "…"
        parts.append(dumped)
    line = " | ".join(parts)
    if len(line) > _MAX_LINE:
        line = line[: _MAX_LINE - 1] + "…"
    return line


def excerpt_or_empty(run_dir: Optional[str] = None, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """Convenience wrapper used by the report endpoint."""
    if run_dir:
        return build_excerpt(run_dir, max_chars=max_chars)
    return build_current_excerpt(max_chars=max_chars)
