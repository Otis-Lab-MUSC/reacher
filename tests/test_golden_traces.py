"""Golden traces: the real firmware and the Python simulator, given one script,
must emit the same behavioural event stream.

A script of presses, licks, commands and a stop point runs through

* the sketch itself, compiled for the host against ``tests/native/arduino_stub``
  and driven by ``tests/native/trace_harness.cpp``, and
* ``FirmwareSimulator`` on its stepped clock, with the stochastic animal
  silenced so only the scripted inputs happen.

Both level-007 streams are then compared field by field. Only what is
legitimately different is normalised, each for a stated reason:

* **Input timestamps are shifted by the debounce.** ``SwitchLever`` and
  ``LickCircuit`` report an edge once the pin has been stable for more than
  their 20 ms debounce, i.e. 21 ms after the harness flips it. The simulator has
  no pins, so the runner injects each scripted press and lick 21 ms late rather
  than rewriting any timestamp afterwards.
* **Event order within one millisecond is ignored.** The firmware logs a reward
  chain's activations before the press that caused them (the press is logged on
  release); the simulator's heap orders same-time events by insertion. Events
  are compared sorted by time, device and event, never by arrival.
* **Levels other than 007 are not compared.** Config (000) and parameter-log
  (001) lines differ in wording and are not part of the event stream the host
  records.

Nothing else is normalised: devices, events, press classes, orientations, pins
and timestamps must match exactly. Where they do not, the case is an
``xfail(strict=True)`` naming the difference: a finding for the firmware owner,
not something to fix on either side here.

Every stop point also asserts output safety on the firmware run: 5 ms and 3 s
after SESSION_END, cue, cue 2, pump, pump 2, laser and AUX are all off.

Needs a host C++ compiler and the ArduinoJson source, like
test_end_session_outputs.py; skips loudly without them.
"""

import json
import queue
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from reacher.kernel.simulated_subject import SubjectProfile
from reacher.kernel.simulator import FirmwareSimulator
from tests.test_end_session_outputs import FIRMWARE, LIB_SRC, MEGA, NATIVE, STUB, _arduinojson, _cxx

HARNESS = NATIVE / "trace_harness.cpp"

#: SwitchLever / LickCircuit debounceDelay is 20 ms and the edge registers once
#: more than that has elapsed, so a pin flipped at t is reported at t + 21.
DEBOUNCE_MS = 21

#: Fields compared on every level-007 event.
FIELDS = ("device", "event", "class", "orientation", "pin", "timestamp", "start_timestamp", "end_timestamp")

#: Outputs that drive hardware, as the harness reports them.
OUTPUTS = ("cue", "cue2", "pump", "pump2", "laser", "aux")

pytestmark = [
    pytest.mark.skipif(not (LIB_SRC / "Scheduler.cpp").is_file(), reason="firmware source not present"),
    pytest.mark.skipif(_cxx() is None, reason="no host c++ compiler: set REACHER_HOST_CXX"),
    pytest.mark.skipif(_arduinojson() is None, reason="ArduinoJson source not found: set ARDUINOJSON_SRC"),
]


@dataclass
class Script:
    """One scripted session. Times are ms of session time."""

    sketch: str
    pre: list[dict] = field(default_factory=list)
    cmds: list[tuple[int, dict]] = field(default_factory=list)
    presses: list[tuple[str, int, int]] = field(default_factory=list)  # (RH|LH, at, ms)
    licks: list[tuple[int, int]] = field(default_factory=list)  # (at, ms)
    stop: int = 0
    #: Outputs that must be on just before the stop, so the safety check is not vacuous.
    on_at_stop: tuple[str, ...] = ()

    def harness_text(self) -> str:
        lines = [f"pre {json.dumps(c)}" for c in self.pre]
        lines += [f"cmd {t} {json.dumps(c)}" for t, c in self.cmds]
        lines += [f"press {side} {t} {ms}" for side, t, ms in self.presses]
        lines += [f"lick {t} {ms}" for t, ms in self.licks]
        lines.append(f"stop {self.stop}")
        if self.on_at_stop:
            lines.append(f"stop_probe {self.stop - 1}")
        return "\n".join(lines) + "\n"


_FR_DEVICES = [
    {"cmd": 371, "frequency": 8000}, {"cmd": 372, "duration": 1000},
    {"cmd": 472, "duration": 2000}, {"cmd": 1001}, {"cmd": 1301}, {"cmd": 301},
    {"cmd": 401}, {"cmd": 501},
]

SCRIPTS = {
    # FR2 with cue and pump: ratio counting, an inactive LH press, licks, and a
    # stop in the middle of the second reward.
    "fr_cue_pump": Script(
        "fr",
        pre=[*_FR_DEVICES, {"cmd": 201, "ratio": 2}],
        presses=[("RH", 500, 100), ("RH", 900, 80), ("LH", 1500, 120), ("RH", 4000, 100), ("RH", 4400, 90)],
        licks=[(1200, 40), (1300, 40), (4600, 50)],
        stop=5000,
        on_at_stop=("cue", "pump"),
    ),
    # The same on the UNO build.
    "fr_lite_cue_pump": Script(
        "fr_lite",
        pre=[*_FR_DEVICES, {"cmd": 201, "ratio": 2}],
        presses=[("RH", 500, 100), ("RH", 900, 80), ("LH", 1500, 120), ("RH", 4000, 100), ("RH", 4400, 90)],
        licks=[(1200, 40), (4600, 50)],
        stop=5000,
        on_at_stop=("cue", "pump"),
    ),
    # A 3 s lever timeout armed by every active press (mode 0): presses inside
    # the window classify TIMEOUT and do not count; stop mid-reward.
    "fr_timeout_every_press": Script(
        "fr",
        pre=[*_FR_DEVICES, {"cmd": 1074, "timeout": 3000}, {"cmd": 1077, "timeout_mode": 0}],
        presses=[("RH", 500, 100), ("RH", 1500, 100), ("RH", 2800, 100), ("RH", 4000, 100)],
        stop=4600,
        on_at_stop=("cue", "pump"),
    ),
    # Mode 1 at FR2: only the rewarded press opens the window.
    "fr_timeout_rewarded_only": Script(
        "fr",
        pre=[*_FR_DEVICES, {"cmd": 201, "ratio": 2}, {"cmd": 1074, "timeout": 3000},
             {"cmd": 1077, "timeout_mode": 1}],
        presses=[("RH", 500, 100), ("RH", 1000, 100), ("RH", 1500, 100), ("RH", 4500, 100),
                 ("RH", 5000, 100)],
        stop=5600,
        on_at_stop=("cue", "pump"),
    ),
    # Contingent laser (1 Hz, 1 s train) and pump 2 as the reward pump: stop
    # inside the first laser pulse and the infusion.
    "fr_laser_pump2": Script(
        "fr",
        pre=[*_FR_DEVICES, {"cmd": 671, "frequency": 1}, {"cmd": 672, "duration": 1000},
             {"cmd": 482, "duration": 2000}, {"cmd": 411}, {"cmd": 221, "pump2": True}, {"cmd": 601}],
        presses=[("RH", 500, 100), ("RH", 3500, 100)],
        stop=3700,
        on_at_stop=("cue", "pump2", "laser"),
    ),
    # PR, ratio 1 stepping by 1, no lever timeout (the PR sketch boots with 20 s):
    # rewards on the 1st, then the 2nd-next, then the 3rd-next press.
    "pr_cue_pump": Script(
        "pr",
        pre=[*_FR_DEVICES, {"cmd": 1074, "timeout": 0}, {"cmd": 201, "ratio": 1}, {"cmd": 205, "step": 1}],
        presses=[("RH", t, 80) for t in (500, 3500, 3800, 6800, 7100, 7400)],
        stop=7700,
        on_at_stop=("cue", "pump"),
    ),
    # Omission, 5 s: the reward fires after 5 s without a press; a press restarts the timer.
    "omission_cue_pump": Script(
        "omission",
        pre=[*_FR_DEVICES, {"cmd": 203, "interval": 5000}],
        presses=[("RH", 2000, 100), ("RH", 4000, 100)],
        stop=9500,
        on_at_stop=("cue", "pump"),
    ),
    # AUX (Mega fr only): a shock on every rewarded press, stopped mid-shock.
    "fr_aux_shock": Script(
        "fr",
        pre=[*_FR_DEVICES, {"cmd": 1472, "duration": 800}, {"cmd": 1480, "mode": 0},
             {"cmd": 1481, "probability": 100}, {"cmd": 1484, "seed": 12345}, {"cmd": 1401}],
        presses=[("RH", 500, 100), ("RH", 3000, 100)],
        stop=3300,
        on_at_stop=("aux",),
    ),
}

#: Known firmware/simulator divergences: (script, exact difference).
DIVERGENCES: dict[str, str] = {}


def _build(sketch: str, out_dir: Path) -> Path:
    exe = out_dir / sketch
    if exe.exists():
        return exe
    mega = not sketch.endswith("_lite")
    cmd = [
        *_cxx().split(), "-std=c++17", "-O0", "-w",
        "-DARDUINOJSON_ENABLE_PROGMEM=0", "-DARDUINOJSON_ENABLE_ARDUINO_STRING=0",
        "-DARDUINOJSON_ENABLE_ARDUINO_STREAM=0", "-DARDUINOJSON_ENABLE_ARDUINO_PRINT=0",
        "-include", str(STUB / "json_fstr.h"),
        f"-I{STUB}", f"-I{LIB_SRC}", f"-I{FIRMWARE / sketch}", f"-I{_arduinojson()}",
        f'-DSKETCH="{FIRMWARE / sketch / (sketch + ".ino")}"',
        *([MEGA] if mega else []), *(["-DHAS_AUX"] if sketch == "fr" else []),
        str(HARNESS), str(STUB / "stub.cpp"), *sorted(str(p) for p in LIB_SRC.glob("*.cpp")),
        # Sketch-local sources (pavlovian's PavlovianScheduler.cpp); the IDE links them too.
        *sorted(str(p) for p in (FIRMWARE / sketch).glob("*.cpp")),
        "-o", str(exe),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return exe


@pytest.fixture(scope="module")
def build_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("golden_traces")


def run_firmware(script: Script, build_dir: Path) -> tuple[list[dict], dict[str, dict[str, str]]]:
    exe = _build(script.sketch, build_dir)
    path = build_dir / f"{script.sketch}-{id(script)}.txt"
    path.write_text(script.harness_text())
    out = subprocess.run([str(exe), str(path)], check=True, capture_output=True, text=True).stdout
    events, checks = [], {}
    for line in out.splitlines():
        if line.startswith("#check "):
            label, *fields = line[len("#check "):].split()
            checks[label.split("@")[0]] = dict(f.split("=") for f in fields)
        elif line.startswith("{"):
            events.append(json.loads(line))
    return events, checks


def run_simulator(script: Script) -> list[dict]:
    q: queue.Queue = queue.Queue()
    never = 10**12  # the animal never presses, and never starts drinking, on its own
    animal = SubjectProfile(mean_iri_ms=never, pavlovian_mean_iri_ms=never, lick_latency_ms=(never, never))
    sim = FirmwareSimulator(q, script.sketch, seed=1, realtime=False, subject_profile=animal)
    for c in script.pre:
        sim.handle_command(c)
    sim.handle_command({"cmd": 101})

    # Inputs register after the debounce (module docstring); commands land on the
    # loop() of their own millisecond.
    actions: list[tuple[int, int, object]] = []
    for side, at, ms in script.presses:
        t = at + DEBOUNCE_MS
        actions.append((t, 0, lambda side=side, t=t, ms=ms: sim._lever_press(side, t, ms)))
    for at, ms in script.licks:
        t = at + DEBOUNCE_MS
        actions.append((t + ms, 0, lambda t=t, ms=ms: sim._log_lick(t, t + ms)))
    for at, c in script.cmds:
        actions.append((at, 1, lambda c=c: sim.handle_command(c)))
    actions.append((script.stop, 2, lambda: sim.handle_command({"cmd": 100})))
    for t, _, act in sorted(actions, key=lambda a: (a[0], a[1])):
        sim.run_until(t)
        act()

    events = []
    while not q.empty():
        item = q.get_nowait()
        events.append(json.loads(item) if isinstance(item, (str, bytes)) else item)
    return events


def stream(events: list[dict]) -> list[dict]:
    """The level-007 events, projected onto FIELDS and sorted (module docstring)."""
    rows = [{k: e[k] for k in FIELDS if k in e} for e in events if e.get("level") == "007"]
    when = lambda r: r.get("start_timestamp", r.get("timestamp", 0))  # noqa: E731
    return sorted(rows, key=lambda r: (when(r), r["device"], r["event"]))


def _cases():
    for name in SCRIPTS:
        marks = [pytest.mark.xfail(strict=True, reason=DIVERGENCES[name])] if name in DIVERGENCES else []
        yield pytest.param(name, marks=marks, id=name)


@pytest.mark.parametrize("name", list(_cases()))
def test_firmware_and_simulator_emit_the_same_events(name, build_dir):
    script = SCRIPTS[name]
    firmware, _ = run_firmware(script, build_dir)
    fw, sim = stream(firmware), stream(run_simulator(script))
    assert any(r["event"] == "PRESS" for r in fw), f"{name}: no press reached the firmware stream"
    assert fw == sim, f"{name}: firmware and simulator streams differ\nfirmware:  {fw}\nsimulator: {sim}"


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_every_output_is_off_after_the_stop(name, build_dir):
    script = SCRIPTS[name]
    events, checks = run_firmware(script, build_dir)
    probe = checks["before_stop"]
    for out in script.on_at_stop:
        assert probe[out] == "1", f"{name}: {out} was not on at the stop, so the check below proves nothing"
    assert any(e.get("event") == "END" for e in events), f"{name}: no CONTROLLER END"
    off = dict.fromkeys(OUTPUTS, "0")
    assert checks["after_5ms"] == off, f"{name}: output on 5 ms after END: {checks['after_5ms']}"
    assert checks["after_3s"] == off, f"{name}: output on 3 s after END: {checks['after_3s']}"


def test_the_comparison_catches_a_changed_press_class(build_dir):
    """Golden-negative: a stream that differs only in one press class must not compare equal."""
    fw = stream(run_firmware(SCRIPTS["fr_timeout_every_press"], build_dir)[0])
    drifted = [dict(r) for r in fw]
    timeout = next(r for r in drifted if r.get("class") == "TIMEOUT")
    timeout["class"] = "ACTIVE"
    assert drifted != fw


def test_the_safety_probe_sees_an_output_that_stays_on(build_dir, tmp_path):
    """Golden-negative: without a stop, the reward is still running at the probe."""
    script = Script("fr", pre=list(_FR_DEVICES), presses=[("RH", 500, 100)], stop=5000, on_at_stop=())
    text = script.harness_text().replace("stop 5000", "stop_probe 700\nstop 5000")
    exe = _build("fr", build_dir)
    path = tmp_path / "probe.txt"
    path.write_text(text)
    out = subprocess.run([str(exe), str(path)], check=True, capture_output=True, text=True).stdout
    probe = next(line for line in out.splitlines() if line.startswith("#check before_stop"))
    assert "cue=1" in probe and "pump=1" in probe
