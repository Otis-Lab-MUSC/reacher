"""The change-request form, the edit guides, and plan_change.

The guides are data that point into source files by exact anchor strings. The
most important test here is test_every_guide_anchor_resolves: when a refactor
moves or renames an anchor, the guide is wrong, and an agent following it would
edit at a guess. That has to fail CI, not surface mid-change.
"""

import json
import re
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
    "setting": "reward_window",
    "reaches_board": True,
    "lite_twins": "exclude",
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


def test_every_named_guard_exists():
    """A guard that names a check, test or runner key that is not there guards nothing."""
    from reacher.mcp import checks, runner
    ids = {c.id for c in checks.all_checks()}
    for kind, guide in guides.GUIDES.items():
        for step in guide["steps"]:
            g = step["guard"]
            if g.startswith("check:"):
                assert g[6:] in ids, (kind, g)
            elif g.startswith("run:"):
                assert g[4:] in runner.COMMANDS, (kind, g)
            elif g.startswith("test:"):
                assert (REPO_ROOT / g[5:]).is_file(), (kind, g)


def test_every_kind_in_the_decision_list_has_a_guide():
    assert {d["kind"] for d in guides.DECISION_LIST} == set(guides.KINDS)
    assert {e["kind"] for e in guides.EXAMPLES} <= set(guides.KINDS)


def _combos():
    """Every (kind, device, reaches_board) a guide expands differently for."""
    for kind in guides.KINDS:
        if not guides.GUIDES[kind]["steps"]:
            continue
        if kind in ("device_setting", "pin_or_board"):
            for device in guides.DEVICES:
                for board in ((True, False) if kind == "device_setting" else (None,)):
                    yield kind, device, board
        else:
            yield kind, "buzzer", None


@pytest.mark.parametrize("kind,device,reaches_board", list(_combos()))
def test_every_guide_anchor_resolves(kind, device, reaches_board, doc, roots):
    if roots["labrynth"] is None and any(s["repo"] == "labrynth" for s in guides.GUIDES[kind]["steps"]):
        pytest.skip("labrynth checkout not present: frontend anchors UNVERIFIED, not passing")
    fields = dict(FULL, kind=kind, device=device, reaches_board=reaches_board,
                  paradigms=["fr", "fr_lite"], boards=["mega"], pins={"mega": 30})
    plan = changes.plan(fields, doc=doc, roots=roots)
    bad = [(s["n"], s["path"], s["anchor"], s["anchor_status"])
           for s in plan["steps"] if s["anchor"] is not None and s["anchor_status"] != "found"]
    assert not bad, f"{kind}/{device} guide points at code that is not there: {bad}"
    leftovers = [(s["n"], k, s[k]) for s in plan["steps"] for k in ("path", "anchor", "do", "verify")
                 if s[k] and re.search(r"\{(" + "|".join(guides.PLACEHOLDERS) + r")\}", s[k])]
    assert not leftovers, f"unfilled placeholders: {leftovers}"


def _paths(plan):
    return {s["path"] for s in plan["steps"]}


def test_device_setting_guide_covers_every_file_timeout_mode_touched(doc):
    """Lever timeout mode: reacher 5e5a6ba + be9d7bb, labrynth 3e7601d6 2997c55f
    1a5a061a. If the guide drops one, the next device setting misses it the same way."""
    paths = _paths(changes.plan(FULL, doc=doc))
    for needed in (
        "firmware/libraries/REACHERDevices/src/Commands.h",
        "firmware/libraries/REACHERDevices/src/Scheduler.h",
        "firmware/fr/fr.ino", "firmware/pr/pr.ino",
        "src/reacher/kernel/commands.py",
        "src/reacher/kernel/simulator.py",
        "src/reacher/api/routers/hardware.py",
        "web/src/types/index.ts",
        "web/src/store/useSessionStore.ts",
        "web/src/components/hardware/LeverControl.tsx",
        "web/src/components/program/devicePresets.ts",
        "web/src/components/program/presets/frSaPresets.ts",
        "web/src/components/monitor/hardwareSummary.ts",
        "web/src/components/monitor/SessionStartModal.tsx",
        "web/src/components/tutorial/help/content.ts",
        "cli/presets.py",
        "cli/app.py",
    ):
        assert needed in paths, needed


def test_host_only_setting_covers_what_counted_pumps_touched(doc):
    """Counted pumps (b534b5f, labrynth 5c659162) never reached the board: no
    firmware, no command code, no simulator; kernel, API, client and CLI instead."""
    out = changes.plan(dict(FULL, device="pump", setting="counted", reaches_board=False,
                            request="only count pump 1 toward the infusion limit"), doc=doc)
    paths = _paths(out)
    assert not any(p.startswith("firmware/") for p in paths)
    assert "src/reacher/kernel/simulator.py" not in paths
    assert "src/reacher/kernel/commands.py" not in paths
    assert not any("_SET_" in s["do"] for s in out["steps"])
    for needed in ("src/reacher/kernel/reacher.py", "src/reacher/api/routers/program.py",
                   "src/reacher/api/routers/session.py", "web/src/api/client.ts",
                   "web/src/hooks/useSessionRecovery.ts", "cli/app.py"):
        assert needed in paths, needed


def test_pin_or_board_guide_covers_every_copy_of_a_default_pin(doc):
    """Default pins live in four places; only Pins.h and the generated pinTables.ts are compared by C6."""
    out = changes.plan(dict(FULL, kind="pin_or_board", device="cue2", pins={"mega": 30}), doc=doc)
    by_path = {s["path"]: s for s in out["steps"]}
    assert by_path["firmware/libraries/REACHERDevices/src/Pins.h"]["anchor"] == "constexpr int8_t PIN_CUE_2 "
    assert "CUE_2" in by_path["src/reacher/kernel/simulator.py"]["do"]
    assert by_path["src/reacher/api/routers/hardware.py"]["guard"] == "none"
    assert by_path["web/src/generated/pinTables.ts"]["guard"] == "check:C6"


def test_pin_or_board_skips_the_simulator_for_a_device_it_does_not_pin(doc):
    out = changes.plan(dict(FULL, kind="pin_or_board", device="aux", pins={"mega": 30}), doc=doc)
    assert "src/reacher/kernel/simulator.py" not in _paths(out)


def test_new_device_guide_covers_the_aux_layers(doc):
    """AUX (reacher d17ecef..cf5a439, labrynth 3c720049..87135e0a)."""
    out = changes.plan(dict(FULL, kind="new_device", device="buzzer", boards=["mega"],
                            pins={"mega": 30}, lite_twins="exclude"), doc=doc)
    paths = _paths(out)
    for needed in (
        "firmware/libraries/REACHERDevices/src/Device.h",
        "firmware/libraries/REACHERDevices/src/Pins.h",
        "firmware/libraries/REACHERDevices/src/Commands.h",
        "firmware/libraries/REACHERDevices/src/Scheduler.cpp",
        "src/reacher/kernel/commands.py", "src/reacher/pin_overrides.py", "src/reacher/schema.py",
        "src/reacher/kernel/reacher.py", "src/reacher/kernel/simulator.py",
        "src/reacher/api/routers/hardware.py",
        "web/src/components/hardware/pinMeta.ts", "web/src/types/index.ts",
        "web/src/store/useSessionStore.ts", "web/src/hooks/useSessionWebSockets.ts",
        "web/src/components/monitor/EventTimeline.tsx",
        "web/src/components/monitor/SessionStartModal.tsx",
        "web/src/components/hardware/usePinOverridesHydration.ts",
    ):
        assert needed in paths, needed


def test_guide_names_a_guard_only_where_an_omission_fails_it():
    """Guards that were named but would not fail on the omission, now by hand."""
    def guard(kind, path):
        return next(s["guard"] for s in guides.GUIDES[kind]["steps"] if s["path"] == path)
    assert guard("new_device", "web/src/components/monitor/hardwareSummary.ts") == "none"
    assert guard("new_paradigm", "web/src/lib/firmwareBoard.ts") == "none"
    assert guard("new_device", "src/reacher/kernel/reacher.py") == "none"
    assert guard("new_device", "src/reacher/schema.py") == "check:C6"
    assert guard("preset", "scripts/check-cli-parity.py") == "run:cli_parity"


def test_every_plan_ends_by_writing_the_change_request_record(doc):
    out = changes.plan(FULL, doc=doc)
    last = out["steps"][-1]
    assert last["path"] == "changes/YYYY-MM-DD-<slug>.md"
    assert "change_request_record" in last["do"]


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
    assert any("LEVER_RH_SET_REWARD_WINDOW = 1070" in s["do"] for s in out["steps"])
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


@pytest.mark.parametrize("kind", ["device_setting", "pin_or_board"])
@pytest.mark.parametrize("device", sorted(guides.SAFETY_DEVICES))
def test_safety_devices_always_hand_off(device, kind, doc):
    out = changes.plan(dict(FULL, kind=kind, device=device, behavior="arm it", request="x",
                            setting="mode", pins={"mega": 30}), doc=doc)
    assert any("safety output" in r for r in out["stop_and_hand_off"])
    assert out["small_model_ok"] is False


@pytest.mark.parametrize("kind,words", [
    ("preset", "an SA preset with a longer pump duration"),
    ("ui_only", "rename the shock card"),
    ("device_setting", "let the laser follow LH presses only"),
    ("preset", "a preset with opto stimulation on every reward"),
])
def test_a_safety_output_named_only_in_the_words_still_hands_off(kind, words, doc):
    out = changes.plan(dict(FULL, kind=kind, device="lever_rh", request=words), doc=doc)
    assert any("safety output" in r for r in out["stop_and_hand_off"]), words


def test_a_harmless_setting_does_not_hand_off(doc):
    out = changes.plan(dict(FULL, behavior="mode 1 counts only rewarded presses",
                            done_when="the press is logged as TIMEOUT", setting="count_mode",
                            request="count only rewarded presses"), doc=doc)
    assert out["stop_and_hand_off"] == [] and out["small_model_ok"] is True


def test_timing_words_match_whole_words_only(doc):
    out = changes.plan(dict(FULL, request="list the items in the summary", behavior="show items",
                            done_when="summary lists them", setting="summary_items"), doc=doc)
    assert out["stop_and_hand_off"] == []


def test_kind_hints_match_whole_words_only():
    assert "new_device" not in changes.guess_kinds("add a checkbox to the lever card")
    assert "device_setting" not in changes.guess_kinds("update the model number")


def test_new_paradigm_always_hands_off(doc):
    out = changes.plan(dict(FULL, kind="new_paradigm", request="add a random-ratio schedule"), doc=doc)
    assert out["stop_and_hand_off"] and out["small_model_ok"] is False


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
    assert all(s["anchor_status"].startswith("unchecked") for s in out["steps"] if s["anchor"])


# --- Lite twins and device names ---------------------------------------------


def test_a_lite_twin_left_out_is_a_question_not_a_silent_skip(doc):
    out = changes.plan(dict(FULL, lite_twins=None), doc=doc)
    assert out["outcome"] == "needs_answers"
    q = next(q for q in out["open_questions"] if q["field"] == "lite_twins")
    assert "fr_lite" in q["question"] and "pr_lite" in q["question"]


def test_lite_twins_include_adds_the_uno_sketches(doc):
    out = changes.plan(dict(FULL, lite_twins="include"), doc=doc)
    assert out["outcome"] == "plan"
    assert out["change_request"]["paradigms"] == ["fr", "pr", "fr_lite", "pr_lite"]
    assert {s["path"] for s in out["steps"] if s["path"].endswith(".ino")} == {
        "firmware/fr/fr.ino", "firmware/pr/pr.ino",
        "firmware/fr_lite/fr_lite.ino", "firmware/pr_lite/pr_lite.ino"}


def test_mega_only_devices_are_not_asked_about_lite_twins(doc):
    out = changes.plan(dict(FULL, device="aux", lite_twins=None), doc=doc)
    assert not any(q["field"] == "lite_twins" for q in out["open_questions"])


def test_host_only_settings_are_not_asked_about_lite_twins(doc):
    out = changes.plan(dict(FULL, reaches_board=False, lite_twins=None), doc=doc)
    assert not any(q["field"] == "lite_twins" for q in out["open_questions"])


def test_unknown_device_is_an_open_question(doc):
    out = changes.plan(dict(FULL, device="buzzer"), doc=doc)
    assert out["outcome"] == "needs_answers"
    assert any("new_device" in q["question"] for q in out["open_questions"])


def test_new_device_with_an_existing_key_is_an_open_question(doc):
    out = changes.plan(dict(FULL, kind="new_device", device="laser", boards=["mega"],
                            pins={"mega": 30}), doc=doc)
    assert any("device_setting" in q["question"] for q in out["open_questions"])


def test_device_names_come_from_the_table_not_the_key(doc):
    """cue2's command prefix is CUE2, its Pins.h symbol PIN_CUE_2, its UI key secondaryCue."""
    out = changes.plan(dict(FULL, device="cue2", setting="ramp"), doc=doc)
    text = " ".join(s["do"] + s["anchor"] + s["path"] for s in out["steps"] if s["anchor"])
    assert "CUE2_SET_RAMP" in text and "secondaryCue" in text and "CueControl" in text


# --- The change-request record ----------------------------------------------


def _front(text):
    head = text.split("---\n")[1]
    return {k: json.loads(v) for k, v in (line.split(": ", 1) for line in head.strip().splitlines())}


def test_record_refuses_an_unconfirmed_request(doc):
    out = changes.record(dict(FULL, done_when=None), doc=doc)
    assert out["ok"] is False and out["error"] == "not_confirmed"
    assert any(q["field"] == "done_when" for q in out["open_questions"])


def test_record_front_matter_has_the_fixed_schema(doc):
    answers = [{"question": "Should UNO builds get it?", "answer": "No: Mega rigs only"}]
    out = changes.record(dict(FULL, answers=answers), doc=doc, date="2026-10-23")
    assert out["ok"] is True
    front = _front(out["text"])
    assert list(front) == ["schema", "date", "kind", "request", "paradigms", "boards", "devices",
                           "pins", "commands", "reaches_board", "safety_flags", "small_model_ok",
                           "bench_test", "silent_steps"]
    assert front["schema"] == changes.RECORD_SCHEMA
    assert front["kind"] == "device_setting" and front["devices"] == ["lever_rh"]
    assert front["boards"] == ["mega"]
    assert front["commands"] == [{"name": "LEVER_RH_SET_REWARD_WINDOW", "status": "new"}]
    assert "No: Mega rigs only" in out["text"]
    assert {t["repo"] for t in out["targets"]} == {"reacher", "labrynth"}
    assert out["targets"][0]["path"] == "changes/2026-10-23-arm-the-lever-timeout-only-after-rewarded-presses.md"


def test_record_marks_an_existing_command_as_existing(doc):
    out = changes.record(dict(FULL, commands=["LEVER_RH_SET_TIMEOUT"]), doc=doc, date="2026-10-23")
    assert _front(out["text"])["commands"][0] == {"name": "LEVER_RH_SET_TIMEOUT", "status": "existing"}


def test_a_setting_that_already_has_a_command_is_a_question(doc):
    """Lever timeout mode exists (1077); re-adding it would duplicate the code."""
    out = changes.plan(dict(FULL, setting="timeout_mode"), doc=doc)
    assert out["outcome"] == "needs_answers"
    assert any("LEVER_RH_SET_TIMEOUT_MODE already exists (code 1077)" in q["question"]
               for q in out["open_questions"])


@pytest.mark.parametrize("device", sorted(guides.DEVICES))
def test_the_suggested_parameter_code_is_free_and_in_the_device_block(device, doc):
    out = changes.plan(dict(FULL, device=device, setting="brand_new"), doc=doc)
    do = next(s["do"] for s in out["steps"] if s["path"].endswith("Commands.h"))
    code = int(re.search(r"= (\d+);", do).group(1))
    assert code not in doc["python"]["command_code_enum"].values()
    assert guides.DEVICES[device]["param_base"] <= code < guides.DEVICES[device]["param_base"] + 10


def test_record_quotes_text_that_would_break_yaml(doc):
    out = changes.record(dict(FULL, request="mode: 1 # rewarded only"), doc=doc, date="2026-10-23")
    assert _front(out["text"])["request"] == "mode: 1 # rewarded only"


def test_record_for_a_frontend_only_change_targets_labrynth_only(doc):
    out = changes.record({"request": "relabel", "kind": "ui_only", "done_when": "reads Shock"},
                         doc=doc, date="2026-10-23")
    assert [t["repo"] for t in out["targets"]] == ["labrynth"]


def test_record_rejects_a_bad_date(doc):
    out = changes.record(FULL, doc=doc, date="23/10/2026")
    assert out["ok"] is False and out["error"] == "bad_date"


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


def test_device_table_covers_exactly_the_registered_components(doc):
    """A device added to the registry without a DEVICES row would plan with blank names."""
    assert set(guides.DEVICES) == set(doc["python"]["component_keys"])
    assert set(guides.SAFETY_DEVICES) <= set(guides.DEVICES)
