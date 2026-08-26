"""Translate a plain-language bug report + log excerpt via bundled llama.cpp.

The binary and GGUF are *not* a pip dependency.  Labrynth's launcher sets
``REACHER_LLM_BIN`` and ``REACHER_LLM_MODEL`` when they were frozen into the
installer.  If either is missing this module refuses to call the network —
there is no cloud fallback.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

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

FALLBACK_LABEL = "needs-triage"
AGENT_READY_LABEL = "agent-ready"
DEVELOP_LABEL = "develop"

_LLM_TIMEOUT_S = 120
_THREADS = 2
_N_PREDICT = 1024

_SYSTEM_PROMPT = f"""You are a technical issue translator for a neuroscience lab software suite called REACHER / Labrynth.
Researchers who are not software developers will describe bugs in plain language.
Your job is to translate their descriptions, plus a diagnostic log excerpt, into a well-structured GitHub issue a developer can act on immediately.

You must return ONLY a valid JSON object (no markdown, no commentary) with the following schema:
{{
  "title": "Short, imperative, specific title (max 72 chars)",
  "body": "Full GitHub issue body in Markdown",
  "labels": ["label1", "label2"]
}}

For the body, use this exact structure:
## Description
[Clear technical summary of the problem]

## Steps to Reproduce
[Numbered list — infer reasonable steps from the description if not explicitly provided; write "Unknown — needs investigation" if truly cannot be inferred]

## Expected Behavior
[What should have happened]

## Actual Behavior
[What actually happened]

## Severity
[minor | moderate | critical] — [one-sentence impact statement]

## Reporter Notes
[Preserve any useful verbatim details from the original description that don't fit elsewhere]

## Diagnostic excerpt
[Quote the most relevant log lines; do not invent records that are not in the excerpt]

For labels, choose 1–3 from this list only: {json.dumps(ALLOWED_LABELS)}
Choose based on what component or area the bug most likely affects.
Always include "bug" unless the report is clearly a feature request or question.

The title should be imperative and specific: "Camera feed freezes on session restart" not "Bug with camera"."""


def llm_available() -> bool:
    """True when a bundled (or operator-supplied) llama-cli + GGUF are on disk."""
    bin_path = os.environ.get("REACHER_LLM_BIN", "").strip()
    model = os.environ.get("REACHER_LLM_MODEL", "").strip()
    return bool(bin_path and model and os.path.isfile(bin_path) and os.path.isfile(model))


def summarize_report(
    *,
    repo: str,
    description: str,
    excerpt: str,
    steps: str = "",
    severity: str = "",
    versions: str = "",
) -> dict[str, Any]:
    """Run llama-cli and return ``{title, body, labels, summarized}``.

    On any inference or parse failure, returns a fallback issue (``summarized``
    is False) rather than raising — the caller can still file it.
    """
    try:
        raw = _run_llama(_user_content(repo, description, excerpt, steps, severity))
        parsed = _parse_model_json(raw)
        return _sanitize(parsed, excerpt=excerpt, versions=versions, summarized=True)
    except Exception as exc:
        logger.warning("Local summarizer failed; filing fallback: %s", exc)
        return _fallback(description, excerpt, steps, severity, versions)


def _user_content(repo: str, description: str, excerpt: str, steps: str, severity: str) -> str:
    parts = [
        f"Repository: {repo}",
        f"Severity: {severity or 'unspecified'}",
        "",
        "User description:",
        description.strip(),
        "",
    ]
    if steps.strip():
        parts.extend(["Reproduction steps provided by user:", steps.strip(), ""])
    else:
        parts.append("No reproduction steps provided.")
        parts.append("")
    parts.extend(
        [
            "Diagnostic excerpt from the current application run:",
            excerpt.strip() or "(empty)",
        ]
    )
    return "\n".join(parts)


def _run_llama(user_content: str) -> str:
    bin_path = os.environ["REACHER_LLM_BIN"]
    model = os.environ["REACHER_LLM_MODEL"]
    prompt = (
        "<|im_start|>system\n"
        f"{_SYSTEM_PROMPT}<|im_end|>\n"
        "<|im_start|>user\n"
        f"{user_content}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(prompt)
        prompt_path = fh.name
    try:
        cmd = [
            bin_path,
            "-m",
            model,
            "-f",
            prompt_path,
            "-n",
            str(_N_PREDICT),
            "--temp",
            "0.1",
            "-t",
            str(_THREADS),
            "-ngl",
            "0",
            "--no-display-prompt",
            "--no-conversation",
            "--simple-io",
        ]
        env = os.environ.copy()
        env["LLAMA_LOG_LEVEL"] = "ERROR"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_LLM_TIMEOUT_S,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "")[-500:]
            raise RuntimeError(f"llama-cli exited {result.returncode}: {stderr}")
        return result.stdout or ""
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


def _parse_model_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if "```" in text:
        inner = text.split("```", 2)[1]
        if inner.lstrip().startswith("json"):
            inner = inner.lstrip()[4:]
        text = inner.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output contained no JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model JSON was not an object")
    return parsed


def _sanitize(
    parsed: dict[str, Any],
    *,
    excerpt: str,
    versions: str,
    summarized: bool,
) -> dict[str, Any]:
    title = str(parsed.get("title") or "Untitled issue").strip()[:72]
    body = str(parsed.get("body") or "").strip()
    labels = [lab for lab in parsed.get("labels", []) if lab in ALLOWED_LABELS]
    if not labels:
        labels = ["bug"]
    if "## Diagnostic excerpt" not in body and excerpt:
        body = body.rstrip() + "\n\n## Diagnostic excerpt\n```\n" + excerpt + "\n```"
    body = _append_footer(body, versions)
    return {"title": title, "body": body, "labels": labels, "summarized": summarized}


def _fallback(
    description: str,
    excerpt: str,
    steps: str,
    severity: str,
    versions: str,
) -> dict[str, Any]:
    steps_block = steps.strip() or "Unknown — needs investigation"
    sev = severity.strip() or "unspecified"
    body = "\n".join(
        [
            "## Description",
            description.strip() or "(no description provided)",
            "",
            "## Steps to Reproduce",
            steps_block,
            "",
            "## Expected Behavior",
            "Unknown — needs investigation",
            "",
            "## Actual Behavior",
            "See description.",
            "",
            "## Severity",
            sev,
            "",
            "## Reporter Notes",
            "This issue was filed without a local-model summary (inference failed or produced invalid JSON).",
            "",
            "## Diagnostic excerpt",
            "```",
            excerpt or "(empty)",
            "```",
        ]
    )
    body = _append_footer(body, versions)
    title = (description.strip().splitlines() or ["Untitled issue"])[0][:72]
    return {
        "title": title or "Untitled issue",
        "body": body,
        "labels": ["bug", FALLBACK_LABEL],
        "summarized": False,
    }


def _append_footer(body: str, versions: str) -> str:
    footer = "\n\n---\n*Auto-generated from an in-app Labrynth report. Assigned to `develop` branch for triage.*"
    if versions:
        footer += f"\n*{versions}*"
    if footer.strip() in body:
        return body
    return body.rstrip() + footer
