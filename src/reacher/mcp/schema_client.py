"""Fetch ground truth from a reacher checkout, in a subprocess, per call.

This module exists because of one silent failure mode. labrynth depends on a
pinned ``reacher2p`` wheel, so a user customizing REACHER very likely has both
an installed package *and* a source checkout. If this server imported
``reacher.kernel.commands`` directly, it would read whichever copy won the
import — usually the wheel — while the user's agent edited the checkout. Every
answer would be confidently stale and every check would pass against the wrong
tree, with nothing anywhere reporting a problem.

So ground truth is never imported. It is fetched by running
``reacher.schema`` as a subprocess with ``PYTHONPATH`` pointed at the target
checkout's ``src/``, which precedes site-packages in ``sys.path``.

Running it per call rather than caching is deliberate: an agent edits between
calls, and a consistency check that answers from a snapshot taken before those
edits is worse than no check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from ..child_env import clean_child_env
from ..schema import SCHEMA_VERSION

#: Versions of the schema document this server understands. A dump outside the
#: range is refused rather than misread — a partially-understood document is how
#: a checker reports agreement it never verified.
SUPPORTED_SCHEMA_VERSIONS = range(1, SCHEMA_VERSION + 1)

DEFAULT_TIMEOUT_S = 60


class SchemaUnavailable(RuntimeError):
    """Ground truth could not be obtained. Never treat this as 'no drift'."""


def fetch(
    reacher_root: Path,
    *,
    python: Optional[str] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run ``reacher.schema dump`` against *reacher_root* and return the document.

    Raises SchemaUnavailable on any failure — a missing checkout, a syntax error
    mid-edit, a timeout, or an unsupported schema version. Callers must surface
    that as UNAVAILABLE, never as a passing check.
    """
    src = reacher_root / "src"
    if not (src / "reacher" / "schema.py").is_file():
        raise SchemaUnavailable(
            f"{reacher_root} does not look like a reacher checkout "
            f"(no src/reacher/schema.py) — cannot obtain ground truth"
        )

    env = clean_child_env()
    # Prepend, never replace: the checkout must win over site-packages, but a
    # caller's own PYTHONPATH may carry something the interpreter needs.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(src), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )

    try:
        proc = subprocess.run(
            [python or sys.executable, "-m", "reacher.schema", "dump", "--json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(reacher_root),
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SchemaUnavailable(f"schema dump timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise SchemaUnavailable(f"could not run the schema dump: {exc}") from exc

    if not proc.stdout.strip():
        raise SchemaUnavailable(
            "schema dump produced no output"
            + (f" (stderr: {proc.stderr.strip()[:400]})" if proc.stderr.strip() else "")
        )

    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SchemaUnavailable(
            f"schema dump emitted invalid JSON: {exc}. "
            f"stderr: {proc.stderr.strip()[:400] or '(empty)'}"
        ) from exc

    version = doc.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SchemaUnavailable(
            f"schema_version {version!r} is outside the supported range "
            f"{SUPPORTED_SCHEMA_VERSIONS.start}..{SUPPORTED_SCHEMA_VERSIONS.stop - 1}. "
            "Refusing to read a document this server may misinterpret."
        )

    # A non-zero exit means a *section* failed to build. The document is still
    # structurally valid and its errors list says what is missing, so return it
    # and let the caller degrade per-section rather than losing everything.
    if proc.returncode != 0 and not doc.get("errors"):
        raise SchemaUnavailable(
            f"schema dump exited {proc.returncode} without reporting an error: "
            f"{proc.stderr.strip()[:400] or '(no stderr)'}"
        )
    return doc


def firmware_available(doc: dict[str, Any]) -> bool:
    """Whether firmware-dependent checks can run against this document."""
    return bool(doc.get("firmware", {}).get("present"))
