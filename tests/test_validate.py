"""Tests for the hardcoded config rule engine — one test per rule."""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.api.routers.validators import ValidateConfigRequest, ValidationWarning, run_validation

AUTH = {"Authorization": f"Bearer {API_KEY}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    with patch("reacher.session_manager.REACHER"), patch("os.makedirs"):
        app = create_app()
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(**kwargs) -> ValidateConfigRequest:
    return ValidateConfigRequest(**kwargs)


def _has(result, field: str, severity: str) -> bool:
    return any(w.field == field and w.severity == severity for w in result.warnings)


def _pump_ok() -> dict:
    return {"primaryPump": {"armed": True, "duration": 100}}


def _lever_pump_ok() -> dict:
    return {
        "rhLever": {"armed": True, "timeout": 0, "ratio": 1},
        "lhLever": {"armed": False},
        "primaryPump": {"armed": True, "duration": 100},
    }


def _pav_base() -> dict:
    """Minimal valid Pavlovian pavlovianParams — all required fields set correctly."""
    return {
        "206": 80, "207": 0,
        "208": 10, "209": 10,
        "210": 8000, "211": 4000,
        "213": 2000, "214": 500,
        "216": 10000, "217": 8000, "218": 15000,
        "374": 0, "375": 0, "384": 0, "385": 0,
    }


# ---------------------------------------------------------------------------
# HTTP layer: auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_requires_auth(self, client):
        resp = client.post("/api/validate/config", json={})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# HTTP layer: exception fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_returns_empty_on_rule_engine_error(self, client):
        with patch("reacher.api.routers.validate.run_validation", side_effect=RuntimeError("bug")):
            resp = client.post("/api/validate/config", json={}, headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["warnings"] == []


# ---------------------------------------------------------------------------
# Rule 1–2: FR required fields
# ---------------------------------------------------------------------------

class TestFRRules:
    def test_rule_1_ratio_lt_1(self):
        req = _req(paradigm="fr", paradigmSettings={"ratio": 0}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.ratio", "error")

    def test_rule_1_ratio_none(self):
        req = _req(paradigm="fr", paradigmSettings={}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.ratio", "error")

    def test_rule_2_no_lever(self):
        req = _req(
            paradigm="fr",
            paradigmSettings={"ratio": 1},
            hardwareUi={"rhLever": {"armed": False}, "lhLever": {"armed": False}, **_pump_ok()},
        )
        assert _has(run_validation(req), "hardwareUi.lever", "error")

    def test_rule_2_lh_lever_satisfies(self):
        req = _req(
            paradigm="fr",
            paradigmSettings={"ratio": 1},
            hardwareUi={"rhLever": {"armed": False}, "lhLever": {"armed": True}, **_pump_ok()},
        )
        assert not _has(run_validation(req), "hardwareUi.lever", "error")


# ---------------------------------------------------------------------------
# Rules 4–6: PR required fields
# ---------------------------------------------------------------------------

class TestPRRules:
    def test_rule_4_ratio_lt_1(self):
        req = _req(paradigm="pr", paradigmSettings={"ratio": 0, "step": 1}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.ratio", "error")

    def test_rule_5_step_zero(self):
        req = _req(paradigm="pr", paradigmSettings={"ratio": 1, "step": 0}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.step", "error")

    def test_rule_5_step_none(self):
        req = _req(paradigm="pr", paradigmSettings={"ratio": 1}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.step", "error")

    def test_rule_6_no_lever(self):
        req = _req(
            paradigm="pr",
            paradigmSettings={"ratio": 1, "step": 1},
            hardwareUi={"rhLever": {"armed": False}, "lhLever": {"armed": False}, **_pump_ok()},
        )
        assert _has(run_validation(req), "hardwareUi.lever", "error")


# ---------------------------------------------------------------------------
# Rules 8–9: VI required fields
# ---------------------------------------------------------------------------

class TestVIRules:
    def test_rule_8_interval_zero(self):
        req = _req(paradigm="vi", paradigmSettings={"interval": 0}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.interval", "error")

    def test_rule_8_interval_none(self):
        req = _req(paradigm="vi", paradigmSettings={}, hardwareUi=_lever_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.interval", "error")

    def test_rule_9_no_lever(self):
        req = _req(
            paradigm="vi",
            paradigmSettings={"interval": 30000},
            hardwareUi={"rhLever": {"armed": False}, "lhLever": {"armed": False}, **_pump_ok()},
        )
        assert _has(run_validation(req), "hardwareUi.lever", "error")


# ---------------------------------------------------------------------------
# Rule 11: Omission required fields (levers optional)
# ---------------------------------------------------------------------------

class TestOmissionRules:
    def test_rule_11_interval_zero(self):
        req = _req(paradigm="omission", paradigmSettings={"interval": 0}, hardwareUi=_pump_ok())
        assert _has(run_validation(req), "paradigmSettings.interval", "error")

    def test_omission_no_lever_is_fine(self):
        req = _req(
            paradigm="omission",
            paradigmSettings={"interval": 30000},
            hardwareUi={"rhLever": {"armed": False}, "lhLever": {"armed": False}, **_pump_ok()},
        )
        assert not _has(run_validation(req), "hardwareUi.lever", "error")


# ---------------------------------------------------------------------------
# Rules 13–17: Pavlovian required fields
# ---------------------------------------------------------------------------

class TestPavlovianRequiredFields:
    def _base_hw(self):
        return {"primaryPump": {"armed": True, "duration": 100}}

    def test_rule_13_cs_plus_count_zero(self):
        pav = {**_pav_base(), "208": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.csPlus", "error")

    def test_rule_14_cs_minus_count_zero(self):
        pav = {**_pav_base(), "209": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.csMinus", "error")

    def test_rule_15_total_trial_count_over_128(self):
        pav = {**_pav_base(), "208": 100, "209": 50}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.trialCount", "error")

    def test_rule_16_iti_min_gt_mean(self):
        pav = {**_pav_base(), "217": 9000, "216": 5000, "218": 15000}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.iti", "error")

    def test_rule_16_iti_mean_gt_max(self):
        pav = {**_pav_base(), "217": 2000, "216": 16000, "218": 10000}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.iti", "error")

    def test_rule_16_iti_min_zero(self):
        pav = {**_pav_base(), "217": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.iti", "error")

    def test_rule_17_cue_duration_zero(self):
        pav = {**_pav_base(), "213": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._base_hw())
        assert _has(run_validation(req), "pavlovianParams.cueDuration", "error")


# ---------------------------------------------------------------------------
# Rules 19–21: pump hardware
# ---------------------------------------------------------------------------

class TestPumpRules:
    def test_rule_21_no_pump_armed(self):
        req = _req(hardwareUi={"primaryPump": {"armed": False}, "secondaryPump": {"armed": False}})
        assert _has(run_validation(req), "pump", "error")

    def test_rule_19_primary_pump_duration_zero(self):
        req = _req(hardwareUi={"primaryPump": {"armed": True, "duration": 0}})
        assert _has(run_validation(req), "primaryPump.duration", "error")

    def test_rule_20_secondary_pump_duration_zero(self):
        req = _req(hardwareUi={
            "primaryPump": {"armed": True, "duration": 100},
            "secondaryPump": {"armed": True, "duration": 0},
        })
        assert _has(run_validation(req), "secondaryPump.duration", "error")

    def test_no_pump_error_when_one_armed(self):
        req = _req(hardwareUi={"primaryPump": {"armed": True, "duration": 100}})
        assert not _has(run_validation(req), "pump", "error")


# ---------------------------------------------------------------------------
# Rules 22–25: cue hardware
# ---------------------------------------------------------------------------

class TestCueRules:
    def test_rule_22_primary_cue_freq_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "primaryCue": {"armed": True, "frequency": 0, "duration": 500}})
        assert _has(run_validation(req), "primaryCue.frequency", "warning")

    def test_rule_24_primary_cue_duration_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "primaryCue": {"armed": True, "frequency": 8000, "duration": 0}})
        assert _has(run_validation(req), "primaryCue.duration", "warning")

    def test_rule_23_secondary_cue_freq_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "secondaryCue": {"armed": True, "frequency": 0, "duration": 500}})
        assert _has(run_validation(req), "secondaryCue.frequency", "warning")

    def test_rule_25_secondary_cue_duration_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "secondaryCue": {"armed": True, "frequency": 4000, "duration": 0}})
        assert _has(run_validation(req), "secondaryCue.duration", "warning")

    def test_no_cue_warning_when_disarmed(self):
        req = _req(hardwareUi={**_pump_ok(), "primaryCue": {"armed": False, "frequency": 0, "duration": 0}})
        assert not _has(run_validation(req), "primaryCue.frequency", "warning")


# ---------------------------------------------------------------------------
# Rules 26–28: laser hardware
# ---------------------------------------------------------------------------

class TestLaserRules:
    def test_rule_26_laser_freq_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "laser": {"armed": True, "frequency": 0, "duration": 100, "mode": "contingent"}})
        assert _has(run_validation(req), "laser.frequency", "error")

    def test_rule_27_laser_duration_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "laser": {"armed": True, "frequency": 40, "duration": 0, "mode": "contingent"}})
        assert _has(run_validation(req), "laser.duration", "error")

    def test_rule_28_cs_mode_on_operant(self):
        for mode in ("cs_plus", "cs_minus", "cs_both"):
            req = _req(
                paradigm="fr",
                hardwareUi={**_pump_ok(), "laser": {"armed": True, "frequency": 40, "duration": 100, "mode": mode}},
            )
            assert _has(run_validation(req), "laser.mode", "error"), f"expected error for mode={mode}"

    def test_rule_28_cs_mode_ok_on_pavlovian(self):
        req = _req(
            paradigm="pavlovian",
            pavlovianParams=_pav_base(),
            hardwareUi={**_pump_ok(), "laser": {"armed": True, "frequency": 40, "duration": 100, "mode": "cs_plus"}},
        )
        assert not _has(run_validation(req), "laser.mode", "error")


# ---------------------------------------------------------------------------
# Rule 29: microscope
# ---------------------------------------------------------------------------

class TestMicroscopeRules:
    def test_rule_29_frame_rate_none(self):
        req = _req(hardwareUi={**_pump_ok(), "microscope": {"armed": True, "frameRate": None}})
        assert _has(run_validation(req), "microscope.frameRate", "warning")

    def test_rule_29_frame_rate_zero(self):
        req = _req(hardwareUi={**_pump_ok(), "microscope": {"armed": True, "frameRate": 0}})
        assert _has(run_validation(req), "microscope.frameRate", "warning")

    def test_no_warning_when_frame_rate_set(self):
        req = _req(hardwareUi={**_pump_ok(), "microscope": {"armed": True, "frameRate": 30}})
        assert not _has(run_validation(req), "microscope.frameRate", "warning")


# ---------------------------------------------------------------------------
# Rules 30–33: session limits
# ---------------------------------------------------------------------------

class TestLimitRules:
    def test_rule_30_time_limit_zero(self):
        req = _req(limitSettings={"limitType": "Time", "timeLimit": 0, "infusionLimit": 0})
        assert _has(run_validation(req), "limitSettings.timeLimit", "error")

    def test_rule_30_both_limit_time_zero(self):
        req = _req(limitSettings={"limitType": "Both", "timeLimit": 0, "infusionLimit": 10})
        assert _has(run_validation(req), "limitSettings.timeLimit", "error")

    def test_rule_31_infusion_limit_zero(self):
        req = _req(limitSettings={"limitType": "Infusion", "timeLimit": 0, "infusionLimit": 0})
        assert _has(run_validation(req), "limitSettings.infusionLimit", "error")

    def test_rule_31_both_limit_infusion_zero(self):
        req = _req(limitSettings={"limitType": "Both", "timeLimit": 3600, "infusionLimit": 0})
        assert _has(run_validation(req), "limitSettings.infusionLimit", "error")

    def test_rule_32_trials_limit_on_operant(self):
        for paradigm in ("fr", "pr", "vi", "omission"):
            req = _req(paradigm=paradigm, limitSettings={"limitType": "Trials", "timeLimit": 0, "infusionLimit": 20})
            assert _has(run_validation(req), "limitSettings.limitType", "warning"), f"expected warning for {paradigm}"

    def test_rule_32_trials_limit_ok_on_pavlovian(self):
        req = _req(
            paradigm="pavlovian",
            pavlovianParams=_pav_base(),
            hardwareUi={"primaryPump": {"armed": True, "duration": 100}},
            limitSettings={"limitType": "Trials", "infusionLimit": 20},
        )
        assert not _has(run_validation(req), "limitSettings.limitType", "warning")

    def test_rule_33_time_over_4_hours(self):
        req = _req(limitSettings={"limitType": "Time", "timeLimit": 14401, "infusionLimit": 0})
        assert _has(run_validation(req), "limitSettings.timeLimit", "warning")

    def test_rule_33_exactly_4_hours_is_fine(self):
        req = _req(limitSettings={"limitType": "Time", "timeLimit": 14400, "infusionLimit": 0})
        assert not _has(run_validation(req), "limitSettings.timeLimit", "warning")


# ---------------------------------------------------------------------------
# Rules 34–36: temporal ordering
# ---------------------------------------------------------------------------

class TestTemporalRules:
    def test_rule_34_trace_exceeds_time_limit(self):
        req = _req(
            paradigmSettings={"traceInterval": 3_700_000},  # ms — over 1 hour
            limitSettings={"limitType": "Time", "timeLimit": 3600},  # 3600 s = 3,600,000 ms
        )
        assert _has(run_validation(req), "paradigmSettings.traceInterval", "error")

    def test_rule_34_trace_within_limit_is_fine(self):
        req = _req(
            paradigmSettings={"traceInterval": 500},
            limitSettings={"limitType": "Time", "timeLimit": 3600},
        )
        assert not _has(run_validation(req), "paradigmSettings.traceInterval", "error")

    def test_rule_35_rh_lever_timeout_exceeds_limit(self):
        req = _req(
            hardwareUi={"rhLever": {"armed": True, "timeout": 3_700_000}, **_pump_ok()},
            limitSettings={"limitType": "Time", "timeLimit": 3600},
        )
        assert _has(run_validation(req), "hardwareUi.rhLever.timeout", "warning")

    def test_rule_36_lh_lever_timeout_exceeds_limit(self):
        req = _req(
            hardwareUi={"lhLever": {"armed": True, "timeout": 3_700_000}, **_pump_ok()},
            limitSettings={"limitType": "Time", "timeLimit": 3600},
        )
        assert _has(run_validation(req), "hardwareUi.lhLever.timeout", "warning")

    def test_temporal_skipped_for_non_time_limit(self):
        req = _req(
            paradigmSettings={"traceInterval": 99_999_999},
            limitSettings={"limitType": "Infusion", "infusionLimit": 50},
        )
        assert not _has(run_validation(req), "paradigmSettings.traceInterval", "error")


# ---------------------------------------------------------------------------
# Rules 37–43: Pavlovian-specific
# ---------------------------------------------------------------------------

class TestPavlovianSpecificRules:
    def _hw(self):
        return {"primaryPump": {"armed": True, "duration": 100}}

    def test_rule_37_both_reward_probs_zero(self):
        pav = {**_pav_base(), "206": 0, "207": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.rewardProbability", "error")

    def test_rule_38_probs_sum_over_100(self):
        pav = {**_pav_base(), "206": 70, "207": 50}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.rewardProbability", "warning")

    def test_rule_39_identical_tone_frequencies(self):
        pav = {**_pav_base(), "210": 8000, "211": 8000}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.csFrequency", "warning")

    def test_rule_39_no_warning_when_freqs_differ(self):
        pav = {**_pav_base(), "210": 8000, "211": 4000}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert not _has(run_validation(req), "pavlovianParams.csFrequency", "warning")

    def test_rule_40_cue_duration_exceeds_iti_min(self):
        pav = {**_pav_base(), "213": 10000, "217": 8000}  # cue=10s > iti_min=8s
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.cueDuration", "warning")

    def test_rule_41_cue_plus_trace_exceeds_iti_min(self):
        pav = {**_pav_base(), "213": 6000, "214": 4000, "217": 8000}  # 6000+4000=10000 > 8000
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.traceInterval", "warning")

    def test_rule_42_cs_plus_pulse_on_set_off_zero(self):
        pav = {**_pav_base(), "374": 200, "375": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.csPlusPulse", "warning")

    def test_rule_42_no_warning_when_pulse_off_key_absent(self):
        pav = {k: v for k, v in _pav_base().items() if k not in ("374", "375")}
        pav["374"] = 200  # pulse_on set, but 375 key is absent entirely
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert not _has(run_validation(req), "pavlovianParams.csPlusPulse", "warning")

    def test_rule_42_no_warning_when_both_nonzero(self):
        pav = {**_pav_base(), "374": 200, "375": 200}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert not _has(run_validation(req), "pavlovianParams.csPlusPulse", "warning")

    def test_rule_43_cs_minus_pulse_on_set_off_zero(self):
        pav = {**_pav_base(), "384": 200, "385": 0}
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert _has(run_validation(req), "pavlovianParams.csMinusPulse", "warning")

    def test_rule_43_no_warning_when_pulse_off_key_absent(self):
        pav = {k: v for k, v in _pav_base().items() if k not in ("384", "385")}
        pav["384"] = 200
        req = _req(paradigm="pavlovian", pavlovianParams=pav, hardwareUi=self._hw())
        assert not _has(run_validation(req), "pavlovianParams.csMinusPulse", "warning")

    def test_pavlovian_rules_not_run_for_operant(self):
        req = _req(paradigm="fr", paradigmSettings={"ratio": 1}, hardwareUi=_lever_pump_ok())
        result = run_validation(req)
        pav_fields = {w.field for w in result.warnings if w.field.startswith("pavlovianParams")}
        assert pav_fields == set()


# ---------------------------------------------------------------------------
# Happy path: valid config per paradigm produces no errors
# ---------------------------------------------------------------------------

class TestHappyPath:
    def _base_hw(self, lever=True) -> dict:
        hw = {
            "primaryPump": {"armed": True, "duration": 100},
            "secondaryPump": {"armed": False},
            "primaryCue": {"armed": False},
            "secondaryCue": {"armed": False},
            "laser": {"armed": False},
            "lickCircuit": {"armed": False},
            "microscope": {"armed": False},
        }
        if lever:
            hw["rhLever"] = {"armed": True, "timeout": 0, "ratio": 1}
            hw["lhLever"] = {"armed": False}
        else:
            hw["rhLever"] = {"armed": False}
            hw["lhLever"] = {"armed": False}
        return hw

    def _time_limit(self) -> dict:
        return {"limitType": "Time", "timeLimit": 3600, "infusionLimit": 0, "delay": 0}

    def test_fr_valid(self):
        req = _req(
            paradigm="fr",
            paradigmSettings={"ratio": 1, "traceInterval": 0},
            hardwareUi=self._base_hw(),
            limitSettings=self._time_limit(),
        )
        result = run_validation(req)
        assert result.valid is True
        assert result.warnings == []

    def test_pr_valid(self):
        req = _req(
            paradigm="pr",
            paradigmSettings={"ratio": 1, "step": 1, "traceInterval": 0},
            hardwareUi=self._base_hw(),
            limitSettings=self._time_limit(),
        )
        result = run_validation(req)
        assert result.valid is True
        assert result.warnings == []

    def test_vi_valid(self):
        req = _req(
            paradigm="vi",
            paradigmSettings={"interval": 30000, "traceInterval": 0},
            hardwareUi=self._base_hw(),
            limitSettings=self._time_limit(),
        )
        result = run_validation(req)
        assert result.valid is True
        assert result.warnings == []

    def test_omission_valid(self):
        req = _req(
            paradigm="omission",
            paradigmSettings={"interval": 30000},
            hardwareUi=self._base_hw(lever=False),
            limitSettings=self._time_limit(),
        )
        result = run_validation(req)
        assert result.valid is True
        assert result.warnings == []

    def test_pavlovian_valid(self):
        req = _req(
            paradigm="pavlovian",
            pavlovianParams=_pav_base(),
            hardwareUi=self._base_hw(lever=False),
            limitSettings={"limitType": "Trials", "timeLimit": 0, "infusionLimit": 20, "delay": 0},
        )
        result = run_validation(req)
        assert result.valid is True
        assert result.warnings == []
