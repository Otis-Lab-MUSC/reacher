#!/usr/bin/env python3
"""Bump or verify the version string across all source files.

Usage:
    python scripts/bump-version.py                # print current version
    python scripts/bump-version.py 2.1.0          # set version to 2.1.0
    python scripts/bump-version.py --check 2.1.0  # verify all files match 2.1.0 (exit 1 if not)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_FILES: list[tuple[Path, str, str]] = [
    # (file, pattern to match, replacement template with {version} placeholder)
    (
        ROOT / "pyproject.toml",
        r'^(version\s*=\s*")([^"]+)(")',
        r'\g<1>{version}\3',
    ),
    (
        ROOT / "src" / "reacher" / "__init__.py",
        r'^(__version__\s*=\s*")([^"]+)(")',
        r'\g<1>{version}\3',
    ),
]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")


def read_versions() -> dict[str, str]:
    """Return {filepath: version} for each tracked file."""
    versions: dict[str, str] = {}
    for path, pattern, _ in VERSION_FILES:
        text = path.read_text()
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            versions[str(path.relative_to(ROOT))] = match.group(2)
        else:
            versions[str(path.relative_to(ROOT))] = "<not found>"
    return versions


def set_version(new_version: str) -> None:
    """Write *new_version* into every tracked file."""
    for path, pattern, template in VERSION_FILES:
        text = path.read_text()
        replacement = template.replace("{version}", new_version)
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count == 0:
            print(f"  WARNING: pattern not found in {path.relative_to(ROOT)}")
        path.write_text(new_text)


def check_version(expected: str) -> bool:
    """Return True if every tracked file contains *expected*."""
    ok = True
    for file, version in read_versions().items():
        if version != expected:
            print(f"  MISMATCH {file}: expected {expected}, found {version}")
            ok = False
        else:
            print(f"  OK       {file}: {version}")
    return ok


def main() -> None:
    args = sys.argv[1:]

    # No args — print current versions
    if not args:
        print("Current versions:")
        for file, version in read_versions().items():
            print(f"  {file}: {version}")
        return

    # --check <version>
    if args[0] == "--check":
        if len(args) != 2:
            print("Usage: bump-version.py --check <version>", file=sys.stderr)
            sys.exit(2)
        expected = args[1]
        print(f"Checking all files match version {expected}:")
        if not check_version(expected):
            sys.exit(1)
        print("All files consistent.")
        return

    # <version> — set it
    new_version = args[0]
    if not SEMVER_RE.match(new_version):
        print(f"Invalid semver: {new_version}", file=sys.stderr)
        sys.exit(2)

    print(f"Bumping version to {new_version}:")
    set_version(new_version)
    check_version(new_version)
    print("Done.")


if __name__ == "__main__":
    main()
