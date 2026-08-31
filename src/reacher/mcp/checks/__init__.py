"""Consistency rules, and the context builder that feeds them.

Importing this package registers every rule. The engine lives in ``registry``;
the rules are grouped by what they compare.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema_client import SchemaUnavailable, fetch
from ..sources import ts
from ..workspace import Workspace, discover
from . import commands, pins  # noqa: F401  - imported for their registrations
from .registry import CheckContext, Result, Severity, Status, all_checks, run

__all__ = [
    "CheckContext", "Result", "Severity", "Status",
    "all_checks", "run", "build_context", "check_workspace",
]


def build_context(workspace: Optional[Workspace] = None) -> CheckContext:
    """Gather everything the rules read, degrading loudly on each absence.

    Nothing here raises. A missing repo or an unparseable file becomes a note
    plus a ``None`` field, which the engine turns into UNAVAILABLE results — the
    alternative, an empty table, would read to every comparison as "no drift".
    """
    ws = workspace or discover()
    ctx = CheckContext(
        reacher_root=ws.reacher.path,
        labrynth_root=ws.labrynth.path,
        notes=list(ws.warnings),
    )

    if ws.reacher.present:
        try:
            ctx.schema = fetch(ws.reacher.path)
        except SchemaUnavailable as exc:
            ctx.notes.append(f"ground truth UNAVAILABLE: {exc}")

    if ws.labrynth.present:
        web_src = ws.labrynth.path / "web" / "src"
        try:
            ctx.pin_meta = ts.parse_pin_meta(web_src / "components" / "hardware" / "pinMeta.ts")
        except (OSError, ts.TypeScriptParseError) as exc:
            ctx.notes.append(f"pinMeta.ts UNAVAILABLE: {exc}")
        try:
            ctx.board_types = ts.parse_board_type_union((web_src / "types" / "index.ts").read_text())
        except (OSError, ts.TypeScriptParseError) as exc:
            ctx.notes.append(f"types/index.ts BoardType UNAVAILABLE: {exc}")

    return ctx


def check_workspace(
    workspace: Optional[Workspace] = None,
    *,
    scopes: Optional[list[str]] = None,
    ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Discover, gather, and run — the entry point behind the MCP tool."""
    return run(build_context(workspace), scopes=scopes, ids=ids)
