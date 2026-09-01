"""The MCP tool surface and the runner's honesty guarantees.

Two properties are asserted repeatedly because everything else rests on them:

* No tool raises. A stdio server is long-lived and reads a tree the user's agent
  is actively editing, so a syntax error mid-edit must come back as a structured
  error, not kill the process.
* A zero exit code is never, by itself, a pass. Both repos can report success
  while verifying nothing, so `verdict` is derived from what actually ran.
"""

import asyncio

import pytest

from reacher.mcp import runner
from reacher.mcp.workspace import discover

mcp_sdk = pytest.importorskip("mcp", reason="the [mcp] extra is not installed")
from reacher.mcp.server import mcp  # noqa: E402

EXPECTED_TOOLS = {
    "describe_workspace", "list_commands", "get_hardware_map",
    "explain_event_flow", "check_consistency", "run_checks",
}


def call(name: str, args: dict | None = None) -> dict:
    result = asyncio.run(mcp.call_tool(name, args or {}))
    assert not result.is_error, result.content
    return result.structured_content


# --- Tool surface ---------------------------------------------------------


def test_every_expected_tool_is_registered():
    assert {t.name for t in asyncio.run(mcp.list_tools())} == EXPECTED_TOOLS


def test_every_tool_has_a_description_and_schema():
    for tool in asyncio.run(mcp.list_tools()):
        assert tool.description and len(tool.description) > 40, f"{tool.name} is underdocumented"
        assert tool.input_schema["type"] == "object"


def test_no_tool_can_write_files():
    """The agent writes, under its own permission model. The server never does."""
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    forbidden = {"write", "apply", "edit", "scaffold_apply", "patch", "commit"}
    assert not any(any(f in n for f in forbidden) for n in names), (
        f"a write-capable tool would bypass the permission prompts the user relies on: {names}"
    )


def test_prompts_are_registered():
    assert {p.name for p in asyncio.run(mcp.list_prompts())} == {"reacher_change", "reacher_verify"}


def test_server_instructions_warn_about_unverified_checks():
    """An agent must know before it starts that UNAVAILABLE is not a pass."""
    from reacher.mcp.server import INSTRUCTIONS
    assert "UNAVAILABLE" in INSTRUCTIONS
    assert "not a pass" in INSTRUCTIONS


# --- Tools never raise ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS - {"run_checks"}))
def test_tools_return_structured_errors_for_a_bad_workspace(tmp_path, name):
    """A nonexistent workspace must degrade, not raise out of the server."""
    out = call(name, {"workspace": str(tmp_path / "nope")})
    assert out["ok"] is False
    assert out.get("error") or out.get("results") is not None


def test_run_checks_reports_unavailable_for_a_bad_workspace(tmp_path):
    out = call("run_checks", {"names": ["ruff"], "workspace": str(tmp_path / "nope")})
    assert out["ok"] is False
    assert out["results"][0]["verdict"] == "UNAVAILABLE"
    assert out["results"][0]["ran"] is False


def test_run_checks_rejects_a_command_not_on_the_allowlist():
    out = call("run_checks", {"names": ["rm -rf /"]})
    result = out["results"][0]
    assert result["verdict"] == "UNAVAILABLE"
    assert result["ran"] is False
    assert "not an allowlisted command" in result["reason"]


def test_allowlist_never_exposes_hex_regeneration():
    """compile.sh rewrites committed build artifacts; that stays a human action."""
    joined = " ".join(" ".join(c.argv) for c in runner.COMMANDS.values())
    assert "compile.sh" not in joined


def test_allowlist_uses_fixed_argv_vectors():
    """The caller selects a key; it never composes a command line."""
    for cmd in runner.COMMANDS.values():
        assert isinstance(cmd.argv, tuple) and cmd.argv
        assert not any(any(ch in part for ch in ";|&$`") for part in cmd.argv)


# --- Verdict honesty ------------------------------------------------------


def test_pytest_skips_downgrade_the_verdict():
    """A green run with skipped tests verified less than it appears to."""
    out = runner._verdict(runner.COMMANDS["pytest"], 0, "42 passed, 7 skipped in 1.2s")
    assert out["verdict"] == "pass_with_skips"
    assert out["skipped"] == 7
    assert "unverified" in out["reason"]


def test_clean_pytest_run_is_a_pass():
    assert runner._verdict(runner.COMMANDS["pytest"], 0, "42 passed in 1.2s")["verdict"] == "pass"


def test_lint_warnings_downgrade_the_verdict():
    """Warnings are findings the config chose not to block on, not accepted ones."""
    out = runner._verdict(
        runner.COMMANDS["lint"], 0, "✖ 34 problems (0 errors, 34 warnings)"
    )
    assert out["verdict"] == "pass_with_warnings"
    assert out["warnings"] == 34
    assert "not findings that were reviewed" in out["reason"]


def test_lint_is_unavailable_without_a_flat_eslint_config(tmp_path, monkeypatch):
    """Without a config ESLint 9 finds nothing and still exits 0 — a false green."""
    monkeypatch.setattr(runner, "_has_eslint_config", lambda web: False)
    ws = discover()
    if not ws.labrynth.present:
        pytest.skip("labrynth checkout not present")
    status = runner.available(ws)["lint"]
    assert not status["runnable"]
    assert "exits 0" in status["reason"]


def test_nonzero_exit_is_a_fail():
    assert runner._verdict(runner.COMMANDS["ruff"], 1, "found 3 errors")["verdict"] == "fail"


# --- Live behaviour -------------------------------------------------------


def test_describe_workspace_states_the_tooling_reality():
    out = call("describe_workspace")
    assert out["ok"] is True
    assert "tooling" in out and "commands" in out
    for repo in out["tooling"].values():
        assert repo


def test_check_consistency_reports_unavailable_in_its_summary():
    out = call("check_consistency")
    assert "UNAVAILABLE (not verified)" in out["summary"]
    assert set(out["counts"]) >= {"pass", "fail", "unavailable", "error"}


def test_check_consistency_results_declare_their_sources():
    """Provenance is what stops an agent deleting correct code on a partial model."""
    for result in call("check_consistency")["results"]:
        assert result["derived_from"], f"{result['id']} does not say what it read"


def test_explain_event_flow_separates_the_two_contracts():
    out = call("explain_event_flow", {"device": "LICK"})
    if not out["ok"]:
        pytest.skip(out.get("message", "firmware unavailable"))
    contract = out["contract"]
    assert contract["validate_reacher_against_firmware"] == ["config", "param", "event"]
    assert contract["validate_anything_downstream_against_reacher"] == ["post_kernel"]
    assert out["device"]["in_post_kernel"] is True


def test_list_commands_surfaces_unhandled_paradigms():
    out = call("list_commands", {"name_contains": "LASER_TRIGGER"})
    if not out["ok"]:
        pytest.skip(out.get("message", "ground truth unavailable"))
    names = {c["name"] for c in out["commands"]}
    assert "LASER_TRIGGER_LH_ONLY" in names
    gap = next(c for c in out["commands"] if c["name"] == "LASER_TRIGGER_LH_ONLY")
    assert gap["known_gap"], "the recorded firmware gap should travel with the command"
    assert "vi" in gap["known_gap"]["paradigms"]


def test_free_code_suggestions_stay_inside_their_device_range():
    """Codes are structured by device (3xx cue, 4xx pump, ...).

    Suggesting an arbitrary unused integer would break a convention both firmware
    and the frontend rely on, so a suggestion outside its own range is worse than
    no suggestion.
    """
    out = call("list_commands")
    if not out["ok"] or not out.get("free_codes"):
        pytest.skip("ground truth unavailable")

    used = {c["code"] for c in out["commands"]}
    assert out["free_codes"], "no ranges were reported"
    for label, codes in out["free_codes"].items():
        prefix = label.split()[0]                      # "3xx" / "10xx"
        lo = int(prefix.replace("x", "0"))             # 300 / 1000
        hi = lo + (10 ** prefix.count("x")) - 1        # 399 / 1099
        for code in codes:
            assert lo <= code <= hi, f"{code} suggested for {label} ({lo}-{hi})"
            assert code not in used, f"{code} is already taken but suggested as free"
