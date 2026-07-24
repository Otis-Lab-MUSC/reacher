#!/usr/bin/env python3
"""Bump or verify the version string across all source files.

Usage:
    python scripts/bump-version.py                # print current version
    python scripts/bump-version.py 2.1.0          # set version to 2.1.0
    python scripts/bump-version.py --check 2.1.0  # verify all files match 2.1.0 (exit 1 if not)

Every version-bearing file in the repo is stamped in one pass so a single
invocation keeps the whole tree aligned. Some files carry the version in a
*derived* form rather than the literal semver string:

  - README badge   — shields.io escapes ``-`` as ``--`` (``3.0.0-alpha.1``
                     renders as ``3.0.0--alpha.1``).
  - wheel filename — PEP 440 normalizes prerelease tags (``3.0.0-alpha.1``
                     becomes ``3.0.0a1``).

Each tracked file therefore declares a transform that maps the canonical
semver string to the form that file stores; ``--check`` compares against the
transformed value, so CI (which passes the bare tag, e.g. ``3.0.0-alpha.1``)
still validates the derived spellings.

After bumping, recompile the firmware hex artifacts (``bash firmware/compile.sh``)
so the shipped binaries report the new version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")


# -- version-form transforms -------------------------------------------------


def identity(v: str) -> str:
    return v


_PEP440_TAGS = {"alpha": "a", "beta": "b", "rc": "rc"}


def pep440(v: str) -> str:
    """``3.0.0-alpha.1`` -> ``3.0.0a1`` (PEP 440 prerelease normalization)."""
    m = re.match(r"^(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)\.(\d+))?$", v)
    if not m:
        return v
    base, kind, num = m.groups()
    if not kind:
        return base
    return f"{base}{_PEP440_TAGS[kind]}{num}"


def shields(v: str) -> str:
    """shields.io badge escaping: ``-`` -> ``--`` (``3.0.0-alpha.1`` -> ``3.0.0--alpha.1``)."""
    return v.replace("-", "--")


# (file, pattern with 3 capture groups, replacement template, transform)
# group(2) is the version; template keeps group 1 and 3 around the new value.
VERSION_FILES: list[tuple[Path, str, str, "callable"]] = [
    (
        ROOT / "pyproject.toml",
        r'^(version\s*=\s*")([^"]+)(")',
        r"\g<1>{version}\3",
        identity,
    ),
    (
        ROOT / "src" / "reacher" / "__init__.py",
        r'^(__version__\s*=\s*")([^"]+)(")',
        r"\g<1>{version}\3",
        identity,
    ),
    # Firmware: library manifest + the version each sketch reports over serial
    # in SendIdentification(). After bumping these, recompile the hex artifacts
    # (bash firmware/compile.sh) so the shipped binaries report the new version.
    (
        ROOT / "firmware" / "libraries" / "REACHERDevices" / "library.properties",
        r"^(version=)(\S+)()$",
        r"\g<1>{version}\3",
        identity,
    ),
    *[
        (
            ROOT / "firmware" / sketch / f"{sketch}.ino",
            r'(\\"version\\":\\"v)([^\\"]+)(\\")',
            r"\g<1>{version}\3",
            identity,
        )
        for sketch in ("fr", "pr", "vi", "omission", "pavlovian", "fr_lite")
    ],
    # README version badge (shields.io escapes '-' as '--').
    (
        ROOT / "README.md",
        r"(shields\.io/badge/version-)(.+?)(-blue)",
        r"\g<1>{version}\3",
        shields,
    ),
    # README wheel-install example (PEP 440 normalized filename).
    (
        ROOT / "README.md",
        r"(pip install reacher2p-)(\S+?)(-py3-none-any\.whl)",
        r"\g<1>{version}\3",
        pep440,
    ),
]


def read_versions() -> dict[str, str]:
    """Return {label: version} for each tracked file (raw stored form)."""
    versions: dict[str, str] = {}
    for path, pattern, _, _transform in VERSION_FILES:
        text = path.read_text()
        match = re.search(pattern, text, re.MULTILINE)
        label = _label(path, pattern)
        versions[label] = match.group(2) if match else "<not found>"
    return versions


def _label(path: Path, pattern: str) -> str:
    """Disambiguate multiple patterns in the same file (e.g. README badge vs wheel)."""
    rel = str(path.relative_to(ROOT))
    if "badge/version" in pattern:
        return f"{rel} (badge)"
    if "py3-none-any" in pattern:
        return f"{rel} (wheel)"
    return rel


def set_version(new_version: str) -> None:
    """Write *new_version* (in each file's required form) into every tracked file."""
    for path, pattern, template, transform in VERSION_FILES:
        text = path.read_text()
        replacement = template.replace("{version}", transform(new_version))
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count == 0:
            print(f"  WARNING: pattern not found in {_label(path, pattern)}")
        path.write_text(new_text)


def check_version(expected: str) -> bool:
    """Return True if every tracked file contains *expected* (in its required form)."""
    ok = True
    for path, pattern, _, transform in VERSION_FILES:
        text = path.read_text()
        match = re.search(pattern, text, re.MULTILINE)
        found = match.group(2) if match else "<not found>"
        want = transform(expected)
        label = _label(path, pattern)
        if found != want:
            print(f"  MISMATCH {label}: expected {want}, found {found}")
            ok = False
        else:
            print(f"  OK       {label}: {found}")
    return ok


def main() -> None:
    args = sys.argv[1:]

    # No args — print current versions
    if not args:
        print("Current versions:")
        for label, version in read_versions().items():
            print(f"  {label}: {version}")
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
