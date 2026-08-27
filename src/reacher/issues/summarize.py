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
from dataclasses import dataclass
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
_PROBE_TIMEOUT_S = 60
# llama.cpp exits non-zero on an empty prompt ("input is empty"), so the probe
# needs a token to chew on even though it generates nothing.
_PROBE_PROMPT = "x"
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


@dataclass(frozen=True)
class LlmStatus:
    """Outcome of the summarizer liveness probe."""

    ok: bool
    detail: str = ""


def _llama_argv(
    bin_path: str,
    model: str,
    *,
    prompt_file: str | None = None,
    prompt: str = "",
    n_predict: int,
) -> list[str]:
    """Build the llama.cpp argv.

    The probe and the real call share this so the probe cannot drift from what
    actually runs — a flag the bundled binary rejects has to fail both or
    neither.  ``llama-cli`` dropped ``--no-conversation`` in b10622 (raw
    completion moved to ``llama-completion``), and that shipped undetected
    precisely because nothing validated the argv.
    """
    argv = [bin_path, "-m", model]
    argv += ["-f", prompt_file] if prompt_file else ["-p", prompt]
    argv += [
        "-n",
        str(n_predict),
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
    return argv


def _llama_env() -> dict[str, str]:
    env = os.environ.copy()
    env["LLAMA_LOG_LEVEL"] = "ERROR"
    return env


def _probe(bin_path: str, model: str) -> LlmStatus:
    """Run the real argv with ``-n 0`` — loads the model, generates nothing.

    ``--version`` is not enough: it exits 0 on a binary that rejects the flags
    the summarizer sends.  llama.cpp parses argv before touching the model, so
    a zero-token run exercises the whole path (shared libraries, arguments,
    model load) in well under a second.
    """
    if not bin_path or not model:
        return LlmStatus(False, "REACHER_LLM_BIN / REACHER_LLM_MODEL are not set")
    if not os.path.isfile(bin_path):
        return LlmStatus(False, f"llama.cpp binary not found: {bin_path}")
    if not os.path.isfile(model):
        return LlmStatus(False, f"GGUF model not found: {model}")

    name = os.path.basename(bin_path)
    try:
        result = subprocess.run(
            _llama_argv(bin_path, model, prompt=_PROBE_PROMPT, n_predict=0),
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            env=_llama_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return LlmStatus(False, f"{name} timed out after {_PROBE_TIMEOUT_S}s")
    except OSError as exc:
        return LlmStatus(False, f"{name} could not be executed: {exc}")
    if result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip()
        return LlmStatus(False, f"{name} exited {result.returncode}: {detail[-300:]}")
    return LlmStatus(True, "")


_probe_cache: tuple[tuple, LlmStatus] | None = None


def _cache_key(bin_path: str, model: str) -> tuple:
    def stamp(path: str):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    return (bin_path, model, stamp(bin_path), stamp(model))


def llm_probe() -> LlmStatus:
    """Cached liveness probe.  Re-runs when the configured paths or files change."""
    global _probe_cache
    bin_path = os.environ.get("REACHER_LLM_BIN", "").strip()
    model = os.environ.get("REACHER_LLM_MODEL", "").strip()
    key = _cache_key(bin_path, model)
    if _probe_cache is not None and _probe_cache[0] == key:
        return _probe_cache[1]
    status = _probe(bin_path, model)
    if not status.ok:
        logger.warning("Local summarizer unavailable: %s", status.detail)
    _probe_cache = (key, status)
    return status


def reset_probe_cache() -> None:
    """Drop the cached probe result (tests)."""
    global _probe_cache
    _probe_cache = None


def llm_available() -> bool:
    """True when the bundled llama.cpp binary + GGUF are present *and* runnable."""
    return llm_probe().ok


def summarize_report(
    *,
    repo: str,
    description: str,
    excerpt: str,
    steps: str = "",
    severity: str = "",
    versions: str = "",
) -> dict[str, Any]:
    """Run the summarizer and return ``{title, body, labels, summarized, error}``.

    On any inference or parse failure, returns a fallback issue (``summarized``
    is False, ``error`` explains why) rather than raising — the caller can
    still file it.
    """
    try:
        raw = _run_llama(_user_content(repo, description, excerpt, steps, severity))
        parsed = _parse_model_json(raw)
        return _sanitize(parsed, excerpt=excerpt, versions=versions, summarized=True)
    except Exception as exc:
        # ERROR, not WARNING: a silent drop to the fallback draft is how a
        # broken bundled model reached users looking like a working one.
        logger.error("Local summarizer failed; filing fallback: %s", exc)
        reset_probe_cache()
        return _fallback(description, excerpt, steps, severity, versions, error=str(exc))


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
        result = subprocess.run(
            _llama_argv(bin_path, model, prompt_file=prompt_path, n_predict=_N_PREDICT),
            capture_output=True,
            text=True,
            timeout=_LLM_TIMEOUT_S,
            env=_llama_env(),
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "")[-500:]
            raise RuntimeError(f"{os.path.basename(bin_path)} exited {result.returncode}: {stderr}")
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
    return {"title": title, "body": body, "labels": labels, "summarized": summarized, "error": None}


def _fallback(
    description: str,
    excerpt: str,
    steps: str,
    severity: str,
    versions: str,
    error: str = "",
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
        "error": error or None,
    }


def _append_footer(body: str, versions: str) -> str:
    footer = "\n\n---\n*Auto-generated from an in-app Labrynth report. Assigned to `develop` branch for triage.*"
    if versions:
        footer += f"\n*{versions}*"
    if footer.strip() in body:
        return body
    return body.rstrip() + footer
