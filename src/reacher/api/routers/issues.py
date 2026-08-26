"""In-app issue reporting.

``GET  /api/issues/status`` — whether the local GGUF and GitHub token are present.
``POST /api/issues/report`` — summarize via llama-cli and optionally file on GitHub.

There is no cloud LLM fallback.  If ``REACHER_LLM_BIN`` / ``REACHER_LLM_MODEL``
are unset the report endpoint returns 503.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ... import __version__, diagnostics
from ...diagnostics.excerpt import build_current_excerpt
from ...issues.github import ALLOWED_REPOS, create_issue, github_configured, github_owner
from ...issues.summarize import llm_available, summarize_report

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_DESCRIPTION = 8000
_MAX_STEPS = 4000


class ReportRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=_MAX_DESCRIPTION)
    steps: str = Field("", max_length=_MAX_STEPS)
    severity: Literal["minor", "moderate", "critical", ""] = ""
    repo: Literal["labrynth", "reacher"] = "labrynth"
    app_version: str = Field("", max_length=40)
    file: bool = True


class ReportResponse(BaseModel):
    title: str
    body: str
    labels: list[str]
    summarized: bool
    filed: bool
    html_url: Optional[str] = None
    repo: str


@router.get("/status")
async def issue_status() -> dict:
    """Flags the UI uses to enable/disable Submit-to-GitHub vs summarize-only."""
    return {
        "llm": llm_available(),
        "github": github_configured(),
        "owner": github_owner(),
        "repos": list(ALLOWED_REPOS),
    }


@router.post("/report", response_model=ReportResponse)
async def report_issue(body: ReportRequest, request: Request) -> ReportResponse:
    """Summarize a user report against the current run log and optionally file it."""
    if not llm_available():
        raise HTTPException(
            status_code=503,
            detail="Local summarizer is not available (REACHER_LLM_BIN / REACHER_LLM_MODEL).",
        )

    excerpt = build_current_excerpt()
    versions = f"reacher {__version__}"
    if body.app_version:
        versions = f"Labrynth {body.app_version}; {versions}"

    summarized = await asyncio.to_thread(
        summarize_report,
        repo=body.repo,
        description=body.description,
        excerpt=excerpt,
        steps=body.steps,
        severity=body.severity,
        versions=versions,
    )

    html_url: Optional[str] = None
    filed = False
    should_file = body.file and github_configured()
    if should_file:
        client: httpx.AsyncClient = request.app.state.http_client
        html_url = await create_issue(
            client,
            repo=body.repo,
            title=summarized["title"],
            body=summarized["body"],
            labels=summarized["labels"],
            summarized=bool(summarized["summarized"]),
        )
        filed = True

    diagnostics.log(
        "issues.report",
        src="reacher.api.issues",
        repo=body.repo,
        filed=filed,
        summarized=bool(summarized["summarized"]),
        title=summarized["title"],
    )

    return ReportResponse(
        title=summarized["title"],
        body=summarized["body"],
        labels=summarized["labels"],
        summarized=bool(summarized["summarized"]),
        filed=filed,
        html_url=html_url,
        repo=body.repo,
    )
