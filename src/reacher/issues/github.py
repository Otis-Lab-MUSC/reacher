"""Create GitHub issues via the REST API.

The token is operator-configured (``REACHER_GITHUB_TOKEN``) and is never
shipped in the installer.  Uses the shared ``httpx.AsyncClient`` from app
state, matching ``update.py``.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import HTTPException

from .summarize import AGENT_READY_LABEL, DEVELOP_LABEL, FALLBACK_LABEL

logger = logging.getLogger(__name__)

ALLOWED_REPOS = ("labrynth", "reacher")
_DEFAULT_OWNER = "Otis-Lab-MUSC"
_API_VERSION = "2022-11-28"

_LABEL_COLORS = {
    "bug": "d73a4a",
    "enhancement": "a2eeef",
    "question": "d876e3",
    "hardware": "e4e669",
    "camera": "0075ca",
    "data-export": "cfd3d7",
    "session-management": "0e8a16",
    "UI": "f9d0c4",
    "firmware": "5319e7",
    "installation": "bfd4f2",
    "performance": "ffa500",
    "documentation": "0075ca",
    DEVELOP_LABEL: "1d76db",
    AGENT_READY_LABEL: "5319e7",
    FALLBACK_LABEL: "fbca04",
}


def github_token() -> str:
    return os.environ.get("REACHER_GITHUB_TOKEN", "").strip()


def github_owner() -> str:
    return os.environ.get("REACHER_GITHUB_OWNER", "").strip() or _DEFAULT_OWNER


def github_configured() -> bool:
    return bool(github_token())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {github_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }


async def create_issue(
    client: httpx.AsyncClient,
    *,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    summarized: bool,
) -> str:
    """Create an issue and return its ``html_url``.

    Adds ``develop``.  Successful summaries also get ``agent-ready``; fallback
    filings get ``needs-triage`` instead so agents do not consume garbage.
    """
    if repo not in ALLOWED_REPOS:
        raise HTTPException(status_code=400, detail=f"Unsupported repository: {repo}")
    token = github_token()
    if not token:
        raise HTTPException(status_code=503, detail="GitHub filing is not configured")

    extra = [DEVELOP_LABEL, AGENT_READY_LABEL if summarized else FALLBACK_LABEL]
    all_labels = list(dict.fromkeys([*labels, *extra]))
    full_repo = f"{github_owner()}/{repo}"

    await _ensure_labels(client, full_repo, all_labels)

    # GitHub issue bodies are capped at 65536 characters.
    if len(body) > 65000:
        body = body[:64980] + "\n\n… [truncated]\n"

    try:
        res = await client.post(
            f"https://api.github.com/repos/{full_repo}/issues",
            headers=_headers(),
            json={"title": title, "body": body, "labels": all_labels},
            timeout=20.0,
        )
        res.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("GitHub issue create failed: %s", exc)
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"GitHub rejected the issue: {detail}") from exc
    except Exception as exc:
        logger.warning("GitHub unreachable: %s", exc)
        raise HTTPException(status_code=503, detail=f"GitHub unreachable: {exc}") from exc

    url = res.json().get("html_url")
    if not url:
        raise HTTPException(status_code=502, detail="GitHub response missing html_url")
    return url


async def _ensure_labels(client: httpx.AsyncClient, full_repo: str, labels: list[str]) -> None:
    existing: set[str] = set()
    try:
        page = 1
        while True:
            res = await client.get(
                f"https://api.github.com/repos/{full_repo}/labels",
                headers=_headers(),
                params={"per_page": 100, "page": page},
                timeout=15.0,
            )
            res.raise_for_status()
            batch = res.json()
            if not batch:
                break
            existing.update(str(item.get("name", "")).lower() for item in batch)
            page += 1
    except Exception as exc:
        logger.warning("Could not list GitHub labels (will try create anyway): %s", exc)

    for label in labels:
        if label.lower() in existing:
            continue
        try:
            await client.post(
                f"https://api.github.com/repos/{full_repo}/labels",
                headers=_headers(),
                json={"name": label, "color": _LABEL_COLORS.get(label, "ededed")},
                timeout=10.0,
            )
        except Exception as exc:
            logger.warning("Could not create label %s: %s", label, exc)
