"""In-app issue reporting.

``POST /api/issues/prefill`` composes a title/body from the user's report plus
a capped, redacted diagnostic excerpt, and returns a pre-filled GitHub
"New Issue" link. The user reviews and submits it themselves, in their own
browser, under their own GitHub account — no token, no relay, no LLM, and no
network or subprocess call happens on this path.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ... import __version__, diagnostics
from ...issues.prefill import ALLOWED_LABELS, build_prefill

logger = logging.getLogger(__name__)

router = APIRouter()

#: Sized for a short-form report, not a pasted log — the diagnostic excerpt
#: carries the bulk of the detail, and the URL budget can't absorb more.
_MAX_DESCRIPTION = 2000
_MAX_STEPS = 1500
_MAX_LABELS = 3


class PrefillRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=_MAX_DESCRIPTION)
    steps: str = Field("", max_length=_MAX_STEPS)
    severity: Literal["minor", "moderate", "critical", ""] = ""
    repo: Literal["labrynth", "reacher"] = "labrynth"
    app_version: str = Field("", max_length=40)
    labels: list[str] = Field(default_factory=list)


class PrefillResponse(BaseModel):
    title: str
    body: str
    labels: list[str]
    url: str
    repo: str
    owner: str


@router.post("/prefill", response_model=PrefillResponse)
async def prefill_issue(body: PrefillRequest) -> PrefillResponse:
    versions = f"reacher {__version__}"
    if body.app_version:
        versions = f"Labrynth {body.app_version}; {versions}"

    labels = [lab for lab in dict.fromkeys(body.labels) if lab in ALLOWED_LABELS][:_MAX_LABELS]

    result = build_prefill(
        repo=body.repo,
        description=body.description,
        steps=body.steps,
        severity=body.severity,
        versions=versions,
        labels=labels,
    )

    diagnostics.log(
        "issues.prefill",
        src="reacher.api.issues",
        repo=body.repo,
        title=result["title"],
    )

    return PrefillResponse(**result)
