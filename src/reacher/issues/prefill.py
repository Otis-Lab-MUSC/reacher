"""Build a pre-filled GitHub "New Issue" link — no token, no relay, no LLM.

The user submits the issue themselves, in their own browser, under their own
GitHub account. This module only composes the title/body text and the URL;
it never talks to the network or spawns a subprocess.

GitHub issue bodies are capped at 65,536 characters server-side, but the
binding constraint here is the much smaller practical URL length a browser
(and GitHub itself) will reliably accept for ``issues/new?title=&body=``
query params — see ``URL_BUDGET``.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from ..diagnostics.excerpt import build_current_excerpt

ALLOWED_REPOS = ("labrynth", "reacher")
_DEFAULT_OWNER = "Otis-Lab-MUSC"

ALLOWED_LABELS = [
    "bug",
    "enhancement",
    "question",
    "hardware",
    "camera",
    "data-export",
    "session-management",
    "UI",
    "firmware",
    "installation",
    "performance",
    "documentation",
]

DEVELOP_LABEL = "develop"

#: Conservative ceiling for the whole encoded URL. Well under the practical
#: ~8k limit most browsers and GitHub itself will accept before truncating
#: or rejecting a prefilled ``issues/new`` link.
URL_BUDGET = 6_000

#: Excerpt never gets more than this even when the rest of the URL is short —
#: keeps a one-line description from paying for a needlessly huge excerpt.
_MAX_EXCERPT_CHARS = 4_000
_MIN_EXCERPT_CHARS = 200

_STEPS_PLACEHOLDER = "<!-- What were you doing right before this happened? -->"
_TITLE_MAX = 72


def github_owner() -> str:
    return os.environ.get("REACHER_GITHUB_OWNER", "").strip() or _DEFAULT_OWNER


def _title_from(description: str) -> str:
    first_line = description.strip().splitlines()[0] if description.strip() else "Issue report"
    if len(first_line) > _TITLE_MAX:
        first_line = first_line[: _TITLE_MAX - 1].rstrip() + "…"
    return first_line


def _body(*, description: str, steps: str, severity: str, versions: str, excerpt: str) -> str:
    sections = ["## What happened", description.strip() or "(no description provided)"]

    sections.append("## Steps to Reproduce")
    sections.append(steps.strip() or _STEPS_PLACEHOLDER)

    if severity:
        sections.append(f"## Severity\n{severity}")

    sections.append(f"## Environment\n{versions}")

    sections.append("## Diagnostic excerpt")
    sections.append(f"```\n{excerpt}\n```" if excerpt else "(none captured)")

    return "\n\n".join(sections)


def build_prefill(
    *,
    repo: str,
    description: str,
    steps: str = "",
    severity: str = "",
    versions: str = "",
    labels: list[str] | None = None,
) -> dict:
    """Compose ``{title, body, labels, url}`` for a pre-filled GitHub issue link.

    Shrinks the diagnostic excerpt as needed so the final encoded URL stays
    within ``URL_BUDGET`` — the description and steps are the user's own
    words and are never truncated here.
    """
    owner = github_owner()
    allowed_labels = [lab for lab in dict.fromkeys(labels or []) if lab in ALLOWED_LABELS]
    all_labels = list(dict.fromkeys([*allowed_labels, DEVELOP_LABEL]))

    title = _title_from(description)

    excerpt_cap = _MAX_EXCERPT_CHARS
    while True:
        excerpt = build_current_excerpt(max_chars=excerpt_cap) if excerpt_cap > 0 else ""
        body = _body(description=description, steps=steps, severity=severity, versions=versions, excerpt=excerpt)
        url = issue_url(owner, repo, title, body, all_labels)
        if len(url) <= URL_BUDGET or excerpt_cap <= _MIN_EXCERPT_CHARS:
            break
        excerpt_cap = max(_MIN_EXCERPT_CHARS, excerpt_cap - (len(url) - URL_BUDGET) - 50)

    # Shrinking the excerpt to its floor is not a guarantee — a long enough
    # description/steps input still blows the budget on its own. Fall back to
    # a blunt truncation of the body so this never hands back a link the
    # browser or GitHub will reject outright.
    while len(url) > URL_BUDGET and len(body) > 200:
        cut = min(len(body), max(200, len(body) - (len(url) - URL_BUDGET) - 50))
        body = body[:cut] + "\n\n… [truncated to fit the GitHub link]\n"
        url = issue_url(owner, repo, title, body, all_labels)

    return {"title": title, "body": body, "labels": all_labels, "url": url, "repo": repo, "owner": owner}


def issue_url(owner: str, repo: str, title: str, body: str, labels: list[str]) -> str:
    params = {"title": title, "body": body}
    if labels:
        params["labels"] = ",".join(labels)
    return f"https://github.com/{owner}/{repo}/issues/new?{urlencode(params)}"
