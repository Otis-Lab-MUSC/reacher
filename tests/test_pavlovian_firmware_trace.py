"""The real pavlovian sketch, run on the host, against what the host parses.

``test_golden_traces.py`` compares firmware with the simulator for the operant
sketches. Pavlovian draws its trial order, rewards and ITIs from the board's
``random()``, which the simulator does not share, so a field-by-field stream
comparison cannot apply. These checks hold regardless of the draw:

* every serial line the sketch prints is valid JSON. The host drops a line it
  cannot parse, and a PAVLOV line with a stray quote after its numeric
  timestamp lost every TRACE_START, REWARD_* and ALL_TRIALS_COMPLETE event on a
  real board, so auto-stop never fired;
* the session reaches ALL_TRIALS_COMPLETE with one outcome per trial;
* counterbalance, sent with the payload the host builds for command 212, moves
  the CS+ tone onto CUE_2.

Needs a host C++ compiler and the ArduinoJson source, like
test_end_session_outputs.py; skips loudly without them.
"""

import json
import subprocess

import pytest

from reacher.kernel.commands import CommandCode, build_command_payload
from tests.test_end_session_outputs import LIB_SRC, _arduinojson, _cxx
from tests.test_golden_traces import Script, _build

pytestmark = [
    pytest.mark.skipif(not (LIB_SRC / "Scheduler.cpp").is_file(), reason="firmware source not present"),
    pytest.mark.skipif(_cxx() is None, reason="no host c++ compiler: set REACHER_HOST_CXX"),
    pytest.mark.skipif(_arduinojson() is None, reason="ArduinoJson source not found: set ARDUINOJSON_SRC"),
]

#: Cue, cue 2, pump, pump 2 armed (Pavlovian boots disarmed).
_ARM = [{"cmd": 301}, {"cmd": 311}, {"cmd": 401}, {"cmd": 411}]

#: Three short trials: 2 CS+ (always rewarded), 1 CS- (never), 1-2 s ITIs.
_CONFIG = [
    {"cmd": 206, "probability": 100}, {"cmd": 207, "probability": 0},
    {"cmd": 208, "count": 2}, {"cmd": 209, "count": 1},
    {"cmd": 216, "iti_mean": 1500},
    {"cmd": 217, "iti_min": 1000}, {"cmd": 218, "iti_max": 2000},
]

#: Long enough for three trials (cue 2 s + trace 1 s + consumption 3 s + ITI).
_STOP_MS = 40_000


@pytest.fixture(scope="module")
def build_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("pavlovian_trace")


def _raw_lines(script: Script, build_dir) -> list[str]:
    exe = _build(script.sketch, build_dir)
    path = build_dir / f"pav-{id(script)}.txt"
    path.write_text(script.harness_text())
    out = subprocess.run([str(exe), str(path)], check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.startswith("{")]


def _events(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines]


def _pav(events: list[dict], event: str) -> list[dict]:
    return [e for e in events if e.get("device") == "PAVLOV" and e.get("event") == event]


def test_config_codes_are_the_registry_codes():
    """Guards the literal codes in _CONFIG against a registry renumbering."""
    assert [c["cmd"] for c in _CONFIG] == [
        CommandCode.PAV_CS_PLUS_PROB, CommandCode.PAV_CS_MINUS_PROB,
        CommandCode.PAV_CS_PLUS_COUNT, CommandCode.PAV_CS_MINUS_COUNT,
        CommandCode.PAV_ITI_MEAN, CommandCode.PAV_ITI_MIN, CommandCode.PAV_ITI_MAX,
    ]


def test_every_serial_line_is_valid_json(build_dir):
    lines = _raw_lines(Script("pavlovian", pre=[*_ARM, *_CONFIG], stop=_STOP_MS), build_dir)
    bad = []
    for line in lines:
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            bad.append(f"{exc}: {line}")
    assert not bad, "the host would drop these lines:\n" + "\n".join(bad)


def test_session_completes_with_one_outcome_per_trial(build_dir):
    events = _events(_raw_lines(Script("pavlovian", pre=[*_ARM, *_CONFIG], stop=_STOP_MS), build_dir))
    starts = _pav(events, "TRIAL_START")
    assert len(starts) == 3, f"expected 3 trials, got {len(starts)}"
    assert len(_pav(events, "TRACE_START")) == 3
    delivered, omitted = _pav(events, "REWARD_DELIVERED"), _pav(events, "REWARD_OMITTED")
    assert len(delivered) == 2 and len(omitted) == 1, (delivered, omitted)
    assert len(_pav(events, "ALL_TRIALS_COMPLETE")) == 1


def _cs_plus_tone_devices(events: list[dict]) -> set[str]:
    tones = {e["start_timestamp"]: e["device"] for e in events if e.get("event") == "TONE"}
    return {
        tones[t["timestamp"]]
        for t in _pav(events, "TRIAL_START")
        if t["trial_type"] == "CS_PLUS" and t["timestamp"] in tones
    }


def test_counterbalance_off_plays_cs_plus_on_cue(build_dir):
    events = _events(_raw_lines(Script("pavlovian", pre=[*_ARM, *_CONFIG], stop=_STOP_MS), build_dir))
    assert _cs_plus_tone_devices(events) == {"CUE"}


def test_counterbalance_from_the_host_payload_swaps_cues(build_dir):
    host = build_command_payload(int(CommandCode.PAV_COUNTERBALANCE), True)
    events = _events(_raw_lines(Script("pavlovian", pre=[*_ARM, *_CONFIG, host], stop=_STOP_MS), build_dir))
    assert _cs_plus_tone_devices(events) == {"CUE_2"}
