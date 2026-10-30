"""The style ratchet script: passes on the committed baseline, fails on a rise, refuses silent increases."""

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "style-baseline.py"
COMMITTED_BASELINE = REPO_ROOT / "style-baseline.json"
# mypy dominates the runtime; the AST and ruff metrics exercise every code path under test.
FAST_ARGUMENTS = ["--skip", "mypy"]


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, cwd=REPO_ROOT)


def copy_baseline(tmp_path: Path) -> Path:
    copy = tmp_path / "baseline.json"
    shutil.copy(COMMITTED_BASELINE, copy)
    return copy


def test_committed_baseline_passes() -> None:
    result = run_script(*FAST_ARGUMENTS)
    assert result.returncode == 0, result.stdout


def test_fails_when_baseline_is_below_current(tmp_path: Path) -> None:
    baseline_path = copy_baseline(tmp_path)
    baseline = json.loads(baseline_path.read_text())
    baseline["any_uses"] -= 1
    baseline_path.write_text(json.dumps(baseline))

    result = run_script(*FAST_ARGUMENTS, "--baseline", str(baseline_path))

    assert result.returncode == 1
    assert "any_uses" in result.stdout


def test_update_refuses_to_raise_without_allow_increase(tmp_path: Path) -> None:
    baseline_path = copy_baseline(tmp_path)
    baseline = json.loads(baseline_path.read_text())
    baseline["any_uses"] -= 1
    baseline_path.write_text(json.dumps(baseline))
    before = baseline_path.read_text()

    refused = run_script(*FAST_ARGUMENTS, "--baseline", str(baseline_path), "--update")
    assert refused.returncode == 1
    assert "refusing to raise any_uses" in refused.stdout
    assert baseline_path.read_text() == before

    allowed = run_script(*FAST_ARGUMENTS, "--baseline", str(baseline_path), "--update", "--allow-increase")
    assert allowed.returncode == 0
    assert json.loads(baseline_path.read_text())["any_uses"] == baseline["any_uses"] + 1


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("style_baseline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_mypy_run(returncode: int, stdout: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="mypy: crashed")

    return run


def test_mypy_that_cannot_run_raises_instead_of_counting_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module()
    monkeypatch.setattr(module.subprocess, "run", fake_mypy_run(returncode=2, stdout=""))

    with pytest.raises(RuntimeError, match="mypy"):
        module.count_mypy_errors()


def test_mypy_errors_are_counted_on_exit_code_one(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module()
    output = "src/a.py:1: error: one\nsrc/b.py:2: error: two\nFound 2 errors in 2 files\n"
    monkeypatch.setattr(module.subprocess, "run", fake_mypy_run(returncode=1, stdout=output))

    assert module.count_mypy_errors() == {"mypy_strict_errors": 2}


def test_update_lowers_a_count(tmp_path: Path) -> None:
    baseline_path = copy_baseline(tmp_path)
    baseline = json.loads(baseline_path.read_text())
    baseline["any_uses"] += 5
    baseline_path.write_text(json.dumps(baseline))

    result = run_script(*FAST_ARGUMENTS, "--baseline", str(baseline_path), "--update")

    assert result.returncode == 0
    assert json.loads(baseline_path.read_text())["any_uses"] == baseline["any_uses"] - 5
