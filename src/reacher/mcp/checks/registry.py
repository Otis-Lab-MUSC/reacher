"""The consistency-check engine shared by the MCP server and the test suite.

A check reports one of four statuses, and the distinction between the last two
is the whole point of this module:

    pass         verified, and it holds
    fail         verified, and it does not hold
    unavailable  could NOT be verified — a repo, tool, or tree is missing
    error        the check itself broke

``unavailable`` is never a pass. Both repos have a way to report success while
verifying nothing — labrynth's ``npm run lint`` exits 0 with no config, and this
repo's firmware tests skip silently without a firmware tree — so a result type
that cannot distinguish "checked and fine" from "did not check" would launder
exactly the failures this tooling exists to catch.

Every failing result must carry ``evidence`` naming both sides concretely. A
check that reports "mismatch" without saying which values differ tells an agent
nothing it can act on.

And every result carries ``provenance``: which sources the rule actually read.
This exists because of a failure that recurred four times while this tooling was
being built — each time, a contract was modelled from a partial view of its
producer, and each time the *correct* code was the thing that looked wrong. A
rule saying "SLM is not in post_kernel" invites an agent to delete a working
line. The same rule saying "...derived from levels 007+009 via code_dict" invites
the question that saves it. Rules that suggest deleting or narrowing something
must set ``suggests_removal``, which forces provenance into the fix hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Result:
    """The outcome of one check."""

    id: str
    title: str
    severity: Severity
    status: Status
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    fix_hint: str = ""
    #: Which sources this verdict was derived from. Required on a failure.
    provenance: tuple[str, ...] = ()
    #: True when acting on this finding means deleting or narrowing something.
    #: Those are the findings that need provenance most: the thing being removed
    #: may be correct and the rule's model of the producer incomplete.
    suggests_removal: bool = False

    def as_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status.value,
            "message": self.message,
            "evidence": self.evidence,
            "fix_hint": self.fix_hint,
            "derived_from": list(self.provenance),
        }
        if self.suggests_removal:
            out["before_removing"] = (
                "This finding suggests deleting or narrowing something. Confirm the sources "
                f"in derived_from ({', '.join(self.provenance) or 'unspecified'}) are the "
                "WHOLE producer before acting — a name absent from a partial model looks "
                "exactly like a name that should be removed."
            )
        return out


@dataclass(frozen=True)
class Check:
    """A single consistency rule."""

    id: str
    title: str
    scope: str
    severity: Severity
    fn: Callable[["CheckContext"], Result]
    #: What must be present for this check to mean anything. A check whose
    #: requirements are unmet reports UNAVAILABLE — it never quietly passes.
    requires: tuple[str, ...] = ()


@dataclass
class CheckContext:
    """Everything a check may read. Checks never touch the filesystem directly."""

    schema: Optional[dict[str, Any]] = None
    pin_meta: Optional[dict[str, Any]] = None
    board_types: Optional[list[str]] = None
    reacher_root: Optional[Any] = None
    labrynth_root: Optional[Any] = None
    notes: list[str] = field(default_factory=list)

    def missing(self, requires: tuple[str, ...]) -> list[str]:
        """Return the unmet requirements from *requires*."""
        available = {
            "schema": self.schema is not None,
            "firmware": bool((self.schema or {}).get("firmware", {}).get("present")),
            "frontend": self.pin_meta is not None,
            "board_types": self.board_types is not None,
        }
        return [name for name in requires if not available.get(name, False)]


_REGISTRY: dict[str, Check] = {}


def register(
    id: str, title: str, scope: str, severity: Severity = Severity.ERROR, requires: tuple = ()
):
    """Decorator registering a check function."""

    def wrap(fn: Callable[[CheckContext], Result]) -> Callable[[CheckContext], Result]:
        if id in _REGISTRY:
            raise ValueError(f"duplicate check id {id!r}")
        _REGISTRY[id] = Check(id=id, title=title, scope=scope, severity=severity, fn=fn, requires=requires)
        return fn

    return wrap


def all_checks() -> list[Check]:
    return sorted(_REGISTRY.values(), key=lambda c: c.id)


def run(
    ctx: CheckContext,
    *,
    scopes: Optional[list[str]] = None,
    ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run the selected checks and summarize.

    The summary leads with ``unavailable`` alongside the pass and fail counts,
    because "12 passed" is a misleading headline when six could not run.
    """
    selected = [
        c for c in all_checks()
        if (scopes is None or c.scope in scopes) and (ids is None or c.id in ids)
    ]

    results: list[Result] = []
    for check in selected:
        unmet = ctx.missing(check.requires)
        if unmet:
            results.append(
                Result(
                    id=check.id, title=check.title, severity=check.severity,
                    status=Status.UNAVAILABLE,
                    message=f"not verified — missing: {', '.join(unmet)}",
                    fix_hint=_hint_for(unmet),
                    # An unavailable result is the partial model the module docstring
                    # warns about, so it needs provenance more than a passing one does:
                    # naming what it needed and could not read.
                    provenance=tuple(f"{name} (not present in this workspace)" for name in unmet),
                )
            )
            continue
        try:
            result = check.fn(ctx)
            if result.status in (Status.FAIL, Status.PASS) and not result.provenance:
                result.provenance = ("(unspecified — this rule should declare its sources)",)
            results.append(result)
        except Exception as exc:  # a broken check must not mask the others
            results.append(
                Result(
                    id=check.id, title=check.title, severity=check.severity,
                    status=Status.ERROR,
                    message=f"{type(exc).__name__}: {exc}",
                    fix_hint="The check itself failed. Treat as unverified, not as passing.",
                )
            )

    counts = {s.value: sum(1 for r in results if r.status is s) for s in Status}
    failed = counts["fail"] + counts["error"]
    return {
        # `ok` means nothing failed AND nothing was left unverified. A caller
        # that only reads this field is still told the truth.
        "ok": failed == 0 and counts["unavailable"] == 0,
        "verified": failed == 0,
        "counts": counts,
        "summary": (
            f"{counts['pass']} passed, {counts['fail']} failed, "
            f"{counts['unavailable']} UNAVAILABLE (not verified), {counts['error']} errored"
        ),
        "results": [r.as_dict() for r in results],
        "notes": ctx.notes,
    }


def _hint_for(unmet: list[str]) -> str:
    hints = {
        "schema": "No reacher checkout found. Set $REACHER_WORKSPACE to the directory containing it.",
        "firmware": "This reacher tree has no firmware/ (installed-wheel layout); firmware checks cannot run.",
        "frontend": "No labrynth checkout found. Clone it as a sibling of the reacher checkout.",
        "board_types": "labrynth's types/index.ts could not be read.",
    }
    return " ".join(hints.get(name, "") for name in unmet).strip()


# --- Helpers for writing checks -------------------------------------------


def compare_mappings(
    check: Check | Any,
    left_name: str,
    left: dict,
    right_name: str,
    right: dict,
    fix_hint: str = "",
    suggests_removal: bool = False,
) -> Result:
    """Compare two mappings and build a Result naming every differing key."""
    differing = {
        key: {left_name: left.get(key), right_name: right.get(key)}
        for key in set(left) | set(right)
        if left.get(key) != right.get(key)
    }
    provenance = (left_name, right_name)
    if not differing:
        return Result(
            id=check.id, title=check.title, severity=check.severity, status=Status.PASS,
            message=f"{len(left)} entries agree", provenance=provenance,
        )
    return Result(
        id=check.id, title=check.title, severity=check.severity, status=Status.FAIL,
        message=f"{len(differing)} entries differ between {left_name} and {right_name}",
        evidence={"differing": differing, "left": left_name, "right": right_name},
        fix_hint=fix_hint,
        provenance=provenance,
        suggests_removal=suggests_removal,
    )
