"""In-app issue reporting: local llama.cpp summarizer + GitHub filing."""

from .github import ALLOWED_REPOS, create_issue, github_configured, github_owner
from .summarize import (
    ALLOWED_LABELS,
    FALLBACK_LABEL,
    LlmStatus,
    llm_available,
    llm_probe,
    reset_probe_cache,
    summarize_report,
)

__all__ = [
    "ALLOWED_LABELS",
    "ALLOWED_REPOS",
    "FALLBACK_LABEL",
    "LlmStatus",
    "create_issue",
    "github_configured",
    "github_owner",
    "llm_available",
    "llm_probe",
    "reset_probe_cache",
    "summarize_report",
]
