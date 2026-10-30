"""Style ratchet: count style violations under src/ and fail if any count rises above the baseline.

Usage:
    python scripts/style-baseline.py                 check against style-baseline.json
    python scripts/style-baseline.py --update        record lower counts (never raises one)
    python scripts/style-baseline.py --update --allow-increase
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "src"
DEFAULT_BASELINE = REPO_ROOT / "style-baseline.json"

MAX_FILE_LINES = 300
MAX_FUNCTION_LINES = 50
RUFF_LINE_LENGTH = "120"
RUFF_TARGET_VERSION = "py310"

# Ruff rule families counted by prefix; each becomes the metric `ruff_<family>`.
RUFF_FAMILIES = ("ANN", "D1", "BLE", "S110", "T20", "PLR2004", "C901", "PLR0912", "PLR0915", "ERA")

# Comment and docstring text that records history instead of intent; git holds history.
HISTORY_PATTERNS = (
    re.compile(r"\bFix(?:ed)?:\s*\S"),
    re.compile(r"\bFix\s+\d"),
    re.compile(r"#\d+\b"),
    re.compile(r"\b(?:issue|PR|pull request)\s+\d+\b", re.IGNORECASE),
    re.compile(r"\blegacy\b", re.IGNORECASE),
    re.compile(r"\bno longer\b", re.IGNORECASE),
    re.compile(r"\bpreviously\b", re.IGNORECASE),
)

BROAD_EXCEPTION_NAMES = frozenset({"Exception", "BaseException"})
DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
FUNCTION_NODE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

MYPY_METRIC = "mypy_strict_errors"
MYPY_ERROR_MARKER = ": error: "
MYPY_COUNTABLE_EXIT_CODES = (0, 1)


def list_source_files() -> list[Path]:
    """Return every Python file under src/, sorted."""
    return sorted(SOURCE_DIR.rglob("*.py"))


def parse_source_files(paths: list[Path]) -> dict[Path, ast.Module]:
    """Parse each file into an AST keyed by path."""
    return {path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path)) for path in paths}


def run_ruff_counts() -> dict[str, int]:
    """Count ruff violations per rule family using a fixed, isolated configuration."""
    command = [
        sys.executable, "-m", "ruff", "check", "--isolated",
        "--line-length", RUFF_LINE_LENGTH, "--target-version", RUFF_TARGET_VERSION,
        "--select", ",".join(RUFF_FAMILIES), "--output-format", "json", "--exit-zero", str(SOURCE_DIR),
    ]  # fmt: skip
    completed = subprocess.run(command, capture_output=True, text=True, check=True, cwd=REPO_ROOT)
    codes = [violation["code"] for violation in json.loads(completed.stdout)]
    return {f"ruff_{family}": sum(code.startswith(family) for code in codes) for family in RUFF_FAMILIES}


def count_mypy_errors() -> dict[str, int]:
    """Count errors reported by a clean, non-incremental `mypy --strict` run."""
    command = [sys.executable, "-m", "mypy", "--no-incremental"]
    completed = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    # mypy exits 1 when it finds errors; any other non-zero code means it did not check, and
    # counting its empty output would record zero errors and pass the ratchet.
    if completed.returncode not in MYPY_COUNTABLE_EXIT_CODES:
        raise RuntimeError(f"mypy exited {completed.returncode} without checking:\n{completed.stderr}")
    return {MYPY_METRIC: completed.stdout.count(MYPY_ERROR_MARKER)}


def is_broad_exception_handler(handler: ast.ExceptHandler) -> bool:
    """Return True for a bare except or one that names Exception or BaseException."""
    if handler.type is None:
        return True
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(node, ast.Name) and node.id in BROAD_EXCEPTION_NAMES for node in caught)


def count_broad_excepts(trees: dict[Path, ast.Module]) -> int:
    """Count except handlers that catch Exception, BaseException, or everything."""
    return sum(
        is_broad_exception_handler(node)
        for tree in trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
    )


def count_any_uses(trees: dict[Path, ast.Module]) -> int:
    """Count names and attributes spelled `Any`."""
    count = 0
    for tree in trees.values():
        for node in ast.walk(tree):
            count += isinstance(node, ast.Name) and node.id == "Any"
            count += isinstance(node, ast.Attribute) and node.attr == "Any"
    return count


def iter_comment_texts(source: str) -> Iterator[str]:
    """Yield the text of every comment in the source, without the leading hash."""
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            yield token.string.lstrip("#")


def iter_docstrings(tree: ast.Module) -> Iterator[str]:
    """Yield every module, class and function docstring in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, DOCSTRING_NODE_TYPES):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                yield docstring


def has_history_marker(text: str) -> bool:
    """Return True when the text matches any history pattern."""
    return any(pattern.search(text) for pattern in HISTORY_PATTERNS)


def count_history_comments(trees: dict[Path, ast.Module]) -> int:
    """Count comments and docstrings that match a history pattern."""
    count = 0
    for path, tree in trees.items():
        texts = [*iter_comment_texts(path.read_text(encoding="utf-8")), *iter_docstrings(tree)]
        count += sum(has_history_marker(text) for text in texts)
    return count


def count_long_files(paths: list[Path]) -> int:
    """Count files longer than MAX_FILE_LINES lines."""
    return sum(len(path.read_text(encoding="utf-8").splitlines()) > MAX_FILE_LINES for path in paths)


def count_long_functions(trees: dict[Path, ast.Module]) -> int:
    """Count functions and methods longer than MAX_FUNCTION_LINES lines."""
    return sum(
        isinstance(node, FUNCTION_NODE_TYPES) and node.end_lineno is not None
        and node.end_lineno - node.lineno + 1 > MAX_FUNCTION_LINES
        for tree in trees.values()
        for node in ast.walk(tree)
    )  # fmt: skip


def collect_metrics(skipped: set[str]) -> dict[str, int]:
    """Measure every metric that is not skipped."""
    paths = list_source_files()
    trees = parse_source_files(paths)
    metrics: dict[str, int] = {}
    if "ruff" not in skipped:
        metrics.update(run_ruff_counts())
    if "mypy" not in skipped:
        metrics.update(count_mypy_errors())
    metrics["except_exception_sites"] = count_broad_excepts(trees)
    metrics["any_uses"] = count_any_uses(trees)
    metrics["history_comments"] = count_history_comments(trees)
    metrics["files_over_300_lines"] = count_long_files(paths)
    metrics["functions_over_50_lines"] = count_long_functions(trees)
    return metrics


def load_baseline(path: Path) -> dict[str, int]:
    """Read the baseline JSON, or return an empty baseline when the file is absent."""
    if not path.exists():
        return {}
    return {name: int(value) for name, value in json.loads(path.read_text(encoding="utf-8")).items()}


def write_baseline(path: Path, metrics: dict[str, int]) -> None:
    """Write the metrics as JSON with sorted keys, one metric per line."""
    path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_against_baseline(current: dict[str, int], baseline: dict[str, int]) -> int:
    """Print one line per metric and return 1 if any metric exceeds its baseline."""
    exit_code = 0
    for name, value in sorted(current.items()):
        if name not in baseline:
            print(f"{name}: {value} (no baseline; add it with --update --allow-increase)")
            exit_code = 1
            continue
        print(f"{name}: {value} (baseline {baseline[name]})")
        if value > baseline[name]:
            exit_code = 1
        elif value < baseline[name]:
            print(f"  {name} is below baseline; lower the baseline with --update")
    return exit_code


def update_baseline(path: Path, current: dict[str, int], baseline: dict[str, int], allow_increase: bool) -> int:
    """Record the current counts, refusing any increase unless allowed."""
    raised = sorted(name for name, value in current.items() if value > baseline.get(name, -1))
    if raised and not allow_increase:
        for name in raised:
            print(f"refusing to raise {name}: {baseline.get(name, 'unset')} -> {current[name]}")
        print("pass --allow-increase to record higher counts")
        return 1
    write_baseline(path, {**baseline, **current})
    print(f"wrote {path.name}")
    return 0


def parse_arguments() -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description="Style ratchet over src/.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="baseline JSON path")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline with current counts")
    parser.add_argument("--allow-increase", action="store_true", help="let --update raise a count")
    parser.add_argument("--skip", action="append", choices=("ruff", "mypy"), default=[], help="skip a slow tool")
    return parser.parse_args()


def main() -> int:
    """Run the ratchet in check or update mode and return the exit code."""
    arguments = parse_arguments()
    current = collect_metrics(set(arguments.skip))
    baseline = load_baseline(arguments.baseline)
    if arguments.update:
        return update_baseline(arguments.baseline, current, baseline, arguments.allow_increase)
    return check_against_baseline(current, baseline)


if __name__ == "__main__":
    sys.exit(main())
