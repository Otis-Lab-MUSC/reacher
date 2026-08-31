"""Discovery of the repositories a coordinated change spans.

A REACHER customization touches two checkouts — this one (kernel + firmware) and
labrynth (frontend) — plus whatever tools happen to be installed on the machine.
Neither the repos nor the tools can be assumed present, and every absence has to
degrade *loudly*: the single most dangerous outcome for a non-expert is a report
that reads as green because a check quietly did not run.

Two live traps this module exists to avoid:

* An archived ``reacher-firmware`` sibling sitting next to the real checkout. It
  contains ``Commands.h`` and looks convincing. Discovery keys on files only the
  real repos have.
* labrynth's ``npm run lint``, which exits 0 through a pipe despite ESLint
  finding no flat config. Any consumer must be told that up front, or an agent
  will report "lint passed" off a no-op.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

#: Env var pointing at a workspace root that contains the checkouts.
WORKSPACE_ENV = "REACHER_WORKSPACE"

#: Optional discovery-override file. Discovery only — it carries no knowledge
#: about the repos, deliberately: a file describing which constants mirror which
#: would be one more hand-maintained mirror, with nothing checking it.
WORKSPACE_FILE = ".reacher-workspace.toml"

#: Files that identify each repo. Chosen so nothing else can impersonate them:
#: the archived reacher-firmware repo has Commands.h but no src/reacher/.
_REACHER_MARKER = Path("src") / "reacher" / "kernel" / "commands.py"
_LABRYNTH_MARKER = Path("web") / "src" / "components" / "hardware" / "pinMeta.ts"

#: Directory names never treated as a repo, however convincing their contents.
_NEVER_A_REPO = frozenset({"reacher-firmware"})


@dataclass
class RepoInfo:
    """One discovered checkout."""

    name: str
    path: Optional[Path]
    present: bool
    git_branch: Optional[str] = None
    git_sha: Optional[str] = None
    dirty: Optional[bool] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path) if self.path else None,
            "present": self.present,
            "git_branch": self.git_branch,
            "git_sha": self.git_sha,
            "dirty": self.dirty,
        }


@dataclass
class Workspace:
    """The repos and tooling available for a coordinated change."""

    root: Optional[Path]
    reacher: RepoInfo
    labrynth: RepoInfo
    tools: dict[str, Optional[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root) if self.root else None,
            "repos": {"reacher": self.reacher.as_dict(), "labrynth": self.labrynth.as_dict()},
            "tools": self.tools,
            "tooling": tooling_reality(self),
            "warnings": self.warnings,
        }


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _repo_info(name: str, path: Optional[Path]) -> RepoInfo:
    if path is None:
        return RepoInfo(name=name, path=None, present=False)
    return RepoInfo(
        name=name,
        path=path,
        present=True,
        git_branch=_git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        git_sha=_git(path, "rev-parse", "HEAD"),
        dirty=bool(_git(path, "status", "--porcelain")),
    )


def _looks_like(path: Path, marker: Path) -> bool:
    try:
        return path.name not in _NEVER_A_REPO and (path / marker).is_file()
    except OSError:
        return False


def _read_override(root: Path) -> dict[str, Path]:
    """Read the optional discovery-override file, if present and parseable."""
    config = root / WORKSPACE_FILE
    if not config.is_file():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return {}
    try:
        data = tomllib.loads(config.read_text())
    except Exception:
        return {}
    repos = data.get("repos", {})
    return {
        name: (root / value).resolve()
        for name, value in repos.items()
        if isinstance(value, str)
    }


def _find(start: Path, marker: Path) -> Optional[Path]:
    """Find a checkout at or under *start* by its identifying marker."""
    if _looks_like(start, marker):
        return start
    try:
        children = sorted(p for p in start.iterdir() if p.is_dir())
    except OSError:
        # A workspace path that does not exist or cannot be read is a missing
        # repo, not a crash — the caller gets a warning and UNAVAILABLE checks.
        return None
    for child in children:
        if _looks_like(child, marker):
            return child
    return None


def discover(explicit: Optional[Path] = None) -> Workspace:
    """Locate the reacher and labrynth checkouts and the tools available.

    Resolution order: *explicit* argument, ``$REACHER_WORKSPACE``, then walking
    up from this file until a directory contains a reacher checkout. A user with
    the conventional sibling layout configures nothing.
    """
    root = explicit or (Path(os.environ[WORKSPACE_ENV]) if os.environ.get(WORKSPACE_ENV) else None)
    warnings: list[str] = []

    if root is None:
        here = Path(__file__).resolve()
        for candidate in here.parents:
            if _looks_like(candidate, _REACHER_MARKER):
                root = candidate.parent
                break

    reacher_path = labrynth_path = None
    if root is not None:
        root = root.resolve()
        override = _read_override(root)
        reacher_path = override.get("reacher") or _find(root, _REACHER_MARKER)
        labrynth_path = override.get("labrynth") or _find(root, _LABRYNTH_MARKER)
        for name, path in (("reacher", reacher_path), ("labrynth", labrynth_path)):
            if path is not None and not _looks_like(path, _REACHER_MARKER if name == "reacher" else _LABRYNTH_MARKER):
                warnings.append(f"{WORKSPACE_FILE} points {name} at {path}, which does not look like that repo")
                if name == "reacher":
                    reacher_path = None
                else:
                    labrynth_path = None

    reacher = _repo_info("reacher", reacher_path)
    labrynth = _repo_info("labrynth", labrynth_path)

    if not reacher.present:
        warnings.append(
            "no reacher checkout found — every firmware and kernel check is UNAVAILABLE, "
            f"not passing. Set ${WORKSPACE_ENV} to the directory containing the checkouts."
        )
    if not labrynth.present:
        warnings.append(
            "no labrynth checkout found — every frontend check is UNAVAILABLE, not passing. "
            "A change planned without it is incomplete."
        )
    if reacher.present and not (reacher.path / "firmware").is_dir():
        warnings.append(
            "reacher checkout has no firmware/ tree (installed-wheel layout) — firmware "
            "checks are UNAVAILABLE, not passing."
        )
    for repo in (reacher, labrynth):
        if repo.present and repo.dirty:
            warnings.append(f"{repo.name} has uncommitted changes — checks read the working tree, not HEAD")

    tools = {
        name: shutil.which(name)
        for name in ("git", "arduino-cli", "npm", "npx", "node", "pytest", "ruff")
    }
    if tools["arduino-cli"] is None:
        warnings.append(
            "arduino-cli not on PATH — the UNO flash-headroom guard cannot run. "
            "_lite builds sit at 91-94% of 32256 B, so firmware size impact is UNVERIFIED."
        )

    return Workspace(root=root, reacher=reacher, labrynth=labrynth, tools=tools, warnings=warnings)


def tooling_reality(workspace: Workspace) -> dict[str, Any]:
    """State what each gate actually does, separately from whether it exits zero.

    Both repos have a way to report success while verifying nothing, and an agent
    that trusts an exit code will bank both. This block exists so that never
    happens silently.
    """
    labrynth_present = workspace.labrynth.present
    eslint_config = None
    if labrynth_present:
        web = workspace.labrynth.path / "web"
        eslint_config = next(
            (c for c in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
             if (web / c).is_file()),
            None,
        )

    return {
        "reacher": {
            "tests": {"cmd": "pytest", "status": "working" if workspace.reacher.present else "unavailable",
                      "is_gate": True},
            "lint": {"cmd": "ruff check .", "status": "working" if workspace.reacher.present else "unavailable",
                     "is_gate": True},
            "skip_hazards": [
                {
                    "test": "tests/test_command_parity.py, test_firmware_parity.py",
                    "condition": "firmware/ tree absent",
                    "effect": "silently skips — a wheel-only run reports green having verified nothing",
                },
                {
                    "test": "tests/test_frontend_parity.py",
                    "condition": "labrynth checkout absent",
                    "effect": "skips — frontend parity UNVERIFIED, not passing",
                },
            ],
        },
        "labrynth": {
            "typecheck": {
                "cmd": "npx tsc -b",
                "cwd": "web",
                "status": "working" if labrynth_present else "unavailable",
                "is_gate": True,
            },
            "lint": {
                "cmd": "npm run lint",
                "status": "working" if eslint_config else "BROKEN",
                "is_gate": bool(eslint_config),
                "reason": None if eslint_config else
                          "ESLint 9 requires a flat eslint.config.js; none exists in web/",
                "hazard": None if eslint_config else
                          "exits 0 through a pipe — an agent can report 'lint passed' off a no-op",
            },
            "tests": {
                "status": "none",
                "reason": "no pytest, no vitest, no test files; adding a framework requires coordination",
            },
        },
    }
