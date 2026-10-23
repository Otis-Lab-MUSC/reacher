"""The change-request form, the edit guides, and plan_change.

The guides are data that point into source files by exact anchor strings. The
most important test here is test_every_guide_anchor_resolves: when a refactor
moves or renames an anchor, the guide is wrong, and an agent following it would
edit at a guess. That has to fail CI, not surface mid-change.
"""

import subprocess
from pathlib import Path

import pytest

from reacher.mcp import changes, guides, workspace
from reacher.mcp.schema_client import fetch

REPO_ROOT = Path(__file__).resolve().parent.parent

FULL = {
    "request": "arm the lever timeout only after rewarded presses",
    "kind": "device_setting",
    "paradigms": ["fr", "pr"],
    "device": "lever_rh",
    "setting": "timeout_mode",
    "reaches_board": True,
    "behavior": "TIMEOUT_MODE 1 arms the timeout only on rewarded presses",
    "done_when": "a non-rewarded press during the window is logged ACTIVE, not TIMEOUT",
}


@pytest.fixture(scope="module")
def doc():
    return fetch(REPO_ROOT)


@pytest.fixture(scope="module")
def roots():
    ws = workspace.discover()
    return {"reacher": REPO_ROOT,
            "labrynth": ws.labrynth.path if ws.labrynth.present else None}


# --- Guides are well formed and point at real code -------------------------


def test_guides_are_well_formed():
    assert changes.validate_guides() == []


def test_every_kind_in_the_decision_list_has_a_guide():
    assert {d["kind"] for d in guides.DECISION_LIST} == set(guides.KINDS)
    assert {e["kind"] for e in guides.EXAMPLES} <= set(guides.KINDS)


@pytest.mark.parametrize("kind", [k for k in guides.KINDS if guides.GUIDES[k]["steps"]])
def test_every_guide_anchor_resolves(kind, doc, roots):
    if roots["labrynth"] is None and any(s["repo"] == "labrynth" for s in guides.GUIDES[kind]["steps"]):
        pytest.skip("labrynth checkout not present: frontend anchors UNVERIFIED, not passing")
    fields = dict(FULL, kind=kind, device="aux", paradigms=["fr"], boards=["mega"], pins={"mega": 22})
    plan = changes.plan(fields, doc=doc, roots=roots)
    bad = [(s["n"], s["path"], s["anchor"], s["anchor_status"])
           for s in plan["steps"] if s["anchor_status"] != "found"]
    assert not bad, f"{kind} guide points at code that is not there: {bad}"


def test_device_setting_guide_still_covers_the_layers_timeout_mode_touched():
    """Lever timeout mode (5e5a6ba + labrynth 3e7601d6) touched these files. If
    the guide drops one, the next device setting will miss it the same way."""
    paths = {s["path"] for s in guides.GUIDES["device_setting"]["steps"]}
    for needed in (
        "firmware/libraries/REACHERDevices/src/Commands.h",
        "src/reacher/kernel/commands.py",
        "src/reacher/kernel/simulator.py",
        "web/src/types/index.ts",
        "web/src/store/useSessionStore.ts",
        "web/src/components/program/devicePresets.ts",
        "cli/presets.py",
    ):
        assert needed in paths, needed


# --- The form ---------------------------------------------------------------


def test_form_lists_every_field_with_a_question():
    out = changes.form(["fr", "pr"])
    assert out["ok"]
    names = [f["field"] for f in out["fields"]]
    assert names[:2] == ["request", "kind"]
    assert all(f["ask"] for f in out["fields"])
    para = next(f for f in out["fields"] if f["field"] == "paradigms")
    assert para["values"] == ["fr", "pr"]


def test_form_does_not_mutate_the_module_template():
    changes.form(["only"])
    assert "values" not in next(f for f in changes.FORM_FIELDS if f["field"] == "paradigms")


# --- plan_change outcomes ---------------------------------------------------


def test_no_kind_asks_to_sort_and_offers_a_hint():
    out = changes.plan({"request": "drive a tail-shock box from a TTL pin"})
    assert out["outcome"] == "needs_kind"
    assert out["suggested_kinds"][0] == "new_device"
    assert out["decision_list"]


def test_unknown_kind_is_an_error_not_a_guess():
    out = changes.plan({"request": "x", "kind": "rewrite_everything"})
    assert out["ok"] is False and out["error"] == "unknown_kind"


def test_runtime_setting_is_an_answer_with_no_steps():
    out = changes.plan({"request": "use pin 24 for the shock box", "kind": "runtime_setting"})
    assert out["outcome"] == "no_code_change"
    assert "steps" not in out
    assert any("pin" in a["want"] for a in out["answers"])


def test_missing_required_fields_block_the_plan():
    out = changes.plan({"request": "a new lever option", "kind": "device_setting"})
    assert out["outcome"] == "needs_answers"
    missing = {q["field"] for q in out["open_questions"] if q["required"]}
    assert {"paradigms", "device", "setting", "reaches_board", "done_when"} <= missing


def test_unknown_paradigm_is_an_open_question(doc):
    out = changes.plan(dict(FULL, paradigms=["fr", "random_ratio"]), doc=doc)
    assert out["outcome"] == "needs_answers"
    assert any("random_ratio" in q["question"] for q in out["open_questions"])


def test_full_device_setting_plan_expands_per_sketch(doc, roots):
    out = changes.plan(FULL, doc=doc, roots=roots)
    assert out["outcome"] == "plan"
    sketch_steps = [s for s in out["steps"] if s["path"].endswith(".ino")]
    assert {s["path"] for s in sketch_steps} == {"firmware/fr/fr.ino", "firmware/pr/pr.ino"}
    assert any("LEVER_RH_SET_TIMEOUT_MODE" in s["do"] for s in out["steps"])
    assert [s["n"] for s in out["steps"]] == list(range(1, len(out["steps"]) + 1))


def test_guard_summary_counts_silent_steps(doc, roots):
    out = changes.plan(FULL, doc=doc, roots=roots)
    gs = out["guard_summary"]
    assert gs["steps"] == len(out["steps"])
    assert gs["silent"] == len(gs["silent_steps"]) > 0
    for n in gs["silent_steps"]:
        assert out["steps"][n - 1]["verify"].startswith("by hand")


def test_host_only_setting_skips_firmware_and_simulator(doc, roots):
    """Counted pumps (b534b5f) never reached the board."""
    out = changes.plan(dict(FULL, device="pump", setting="counted", reaches_board=False,
                            request="only count pump 1 toward the infusion limit"),
                       doc=doc, roots=roots)
    paths = {s["path"] for s in out["steps"]}
    assert not any(p.startswith("firmware/") for p in paths)
    assert "src/reacher/kernel/simulator.py" not in paths


@pytest.mark.parametrize("device", sorted(guides.SAFETY_DEVICES))
def test_safety_devices_always_hand_off(device, doc):
    out = changes.plan(dict(FULL, device=device), doc=doc)
    assert out["stop_and_hand_off"]
    assert out["small_model_ok"] is False


def test_new_device_always_hands_off(doc):
    out = changes.plan(dict(FULL, kind="new_device", device="buzzer", boards=["mega"],
                            pins={"mega": 23}), doc=doc)
    assert out["stop_and_hand_off"] and out["small_model_ok"] is False
    assert out["bench_test"].startswith("always")


def test_timing_change_on_firmware_hands_off(doc):
    out = changes.plan(dict(FULL, behavior="add an onset delay in ms before the timeout arms"), doc=doc)
    assert any("timing" in r for r in out["stop_and_hand_off"])


def test_stale_anchor_is_reported_not_hidden(tmp_path, doc):
    fake = tmp_path / "labrynth"
    (fake / "web" / "src" / "types").mkdir(parents=True)
    (fake / "web" / "src" / "types" / "index.ts").write_text("// moved\n")
    out = changes.plan(dict(FULL, kind="ui_only"), doc=doc, roots={"reacher": REPO_ROOT, "labrynth": fake})
    assert out["stale_anchors"] == [1]
    assert "stale" in out["steps"][0]["anchor_status"]


def test_absent_repo_marks_steps_unchecked_rather_than_found(doc):
    out = changes.plan(FULL, doc=doc, roots={"reacher": None, "labrynth": None})
    assert all(s["anchor_status"].startswith("unchecked") for s in out["steps"])


# --- Upstream freshness ------------------------------------------------------


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(path), "PATH": "/usr/bin:/bin"})


def test_checkout_behind_upstream_is_warned(tmp_path):
    """A stale labrynth produced 8 false parity failures on 2026-10-23."""
    upstream = tmp_path / "up"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "commit", "-q", "--allow-empty", "-m", "one")
    root = tmp_path / "ws"
    root.mkdir()
    clone = root / "reacher"
    _git(tmp_path, "clone", "-q", str(upstream), str(clone))
    (clone / "src" / "reacher" / "kernel").mkdir(parents=True)
    (clone / "src" / "reacher" / "kernel" / "commands.py").write_text("")
    _git(upstream, "commit", "-q", "--allow-empty", "-m", "two")
    _git(upstream, "commit", "-q", "--allow-empty", "-m", "three")
    _git(clone, "fetch", "-q")

    ws = workspace.discover(root)
    assert ws.reacher.behind == 2
    assert ws.reacher.upstream == "origin/main"
    assert any("2 commit(s) behind origin/main" in w for w in ws.warnings)


def test_checkout_without_upstream_reports_none(tmp_path):
    root = tmp_path / "ws"
    repo = root / "reacher"
    (repo / "src" / "reacher" / "kernel").mkdir(parents=True)
    (repo / "src" / "reacher" / "kernel" / "commands.py").write_text("")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "one")
    ws = workspace.discover(root)
    assert ws.reacher.behind is None
    assert not any("behind" in w for w in ws.warnings)
