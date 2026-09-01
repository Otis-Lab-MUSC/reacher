"""Golden-negatives for the consistency engine.

Every check passes against the real tree today. That makes them worthless as
evidence on their own — a check that has never failed is indistinguishable from
one that cannot. These drive each rule with deliberately drifted input and
assert three things:

1. it fails,
2. its evidence names **both** sides concretely (a result saying "mismatch"
   without the values tells an agent nothing it can act on), and
3. an absent repo yields UNAVAILABLE, never PASS.

Point 3 is the one that matters most. Both repos have a way to report success
while verifying nothing, so a status vocabulary that cannot distinguish "checked
and fine" from "did not check" would launder exactly the failures this engine
exists to catch.
"""

import copy

import pytest

from reacher.mcp.checks import CheckContext, Status, build_context, run
from reacher.mcp.checks.registry import all_checks

pytest_plugins = ()


@pytest.fixture(scope="module")
def live():
    """The real workspace context. Skipped if either checkout is missing."""
    ctx = build_context()
    if ctx.schema is None or ctx.pin_meta is None:
        pytest.skip("both reacher and labrynth checkouts required for golden-negatives")
    return ctx


def _drift(live: CheckContext, mutate) -> CheckContext:
    """Deep-copy the live context and apply *mutate* to the copy."""
    ctx = CheckContext(
        schema=copy.deepcopy(live.schema),
        pin_meta=copy.deepcopy(live.pin_meta),
        board_types=list(live.board_types) if live.board_types else None,
        reacher_root=live.reacher_root,
        labrynth_root=live.labrynth_root,
    )
    mutate(ctx)
    return ctx


def _result(ctx: CheckContext, cid: str) -> dict:
    out = run(ctx, ids=[cid])
    assert len(out["results"]) == 1, f"{cid} did not run"
    return out["results"][0]


def _assert_fails_with_evidence(ctx, cid, *, mentions=()):
    result = _result(ctx, cid)
    assert result["status"] == Status.FAIL.value, f"{cid} did not fail: {result}"
    assert result["evidence"], f"{cid} failed without evidence"
    assert result["fix_hint"], f"{cid} failed without a fix hint"
    blob = repr(result["evidence"])
    for token in mentions:
        assert str(token) in blob, f"{cid} evidence does not name {token!r}: {blob[:400]}"
    return result


# --- Baseline -------------------------------------------------------------


def test_live_workspace_is_clean(live):
    """The negatives below only prove something if the baseline is green."""
    out = run(live)
    assert out["verified"], out["summary"]
    assert out["counts"]["unavailable"] == 0, out["summary"]


def test_every_registered_check_runs(live):
    assert len(run(live)["results"]) == len(all_checks())


# --- Command / firmware rules --------------------------------------------


def test_c1_detects_a_firmware_only_command(live):
    ctx = _drift(live, lambda c: c.schema["firmware"]["commands_h"].update({"NEW_THING": 999}))
    _assert_fails_with_evidence(ctx, "C1", mentions=["NEW_THING"])


def test_c1_detects_value_drift(live):
    def mutate(c):
        name = next(iter(c.schema["firmware"]["commands_h"]))
        c.schema["firmware"]["commands_h"][name] += 1
    _assert_fails_with_evidence(_drift(live, mutate), "C1", mentions=["value_drift"])


def test_c2_detects_a_code_with_no_spec(live):
    ctx = _drift(live, lambda c: c.schema["python"]["command_code_enum"].update({"ORPHAN": 4242}))
    _assert_fails_with_evidence(ctx, "C2", mentions=["ORPHAN"])


def test_c13_detects_a_lite_twin_missing_a_command(live):
    """The failure mode of maintaining nine sketches by hand."""
    def mutate(c):
        for sketch in c.schema["firmware"]["sketches"]:
            if sketch["name"] == "fr_lite":
                sketch["cmd_refs"] = [r for r in sketch["cmd_refs"] if r != "SESSION_START"]
    _assert_fails_with_evidence(_drift(live, mutate), "C13", mentions=["fr_lite", "SESSION_START"])


def test_c13_detects_two_photon_leaking_into_a_lite_build(live):
    """A lite board has no two-photon hardware at all."""
    def mutate(c):
        for sketch in c.schema["firmware"]["sketches"]:
            if sketch["name"] == "fr_lite":
                sketch["cmd_refs"] = sketch["cmd_refs"] + ["MICROSCOPE_ARM"]
    _assert_fails_with_evidence(_drift(live, mutate), "C13", mentions=["MICROSCOPE_ARM"])


def test_c14_detects_a_declared_paradigm_with_no_handler(live):
    """A command the UI offers and firmware silently drops."""
    def mutate(c):
        for spec in c.schema["python"]["commands"]:
            if spec["name"] == "SESSION_START":
                spec["paradigms"] = ["pavlovian"]
        for sketch in c.schema["firmware"]["sketches"]:
            if sketch["name"] == "pavlovian":
                sketch["cmd_refs"] = [r for r in sketch["cmd_refs"] if r != "SESSION_START"]
        c.schema["firmware"]["common_dispatcher_cmd_refs"] = [
            r for r in c.schema["firmware"]["common_dispatcher_cmd_refs"] if r != "SESSION_START"
        ]
    _assert_fails_with_evidence(_drift(live, mutate), "C14", mentions=["SESSION_START", "pavlovian"])


def test_c14_honours_a_recorded_exemption(live):
    """A justified gap must not be reported as new drift on every run."""
    def mutate(c):
        c.schema["python"]["known_firmware_gaps"]["SESSION_START"] = {
            "paradigms": ["pavlovian"], "reason": "test fixture",
        }
        for spec in c.schema["python"]["commands"]:
            if spec["name"] == "SESSION_START":
                spec["paradigms"] = ["pavlovian"]
        for sketch in c.schema["firmware"]["sketches"]:
            if sketch["name"] == "pavlovian":
                sketch["cmd_refs"] = [r for r in sketch["cmd_refs"] if r != "SESSION_START"]
        c.schema["firmware"]["common_dispatcher_cmd_refs"] = [
            r for r in c.schema["firmware"]["common_dispatcher_cmd_refs"] if r != "SESSION_START"
        ]
    assert _result(_drift(live, mutate), "C14")["status"] == Status.PASS.value


def test_l8_detects_a_device_name_firmware_never_emits(live):
    """The exact shape of the LICK_CIRCUIT duplicate-row bug."""
    ctx = _drift(live, lambda c: c.schema["python"]["command_state_map_devices"].append("LICK_CIRCUIT"))
    result = _assert_fails_with_evidence(ctx, "L8", mentions=["LICK_CIRCUIT"])
    assert "LICK" in result["fix_hint"], "the hint should name the correct level-000 spelling"


# --- Pin / board rules ----------------------------------------------------


def test_c3_detects_a_changed_set_pin_code(live):
    ctx = _drift(live, lambda c: c.pin_meta["set_pin_code"].update({"cue": 999}))
    _assert_fails_with_evidence(ctx, "C3", mentions=["cue", 999])


def test_c4_detects_a_component_missing_from_the_frontend(live):
    def mutate(c):
        c.pin_meta["component_keys"] = [k for k in c.pin_meta["component_keys"] if k != "slm"]
        c.pin_meta["component_union"] = [k for k in c.pin_meta["component_union"] if k != "slm"]
    _assert_fails_with_evidence(_drift(live, mutate), "C4", mentions=["slm"])


def test_c4_detects_the_union_and_array_disagreeing(live):
    """Two hand-written lists of one fact, inside a single file."""
    ctx = _drift(live, lambda c: c.pin_meta["component_union"].append("phantom"))
    _assert_fails_with_evidence(ctx, "C4", mentions=["phantom", "Component_union"])


def test_c5a_detects_a_flipped_pwm_flag(live):
    ctx = _drift(live, lambda c: c.pin_meta["requires_pwm"].update({"cue": False}))
    _assert_fails_with_evidence(ctx, "C5a", mentions=["cue"])


def test_c5b_detects_a_flipped_pcint_flag(live):
    ctx = _drift(live, lambda c: c.pin_meta["requires_pcint"].update({"slm": False}))
    _assert_fails_with_evidence(ctx, "C5b", mentions=["slm"])


def test_c5c_fires_when_a_component_starts_requiring_an_interrupt(live):
    """Harmless today because nothing sets it; must fail loudly the day one does."""
    def mutate(c):
        c.schema["python"]["pin_constraints"][0]["requires_interrupt"] = True
    result = _assert_fails_with_evidence(_drift(live, mutate), "C5c")
    assert "COMPONENT_REQUIRES_INTERRUPT" in result["fix_hint"]


def test_c6_detects_a_default_pin_drifting_from_firmware(live):
    ctx = _drift(live, lambda c: c.pin_meta["default_pin"].update({"cue": 12}))
    _assert_fails_with_evidence(ctx, "C6", mentions=["cue", 12])


def test_c7_detects_the_inclusive_range_off_by_one(live):
    """The mistake a naive text comparison makes on every board set."""
    def mutate(c):
        c.pin_meta["pin_sets"]["uno"]["digital"] = list(range(2, 13))  # dropped pin 13
    _assert_fails_with_evidence(_drift(live, mutate), "C7", mentions=["uno.digital", 13])


def test_c8_passes_at_two_boards_but_records_the_latent_risk(live):
    """A binary ternary is correct now; the check should say so and still warn."""
    result = _result(live, "C8")
    assert result["status"] == Status.PASS.value
    if result["evidence"].get("latent_risk"):
        assert "Record<BoardType" in result["fix_hint"]


def test_c8_fails_once_a_third_board_exists(live):
    """The latent trap becomes a failure at exactly the moment it starts to matter."""
    def mutate(c):
        c.schema["python"]["board_profiles"].append(
            {"board_id": "nano", "display_name": "Nano", "fqbn": "x", "avrdude_args": []}
        )
    _assert_fails_with_evidence(_drift(live, mutate), "C8", mentions=["digitalPinsFor", "nano"])


def test_c9_detects_a_board_missing_from_the_frontend_union(live):
    def mutate(c):
        c.schema["python"]["board_profiles"].append(
            {"board_id": "nano", "display_name": "Nano", "fqbn": "x", "avrdude_args": []}
        )
    _assert_fails_with_evidence(_drift(live, mutate), "C9", mentions=["nano"])


# --- Degradation: UNAVAILABLE is never PASS -------------------------------


def test_missing_frontend_yields_unavailable_not_pass():
    """The single most important property of the status vocabulary."""
    out = run(CheckContext(schema=None, pin_meta=None), scopes=["pins"])
    assert out["results"], "no pin checks ran"
    assert all(r["status"] == Status.UNAVAILABLE.value for r in out["results"])
    assert not out["ok"], "a run that verified nothing must not report ok"
    assert out["counts"]["pass"] == 0


def test_missing_firmware_marks_firmware_checks_unavailable(live):
    ctx = _drift(live, lambda c: c.schema["firmware"].update({"present": False}))
    out = run(ctx, scopes=["firmware"])
    assert out["results"]
    assert all(r["status"] == Status.UNAVAILABLE.value for r in out["results"])
    assert all("firmware" in r["fix_hint"] for r in out["results"])


def test_summary_reports_unavailable_alongside_passes():
    """'12 passed' is a misleading headline when six could not run."""
    out = run(CheckContext(schema=None, pin_meta=None))
    assert "UNAVAILABLE (not verified)" in out["summary"]
    assert out["ok"] is False


def test_a_broken_check_errors_without_masking_the_others(live):
    """One bad rule must not take the suite down with it."""
    ctx = _drift(live, lambda c: c.schema["python"].pop("pin_constraints"))
    out = run(ctx, scopes=["pins"])
    statuses = {r["status"] for r in out["results"]}
    assert Status.ERROR.value in statuses
    assert not out["verified"]
    for result in out["results"]:
        if result["status"] == Status.ERROR.value:
            assert "not as passing" in result["fix_hint"]
