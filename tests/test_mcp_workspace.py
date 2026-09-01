"""Workspace discovery and the checkout-beats-wheel guarantee.

The most valuable test here is test_checkout_wins_over_installed_package. If
that guarantee breaks, every other check in this suite keeps passing while
verifying the wrong tree — the definition of a silent failure.

The rest cover degradation: an absent repo, a mid-edit syntax error, a bad
schema version. Each must produce a loud, structured UNAVAILABLE rather than an
exception that kills a long-lived server or, worse, an empty result a comparison
reads as agreement.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from reacher.mcp import schema_client, workspace

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_reacher(root: Path, *, commands_body: str = "") -> Path:
    """Build a minimal tree that discovery and the schema dump will accept."""
    pkg = root / "src" / "reacher"
    (pkg / "kernel").mkdir(parents=True)
    (pkg / "kernel" / "commands.py").write_text(commands_body)
    return root


# --- Discovery ------------------------------------------------------------


def test_discovers_the_real_workspace():
    ws = workspace.discover()
    assert ws.reacher.present
    assert ws.reacher.path == REPO_ROOT
    assert ws.root == REPO_ROOT.parent


def test_archived_firmware_sibling_is_never_mistaken_for_a_repo(tmp_path):
    """reacher-firmware exists on real machines, has Commands.h, and looks convincing."""
    fake = tmp_path / "reacher-firmware"
    _make_reacher(fake)
    ws = workspace.discover(tmp_path)
    assert not ws.reacher.present, "the archived firmware repo was accepted as a checkout"
    assert any("no reacher checkout" in w for w in ws.warnings)


def test_missing_labrynth_warns_that_checks_are_unavailable_not_passing(tmp_path):
    _make_reacher(tmp_path / "reacher")
    ws = workspace.discover(tmp_path)
    assert ws.reacher.present and not ws.labrynth.present
    assert any("UNAVAILABLE, not passing" in w for w in ws.warnings), (
        "an absent repo must be reported as unverified, never as no-problem"
    )


def test_env_var_selects_the_workspace(tmp_path, monkeypatch):
    _make_reacher(tmp_path / "reacher")
    monkeypatch.setenv(workspace.WORKSPACE_ENV, str(tmp_path))
    assert workspace.discover().reacher.path == tmp_path / "reacher"


def test_override_file_redirects_discovery(tmp_path):
    _make_reacher(tmp_path / "elsewhere")
    (tmp_path / workspace.WORKSPACE_FILE).write_text(
        '[repos]\nreacher = "./elsewhere"\n'
    )
    assert workspace.discover(tmp_path).reacher.path == (tmp_path / "elsewhere").resolve()


def test_override_pointing_at_a_non_repo_is_rejected_with_a_warning(tmp_path):
    (tmp_path / "nothing").mkdir()
    (tmp_path / workspace.WORKSPACE_FILE).write_text('[repos]\nreacher = "./nothing"\n')
    ws = workspace.discover(tmp_path)
    assert not ws.reacher.present
    assert any("does not look like that repo" in w for w in ws.warnings)


def test_malformed_override_file_does_not_break_discovery(tmp_path):
    _make_reacher(tmp_path / "reacher")
    (tmp_path / workspace.WORKSPACE_FILE).write_text("this is not toml {{{")
    assert workspace.discover(tmp_path).reacher.present, "a bad config must fall back, not fail"


def test_wheel_layout_warns_that_firmware_checks_cannot_pass(tmp_path):
    _make_reacher(tmp_path / "reacher")  # no firmware/ tree
    ws = workspace.discover(tmp_path)
    assert any("firmware" in w and "UNAVAILABLE" in w for w in ws.warnings)


# --- Tooling reality ------------------------------------------------------


def test_tooling_block_reports_the_broken_lint_and_its_pipe_hazard():
    """An agent must be told before it runs `npm run lint`, not after it trusts a 0."""
    ws = workspace.discover()
    if not ws.labrynth.present:
        pytest.skip("labrynth checkout not present")
    lint = ws.as_dict()["tooling"]["labrynth"]["lint"]
    web = ws.labrynth.path / "web"
    has_config = any(
        (web / c).is_file() for c in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
    )
    if has_config:
        assert lint["status"] == "working" and lint["is_gate"]
    else:
        assert lint["status"] == "BROKEN"
        assert lint["is_gate"] is False
        assert "exits 0" in lint["hazard"]


def test_tooling_block_names_the_silent_skip_hazards():
    hazards = workspace.discover().as_dict()["tooling"]["reacher"]["skip_hazards"]
    assert any("silently skips" in h["effect"] for h in hazards)
    assert any("firmware" in h["condition"] for h in hazards)


def test_arduino_cli_absence_is_reported_as_unverified(tmp_path, monkeypatch):
    _make_reacher(tmp_path / "reacher")
    monkeypatch.setattr(
        workspace.shutil, "which", lambda name: None if name == "arduino-cli" else f"/usr/bin/{name}"
    )
    ws = workspace.discover(tmp_path)
    assert any("UNVERIFIED" in w for w in ws.warnings), (
        "a missing flash-headroom guard must be stated, not silently skipped — "
        "an unverified 94%-full build is how a non-expert ships broken firmware"
    )


# --- Schema client --------------------------------------------------------


def test_fetch_returns_the_document_for_a_real_checkout():
    doc = schema_client.fetch(REPO_ROOT)
    assert doc["schema_version"] in schema_client.SUPPORTED_SCHEMA_VERSIONS
    assert doc["python"]["commands"]
    assert doc["errors"] == []


def test_checkout_wins_over_installed_package():
    """The guarantee the whole subprocess design exists to provide.

    A checkout whose CommandCode differs from the installed wheel's must be what
    comes back. If this ever inverts, every check in this suite keeps passing
    while reading a tree the user is not editing.
    """
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-c", "import reacher; print(reacher.__file__)"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith(str(REPO_ROOT / "src")), (
        f"site-packages won over the checkout: imported {proc.stdout.strip()}"
    )


def test_fetch_rejects_a_directory_that_is_not_a_checkout(tmp_path):
    with pytest.raises(schema_client.SchemaUnavailable, match="does not look like"):
        schema_client.fetch(tmp_path)


def test_fetch_degrades_on_a_syntax_error_mid_edit(tmp_path):
    """An agent edits between calls; a broken file must not kill the server."""
    root = tmp_path / "reacher"
    src = root / "src" / "reacher"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("__version__ = '0.0.0'\n")
    (src / "schema.py").write_text("def dump(:\n")  # deliberately unparseable
    with pytest.raises(schema_client.SchemaUnavailable):
        schema_client.fetch(root, timeout_s=30)


def test_fetch_rejects_an_unsupported_schema_version(tmp_path):
    """A partially-understood document is how a checker reports unverified agreement."""
    root = tmp_path / "reacher"
    src = root / "src" / "reacher"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("__version__ = '0.0.0'\n")
    (src / "schema.py").write_text(textwrap.dedent("""
        import json, sys
        def main(argv=None):
            json.dump({"schema_version": 999, "python": {}, "firmware": {},
                       "errors": [], "warnings": [], "generated_from": {}}, sys.stdout)
            return 0
        if __name__ == "__main__":
            raise SystemExit(main())
    """))
    with pytest.raises(schema_client.SchemaUnavailable, match="outside the supported range"):
        schema_client.fetch(root, timeout_s=30)


def test_fetch_rejects_non_json_output(tmp_path):
    root = tmp_path / "reacher"
    src = root / "src" / "reacher"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("__version__ = '0.0.0'\n")
    (src / "schema.py").write_text("print('not json at all')\n")
    with pytest.raises(schema_client.SchemaUnavailable, match="invalid JSON"):
        schema_client.fetch(root, timeout_s=30)


def test_fetch_is_not_cached_between_calls(tmp_path):
    """An agent edits between calls; answering from a stale snapshot is worse than nothing."""
    first = schema_client.fetch(REPO_ROOT)
    second = schema_client.fetch(REPO_ROOT)
    assert first is not second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_firmware_available_reflects_the_document():
    assert schema_client.firmware_available({"firmware": {"present": True}})
    assert not schema_client.firmware_available({"firmware": {"present": False}})
    assert not schema_client.firmware_available({})
