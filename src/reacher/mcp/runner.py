"""Allowlisted command execution, with an honest verdict.

The single design rule here: **an exit code of zero is not a pass.**

Both repos in this workspace have a way to report success while verifying
nothing. labrynth's ``npm run lint`` exits 0 through a pipe despite ESLint
finding no flat config. This repo's firmware parity tests skip silently when the
firmware tree is absent, so a wheel-only run is all-green having checked nothing.
An agent that reads only the return code will bank both.

So every result carries ``ran``, ``trustworthy`` and ``verdict`` *separately*
from ``exit_code``, and ``verdict`` can only be ``pass`` when the tool actually
ran and its result means something.

Commands are selected by key from a literal table. The caller never composes a
command line, ``shell=False`` always, cwd is pinned to a discovered repo root,
and the environment goes through ``clean_child_env`` — the frozen-bundle
``LD_LIBRARY_PATH`` leak that broke ``/bin/bash`` on Arch would hit ``arduino-cli``
and ``npm`` in exactly the same way.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..child_env import clean_child_env
from .workspace import Workspace

#: Per-stream output cap. Enough to diagnose, small enough not to flood context.
OUTPUT_LIMIT = 8_000
DEFAULT_TIMEOUT_S = 600


@dataclass(frozen=True)
class Command:
    """One allowlisted command."""

    key: str
    argv: tuple[str, ...]
    repo: str
    #: Path relative to the repo root to run in (labrynth's tooling lives in web/).
    subdir: str = ""
    description: str = ""
    #: Whether a zero exit means the thing was actually verified.
    meaningful: bool = True
    #: Append the checkout's own version to argv. bump-version.py --check takes
    #: the version to verify against, and CI passes the bare git tag; here the
    #: equivalent is what the tree currently declares.
    needs_version: bool = False


#: The complete set of commands this server may run. Deliberately absent:
#:
#: * ``bash firmware/compile.sh`` — writes the committed hex artifacts. Regenerating
#:   tracked build output stays a deliberate human action.
#:
#: ``lint`` is present but gated: it only runs when labrynth has a flat ESLint
#: config, because without one ESLint 9 finds nothing and still exits 0 through a
#: pipe. See ``available()``.
COMMANDS: dict[str, Command] = {
    "pytest": Command(
        "pytest", ("python", "-m", "pytest", "-q"), repo="reacher",
        description="Full backend test suite, including every parity check.",
    ),
    "pytest_parity": Command(
        "pytest_parity",
        ("python", "-m", "pytest", "-q",
         "tests/test_command_parity.py", "tests/test_firmware_parity.py",
         "tests/test_frontend_parity.py", "tests/test_device_names.py"),
        repo="reacher",
        description="Only the cross-layer parity checks.",
    ),
    "ruff": Command(
        "ruff", ("python", "-m", "ruff", "check", "."), repo="reacher",
        description="Backend lint. A required CI gate.",
    ),
    "tsc": Command(
        "tsc", ("npx", "tsc", "-b"), repo="labrynth", subdir="web",
        description="Frontend typecheck. The only working automated gate in labrynth.",
    ),
    "lint": Command(
        "lint", ("npm", "run", "lint"), repo="labrynth", subdir="web",
        description="Frontend lint. Reports warnings separately from errors.",
    ),
    "check_tables": Command(
        "check_tables", ("npx", "tsx", "scripts/check-tables.ts", "--json"),
        repo="labrynth",
        description="labrynth's internal cross-table consistency rules.",
    ),
    "version_check": Command(
        "version_check", ("python", "scripts/bump-version.py", "--check"), repo="reacher",
        description="Version coherence across pyproject, __init__, and the firmware strings.",
        needs_version=True,
    ),
}

#: pytest reports "N passed, M skipped". A green run with skips is not a clean
#: bill of health here — the parity tests skip precisely when a tree is missing.
_PYTEST_TALLY_RE = re.compile(r"(\d+) passed(?:, (\d+) skipped)?")

#: ESLint reports "N problems (E errors, W warnings)". Exit 0 with a large
#: warning count is a backlog, not a clean bill of health, and saying only
#: "pass" would hide it — the same hazard as a skipped test, one level up.
_ESLINT_TALLY_RE = re.compile(r"(\d+) problems? \((\d+) errors?, (\d+) warnings?\)")


def _has_eslint_config(web: Path) -> bool:
    return any(
        (web / name).is_file()
        for name in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
                     "eslint.config.ts")
    )


def available(workspace: Workspace) -> dict[str, dict[str, Any]]:
    """Report which commands can run, and why not when they cannot."""
    out = {}
    for key, cmd in COMMANDS.items():
        repo = getattr(workspace, cmd.repo)
        reason = None
        if not repo.present:
            reason = f"no {cmd.repo} checkout"
        else:
            tool = cmd.argv[0]
            if tool != "python" and workspace.tools.get(tool) is None:
                reason = f"{tool} not on PATH"
            elif cmd.key == "check_tables" and not (repo.path / "scripts" / "check-tables.ts").is_file():
                reason = "scripts/check-tables.ts does not exist yet"
            elif cmd.key == "lint" and not _has_eslint_config(repo.path / "web"):
                reason = (
                    "no flat eslint.config.* — ESLint 9 finds nothing to run and still "
                    "exits 0, so a result here would be a guaranteed false green"
                )
        out[key] = {
            "description": cmd.description,
            "argv": list(cmd.argv),
            "runnable": reason is None,
            "reason": reason,
        }
    return out


def run_command(
    workspace: Workspace, key: str, *, timeout_s: int = DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """Run one allowlisted command and report an honest verdict."""
    cmd = COMMANDS.get(key)
    if cmd is None:
        return {
            "check": key, "ran": False, "trustworthy": False, "verdict": "UNAVAILABLE",
            "reason": f"{key!r} is not an allowlisted command. Available: {sorted(COMMANDS)}",
        }

    status = available(workspace)[key]
    if not status["runnable"]:
        return {
            "check": key, "ran": False, "trustworthy": False, "verdict": "UNAVAILABLE",
            "argv": list(cmd.argv), "reason": status["reason"],
        }

    repo_path: Path = getattr(workspace, cmd.repo).path
    cwd = repo_path / cmd.subdir if cmd.subdir else repo_path
    argv = list(cmd.argv)
    if argv[0] == "python":
        import sys
        argv[0] = sys.executable
    if cmd.needs_version:
        version = _declared_version(repo_path)
        if version is None:
            return {
                "check": key, "ran": False, "trustworthy": False, "verdict": "UNAVAILABLE",
                "argv": argv, "reason": "could not read the version from src/reacher/__init__.py",
            }
        argv.append(version)

    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), env=clean_child_env(), capture_output=True,
            text=True, timeout=timeout_s, check=False, shell=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "check": key, "ran": True, "trustworthy": False, "verdict": "UNAVAILABLE",
            "argv": argv, "cwd": str(cwd),
            "reason": f"timed out after {timeout_s}s — result unknown, not passing",
        }
    except OSError as exc:
        return {
            "check": key, "ran": False, "trustworthy": False, "verdict": "UNAVAILABLE",
            "argv": argv, "cwd": str(cwd), "reason": f"could not execute: {exc}",
        }

    result = {
        "check": key,
        "argv": argv,
        "cwd": str(cwd),
        "ran": True,
        "trustworthy": cmd.meaningful,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-OUTPUT_LIMIT:],
        "stderr_tail": proc.stderr[-OUTPUT_LIMIT:],
    }
    result.update(_verdict(cmd, proc.returncode, proc.stdout))
    return result


def _verdict(cmd: Command, exit_code: int, stdout: str) -> dict[str, Any]:
    """Translate an exit code into a verdict that cannot overstate coverage."""
    if not cmd.meaningful:
        return {"verdict": "UNAVAILABLE", "reason": "this command's exit status means nothing"}
    if exit_code != 0:
        return {"verdict": "fail", "summary": _summarize(stdout) or f"exit {exit_code}"}

    lint_match = _ESLINT_TALLY_RE.search(stdout)
    if lint_match:
        warnings = int(lint_match.group(3))
        if warnings:
            return {
                "verdict": "pass_with_warnings",
                "summary": f"0 blocking errors, but {warnings} warnings outstanding",
                "warnings": warnings,
                "reason": (
                    "Warnings are findings the config chose not to block on, not findings "
                    "that were reviewed and accepted. Do not report the lint as clean."
                ),
            }

    match = _PYTEST_TALLY_RE.search(stdout)
    if match and match.group(2):
        skipped = int(match.group(2))
        return {
            "verdict": "pass_with_skips",
            "summary": f"{match.group(1)} passed but {skipped} SKIPPED — those verified nothing",
            "skipped": skipped,
            "reason": (
                "Tests skip when a tree is absent (firmware/, or the labrynth checkout). "
                "Treat the skipped ones as unverified, not as passing."
            ),
        }
    return {"verdict": "pass", "summary": _summarize(stdout) or "exit 0"}


def _declared_version(repo_path: Path) -> Optional[str]:
    """Read __version__ from the checkout without importing it."""
    init = repo_path / "src" / "reacher" / "__init__.py"
    try:
        text = init.read_text()
    except OSError:
        return None
    match = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text)
    return match.group(1) if match else None


def _summarize(stdout: str) -> Optional[str]:
    for line in reversed(stdout.strip().splitlines()):
        if line.strip():
            return line.strip()[:300]
    return None
