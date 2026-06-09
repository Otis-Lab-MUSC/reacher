"""Pure-Python session config rule engine — replaces the Ollama validator."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic models  (API contract — imported by validate.py)
# ---------------------------------------------------------------------------

class ValidateConfigRequest(BaseModel):
    paradigm: Optional[str] = None
    paradigmSettings: Optional[dict[str, Any]] = None
    hardwareUi: Optional[dict[str, Any]] = None
    pavlovianParams: Optional[dict[str, Any]] = None
    limitSettings: Optional[dict[str, Any]] = None


class ValidationWarning(BaseModel):
    field: str
    message: str
    severity: str  # "warning" | "error"


class ValidateConfigResponse(BaseModel):
    valid: bool
    warnings: list[ValidationWarning]
    suggestions: str


_EMPTY = ValidateConfigResponse(valid=True, warnings=[], suggestions="")


# ---------------------------------------------------------------------------
# Accessor helpers — safe reads into Optional[dict] fields
# ---------------------------------------------------------------------------

def _hw(req: ValidateConfigRequest, device: str, field: str, default: Any = None) -> Any:
    return ((req.hardwareUi or {}).get(device) or {}).get(field, default)


def _ps(req: ValidateConfigRequest, field: str, default: Any = None) -> Any:
    return (req.paradigmSettings or {}).get(field, default)


def _pav(req: ValidateConfigRequest, code: str, default: int = 0) -> int:
    return (req.pavlovianParams or {}).get(code, default)


def _lim(req: ValidateConfigRequest, field: str, default: Any = None) -> Any:
    return (req.limitSettings or {}).get(field, default)


def _w(field: str, msg: str) -> ValidationWarning:
    return ValidationWarning(field=field, message=msg, severity="warning")


def _e(field: str, msg: str) -> ValidationWarning:
    return ValidationWarning(field=field, message=msg, severity="error")


def _any_lever_armed(req: ValidateConfigRequest) -> bool:
    return bool(_hw(req, "rhLever", "armed") or _hw(req, "lhLever", "armed"))


def _any_pump_armed(req: ValidateConfigRequest) -> bool:
    return bool(_hw(req, "primaryPump", "armed") or _hw(req, "secondaryPump", "armed"))


def _has_time_component(limit_type: str) -> bool:
    return limit_type in ("Time", "Both")


def _has_infusion_component(limit_type: str) -> bool:
    return limit_type in ("Infusion", "Both")


# ---------------------------------------------------------------------------
# Rule group 1: paradigm required fields  (rules 1–18)
# ---------------------------------------------------------------------------

def _check_paradigm(req: ValidateConfigRequest) -> list[ValidationWarning]:
    p = req.paradigm
    warnings: list[ValidationWarning] = []

    if p == "fr":
        if (_ps(req, "ratio") or 0) < 1:
            warnings.append(_e("paradigmSettings.ratio", "FR ratio must be ≥ 1"))
        if not _any_lever_armed(req):
            warnings.append(_e("hardwareUi.lever", "FR requires at least one lever armed"))

    elif p == "pr":
        if (_ps(req, "ratio") or 0) < 1:
            warnings.append(_e("paradigmSettings.ratio", "PR ratio must be ≥ 1"))
        if (_ps(req, "step") or 0) < 1:
            warnings.append(_e("paradigmSettings.step", "PR step must be ≥ 1 — step=0 silently degrades PR to FR"))
        if not _any_lever_armed(req):
            warnings.append(_e("hardwareUi.lever", "PR requires at least one lever armed"))

    elif p == "vi":
        interval = _ps(req, "interval")
        if interval is None or interval <= 0:
            warnings.append(_e("paradigmSettings.interval", "VI interval must be > 0 — interval=0 fires rewards continuously"))
        if not _any_lever_armed(req):
            warnings.append(_e("hardwareUi.lever", "VI requires at least one lever armed"))

    elif p == "omission":
        interval = _ps(req, "interval")
        if interval is None or interval <= 0:
            warnings.append(_e("paradigmSettings.interval", "Omission interval must be > 0 — interval=0 fires rewards continuously"))

    elif p == "pavlovian":
        cs_plus = _pav(req, "208")
        cs_minus = _pav(req, "209")
        iti_min = _pav(req, "217")
        iti_mean = _pav(req, "216")
        iti_max = _pav(req, "218")
        cue_dur = _pav(req, "213")

        if cs_plus <= 0:
            warnings.append(_e("pavlovianParams.csPlus", "CS+ trial count (208) must be > 0"))
        if cs_minus <= 0:
            warnings.append(_e("pavlovianParams.csMinus", "CS- trial count (209) must be > 0"))
        if cs_plus + cs_minus > 128:
            warnings.append(_e("pavlovianParams.trialCount", "Total trial count (CS+ + CS-) exceeds the firmware limit of 128"))
        if not (iti_min > 0 and iti_mean > 0 and iti_max > 0 and iti_min <= iti_mean <= iti_max):
            warnings.append(_e("pavlovianParams.iti", "ITI values are invalid: min (217) ≤ mean (216) ≤ max (218) must hold and all must be > 0"))
        if cue_dur <= 0:
            warnings.append(_e("pavlovianParams.cueDuration", "Cue duration (213) must be > 0"))

    return warnings


# ---------------------------------------------------------------------------
# Rule group 2: hardware device rules  (rules 19–29)
# ---------------------------------------------------------------------------

def _check_hardware(req: ValidateConfigRequest) -> list[ValidationWarning]:
    warnings: list[ValidationWarning] = []

    # Rule 21: no pump armed
    if not _any_pump_armed(req):
        warnings.append(_e("pump", "No pump armed — reward delivery is impossible"))

    # Rule 19: primary pump armed but duration zero
    if _hw(req, "primaryPump", "armed") and (_hw(req, "primaryPump", "duration") or 0) == 0:
        warnings.append(_e("primaryPump.duration", "Primary pump duration is zero — no reward will be delivered"))

    # Rule 20: secondary pump armed but duration zero
    if _hw(req, "secondaryPump", "armed") and (_hw(req, "secondaryPump", "duration") or 0) == 0:
        warnings.append(_e("secondaryPump.duration", "Secondary pump duration is zero — no reward will be delivered"))

    # Rules 22, 24: primary cue
    if _hw(req, "primaryCue", "armed"):
        if (_hw(req, "primaryCue", "frequency") or 0) == 0:
            warnings.append(_w("primaryCue.frequency", "Primary cue frequency is zero — no audible tone will play"))
        if (_hw(req, "primaryCue", "duration") or 0) == 0:
            warnings.append(_w("primaryCue.duration", "Primary cue duration is zero — tone will be instantaneous"))

    # Rules 23, 25: secondary cue
    if _hw(req, "secondaryCue", "armed"):
        if (_hw(req, "secondaryCue", "frequency") or 0) == 0:
            warnings.append(_w("secondaryCue.frequency", "Secondary cue frequency is zero — no audible tone will play"))
        if (_hw(req, "secondaryCue", "duration") or 0) == 0:
            warnings.append(_w("secondaryCue.duration", "Secondary cue duration is zero — tone will be instantaneous"))

    # Rules 26–28: laser
    if _hw(req, "laser", "armed"):
        if (_hw(req, "laser", "frequency") or 0) == 0:
            warnings.append(_e("laser.frequency", "Laser frequency cannot be zero when laser is armed"))
        if (_hw(req, "laser", "duration") or 0) == 0:
            warnings.append(_e("laser.duration", "Laser duration is zero — no optogenetic stimulus will be delivered"))
        mode = _hw(req, "laser", "mode") or ""
        if mode in ("cs_plus", "cs_minus", "cs_both") and req.paradigm != "pavlovian":
            warnings.append(_e("laser.mode", "CS-filter laser modes (cs_plus, cs_minus, cs_both) are Pavlovian-only; use contingent or independent for operant paradigms"))

    # Rule 29: microscope armed without frame rate
    if _hw(req, "microscope", "armed"):
        frame_rate = _hw(req, "microscope", "frameRate")
        if frame_rate is None or frame_rate == 0:
            warnings.append(_w("microscope.frameRate", "Microscope frame rate not configured — timestamps will be unreliable"))

    return warnings


# ---------------------------------------------------------------------------
# Rule group 3: session limit conflicts  (rules 30–33)
# ---------------------------------------------------------------------------

def _check_limits(req: ValidateConfigRequest) -> list[ValidationWarning]:
    warnings: list[ValidationWarning] = []
    limit_type = _lim(req, "limitType") or ""
    time_limit = _lim(req, "timeLimit") or 0
    infusion_limit = _lim(req, "infusionLimit") or 0

    # Rule 30
    if _has_time_component(limit_type) and time_limit == 0:
        warnings.append(_e("limitSettings.timeLimit", "Time limit is zero — session would end immediately on start"))

    # Rule 31
    if _has_infusion_component(limit_type) and infusion_limit == 0:
        warnings.append(_e("limitSettings.infusionLimit", "Infusion limit is zero — session would end immediately on start"))

    # Rule 32
    if limit_type == "Trials" and req.paradigm in ("fr", "pr", "vi", "omission"):
        warnings.append(_w("limitSettings.limitType", "Trial-based limits are designed for Pavlovian paradigms; use Time or Infusion limits for operant paradigms"))

    # Rule 33
    if _has_time_component(limit_type) and time_limit > 14400:
        warnings.append(_w("limitSettings.timeLimit", "Session duration over 4 hours — animal welfare consideration"))

    return warnings


def _cue_lever_overlap(
    req: ValidateConfigRequest,
    cue_key: str,
    lever_key: str,
    cue_label: str,
    lever_label: str,
) -> list[ValidationWarning]:
    """Warn when lever timeout allows back-to-back presses that would overlap cue playback."""
    if not _hw(req, cue_key, "armed") or not _hw(req, lever_key, "armed"):
        return []
    contingency = _hw(req, cue_key, "contingency") or {}
    filter_val = {"rhLever": "rh", "lhLever": "lh"}[lever_key]
    if contingency.get("leverFilter") != filter_val:
        return []
    cue_delay = contingency.get("delay") or 0
    cue_duration = _hw(req, cue_key, "duration") or 0
    total = cue_delay + cue_duration
    if total <= 0:
        return []
    timeout = _hw(req, lever_key, "timeout") or 0
    if timeout == 0:
        return [_w(
            f"hardwareUi.{lever_key}.timeout",
            f"{lever_label} timeout is 0 — back-to-back presses will trigger overlapping "
            f"{cue_label} tones (onset delay {cue_delay}ms + duration {cue_duration}ms)",
        )]
    if timeout < total:
        return [_w(
            f"hardwareUi.{lever_key}.timeout",
            f"{lever_label} timeout ({timeout}ms) is shorter than {cue_label} onset delay + "
            f"duration ({total}ms) — rapid presses may produce overlapping cue tones",
        )]
    return []


# ---------------------------------------------------------------------------
# Rule group 4: temporal ordering conflicts  (rules 34–40)
# ---------------------------------------------------------------------------

def _check_temporal(req: ValidateConfigRequest) -> list[ValidationWarning]:
    warnings: list[ValidationWarning] = []
    limit_type = _lim(req, "limitType") or ""
    time_limit = _lim(req, "timeLimit") or 0

    if not _has_time_component(limit_type) or time_limit <= 0:
        return warnings

    time_limit_ms = time_limit * 1000

    # Rule 34: trace interval exceeds session time limit
    trace_interval = _ps(req, "traceInterval") or 0
    if trace_interval > time_limit_ms:
        warnings.append(_e(
            "paradigmSettings.traceInterval",
            "Trace interval (ms) exceeds the session time limit — the reward chain will never complete",
        ))

    # Rule 35: RH lever timeout exceeds session time limit
    if _hw(req, "rhLever", "armed") and (_hw(req, "rhLever", "timeout") or 0) > time_limit_ms:
        warnings.append(_w(
            "hardwareUi.rhLever.timeout",
            "RH lever lock duration (ms) exceeds session time limit — the timeout will never expire within the session",
        ))

    # Rule 36: LH lever timeout exceeds session time limit
    if _hw(req, "lhLever", "armed") and (_hw(req, "lhLever", "timeout") or 0) > time_limit_ms:
        warnings.append(_w(
            "hardwareUi.lhLever.timeout",
            "LH lever lock duration (ms) exceeds session time limit — the timeout will never expire within the session",
        ))

    # Rules 37–40: cue tone overlap from short lever timeouts
    for cue_k, cue_lbl in (("primaryCue", "Primary cue"), ("secondaryCue", "Secondary cue")):
        for lev_k, lev_lbl in (("rhLever", "RH lever"), ("lhLever", "LH lever")):
            warnings.extend(_cue_lever_overlap(req, cue_k, lev_k, cue_lbl, lev_lbl))

    return warnings


# ---------------------------------------------------------------------------
# Rule group 5: Pavlovian-specific rules  (rules 37–43)
# ---------------------------------------------------------------------------

def _check_pavlovian(req: ValidateConfigRequest) -> list[ValidationWarning]:
    warnings: list[ValidationWarning] = []
    pav = req.pavlovianParams or {}

    cs_plus_prob = _pav(req, "206")
    cs_minus_prob = _pav(req, "207")
    cs_plus_freq = _pav(req, "210")
    cs_minus_freq = _pav(req, "211")
    cue_dur = _pav(req, "213")
    trace = _pav(req, "214")
    iti_min = _pav(req, "217")

    # Rule 37: both reward probabilities are zero
    if cs_plus_prob == 0 and cs_minus_prob == 0:
        warnings.append(_e(
            "pavlovianParams.rewardProbability",
            "Both CS+ and CS- reward probabilities are zero — no rewards will ever be delivered",
        ))

    # Rule 38: reward probabilities sum over 100%
    if cs_plus_prob + cs_minus_prob > 100:
        warnings.append(_w(
            "pavlovianParams.rewardProbability",
            "CS+ and CS- reward probabilities sum to over 100% — reward delivery logic may behave unexpectedly",
        ))

    # Rule 39: identical tone frequencies
    if cs_plus_freq > 0 and cs_plus_freq == cs_minus_freq:
        warnings.append(_w(
            "pavlovianParams.csFrequency",
            "CS+ and CS- tone frequencies are identical — the animal cannot acoustically distinguish the two stimuli",
        ))

    # Rule 40: cue duration exceeds ITI minimum
    if iti_min > 0 and cue_dur > iti_min:
        warnings.append(_w(
            "pavlovianParams.cueDuration",
            "Cue duration exceeds minimum ITI — consecutive trials may begin before the previous cue has finished",
        ))

    # Rule 41: cue + trace exceeds ITI minimum
    if iti_min > 0 and (cue_dur + trace) > iti_min:
        warnings.append(_w(
            "pavlovianParams.traceInterval",
            "Cue duration + trace interval exceeds minimum ITI — reward delivery may extend into the next trial's ITI window",
        ))

    # Rule 42: CS+ pulse_on set but pulse_off is explicitly 0 (not absent)
    pulse_on_plus = pav.get("374", 0)
    pulse_off_plus = pav.get("375")  # None when key is absent; 0 when explicitly set
    if pulse_on_plus > 0 and pulse_off_plus is not None and pulse_off_plus == 0:
        warnings.append(_w(
            "pavlovianParams.csPlusPulse",
            "CS+ cue pulse_on is set but pulse_off is zero — the tone will sound continuously after the first pulse onset",
        ))

    # Rule 43: CS- pulse_on set but pulse_off is explicitly 0 (not absent)
    pulse_on_minus = pav.get("384", 0)
    pulse_off_minus = pav.get("385")  # None when key is absent
    if pulse_on_minus > 0 and pulse_off_minus is not None and pulse_off_minus == 0:
        warnings.append(_w(
            "pavlovianParams.csMinusPulse",
            "CS- cue pulse_on is set but pulse_off is zero — the tone will sound continuously after the first pulse onset",
        ))

    return warnings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_validation(req: ValidateConfigRequest) -> ValidateConfigResponse:
    all_warnings = (
        _check_paradigm(req)
        + _check_hardware(req)
        + _check_limits(req)
        + _check_temporal(req)
        + (_check_pavlovian(req) if req.paradigm == "pavlovian" else [])
    )
    valid = not any(w.severity == "error" for w in all_warnings)
    return ValidateConfigResponse(valid=valid, warnings=all_warnings, suggestions="")
