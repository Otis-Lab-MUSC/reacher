"""Ending a session mid-reward must leave no output driven.

Field report: a heavy presser's session ended on an active press and the cue
kept sounding afterwards. ``EndSession()`` silenced the cue and pump once, but
the sketches keep devices armed for ``SCOPE_DRAIN_MS`` after it, so the next
tick re-drove the still-open window; the drain then disarmed them, and a
disarmed ``Cue``/``Pump`` never ran its off edge. Tone and relay stayed on until
the board was reset.

Each sketch is compiled for the host against ``tests/native/arduino_stub`` and
run by ``tests/native/end_session_harness.cpp``: the RH lever goes down and
stays down, the reward starts, and the host ends the session 100 ms into it.

Needs a host C++ compiler (``c++``, or ``REACHER_HOST_CXX``) and the ArduinoJson
source (``~/Arduino/libraries/ArduinoJson``, or ``ARDUINOJSON_SRC``); skips
loudly without them.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = REPO_ROOT / "firmware"
LIB_SRC = FIRMWARE / "libraries" / "REACHERDevices" / "src"
NATIVE = Path(__file__).resolve().parent / "native"
STUB = NATIVE / "arduino_stub"
HARNESS = NATIVE / "end_session_harness.cpp"

MEGA = "-D__AVR_ATmega2560__"

# (sketch, board define, press_at_ms, act_after_ms). VI only rewards a press once
# its interval has elapsed; omission rewards 20 s with no new press, so the
# held press starts its absence timer and the reward lands at press + 20 s.
CASES = [
    ("fr", MEGA, 500, 100),
    ("pr", MEGA, 500, 100),
    ("vi", MEGA, 40_000, 100),
    ("omission", MEGA, 500, 20_100),
    ("fr_lite", None, 500, 100),
    ("pr_lite", None, 500, 100),
    ("vi_lite", None, 40_000, 100),
    ("omission_lite", None, 500, 20_100),
]


def _cxx() -> str | None:
    return os.environ.get("REACHER_HOST_CXX") or shutil.which("c++")


def _arduinojson():
    env = os.environ.get("ARDUINOJSON_SRC")
    path = Path(env) if env else Path.home() / "Arduino" / "libraries" / "ArduinoJson" / "src"
    return path if (path / "ArduinoJson.h").is_file() else None


pytestmark = [
    pytest.mark.skipif(not (LIB_SRC / "Scheduler.cpp").is_file(), reason="firmware source not present"),
    pytest.mark.skipif(_cxx() is None, reason="no host c++ compiler: set REACHER_HOST_CXX"),
    pytest.mark.skipif(_arduinojson() is None, reason="ArduinoJson source not found: set ARDUINOJSON_SRC"),
]


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("end_session")
    built = {}

    def _build(sketch, define):
        if sketch in built:
            return built[sketch]
        exe = out_dir / sketch
        cxx = _cxx()
        assert cxx is not None  # pytestmark skips the module without one
        cmd = [
            *cxx.split(),
            "-std=c++17",
            "-O0",
            "-w",
            "-DARDUINOJSON_ENABLE_PROGMEM=0",
            "-DARDUINOJSON_ENABLE_ARDUINO_STRING=0",
            "-DARDUINOJSON_ENABLE_ARDUINO_STREAM=0",
            "-DARDUINOJSON_ENABLE_ARDUINO_PRINT=0",
            "-include",
            str(STUB / "json_fstr.h"),
            f"-I{STUB}",
            f"-I{LIB_SRC}",
            f"-I{FIRMWARE / sketch}",
            f"-I{_arduinojson()}",
            f'-DSKETCH="{FIRMWARE / sketch / (sketch + ".ino")}"',
            *([define] if define else []),
            str(HARNESS),
            str(STUB / "stub.cpp"),
            *sorted(str(p) for p in LIB_SRC.glob("*.cpp")),
            "-o",
            str(exe),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        built[sketch] = exe
        return exe

    return _build


def _run(exe, press_at, act_after, action):
    out = subprocess.run(
        [str(exe), str(press_at), str(act_after), action], check=True, capture_output=True, text=True
    ).stdout
    states = {}
    for line in out.splitlines():
        label, *fields = line.split()
        if fields:
            states[label] = dict(f.split("=") for f in fields)
        else:
            key, value = label.split("=")
            states[key] = value
    return states


@pytest.mark.parametrize("sketch, define, press_at, act_after", CASES, ids=[c[0] for c in CASES])
def test_session_end_mid_reward_leaves_no_output_on(build, sketch, define, press_at, act_after):
    s = _run(build(sketch, define), press_at, act_after, "stop")
    # Guard against a vacuous pass: the stop has to land inside the reward.
    assert s["before"]["cue"] == "1" and s["before"]["pump"] == "1", f"{sketch}: stop did not land mid-reward: {s}"
    assert s["controller_end"] == "1", f"{sketch}: no CONTROLLER END: {s}"
    assert s["after_3s"] == {"cue": "0", "pump": "0", "laser": "0"}, f"{sketch}: output left on after END: {s}"


def test_session_end_silences_outputs_at_once(build):
    """The outputs go off on END itself, not only once the drain window closes."""
    s = _run(build("fr", MEGA), 500, 100, "stop")
    assert s["after_5ms"] == {"cue": "0", "pump": "0", "laser": "0"}, s


def test_disarm_mid_reward_silences_cue_and_pump(build):
    """CUE_DISARM / PUMP_DISARM during a reward: a disarmed device must not stay on."""
    s = _run(build("fr", MEGA), 500, 100, "disarm")
    assert s["before"]["cue"] == "1" and s["before"]["pump"] == "1", s
    assert s["after_3s"]["cue"] == "0" and s["after_3s"]["pump"] == "0", s
