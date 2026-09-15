"""QA-reserved end-to-end coverage for the lever timeout mode (Cmd 1077 / 1377).

``tests/test_timeout_mode.py`` (task B6) covers each layer well, but always with
one joint mocked out: its router tests run against a ``Mock`` REACHER, and its
``firmware_information`` test drives ``REACHER`` directly and never touches HTTP.
The seam between them — a real ``SessionManager``, a real ``REACHER``, the real
simulator, reached over the real router — is where the UI actually lives, and it
was untested. Everything here goes through ``TestClient`` against an unmocked
stack.

Four gaps are pinned, each found during Phase-3 verification:

1. the HTTP round trip (``POST`` the mode, start, read it back out of
   ``GET /api/hardware/{id}/config``),
2. the paradigm gate for omission/pavlovian against a *real* session manager —
   the frontend's session-start dispatch depends on this returning 400, so it
   must not drift silently,
3. the cross-layer invariant that mode 1 is meaningless without a ``SET_TIMEOUT``
   chain step, plus the simulator's FR default-timeout fidelity defect, which is
   pinned with ``xfail(strict=True)`` rather than left undocumented,
4. the cross-repo dispatch contract: every code labrynth's session-start loop
   sends must be declared for every paradigm it can be sent on. 1077 was not,
   which was the QA.md BLOCKER (an omission session start POSTed it, took a 400
   and never reached ``startProgram()``). Fixed by labrynth's per-param gate
   ``PARAM_PARADIGMS`` / ``canDispatchParam``; this file now checks both halves —
   the gate's contents *and* that every dispatch loop consults it.
"""

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY
from reacher.kernel.commands import ALL_PARADIGMS, COMMAND_REGISTRY, CommandCode
from reacher.kernel.simulator import (
    TIMEOUT_MODE_EVERY_PRESS,
    TIMEOUT_MODE_REWARD_ONLY,
    FirmwareSimulator,
)
from reacher.mcp.sources import ts

AUTH = {"Authorization": f"Bearer {API_KEY}"}

RH = int(CommandCode.LEVER_RH_SET_TIMEOUT_MODE)
LH = int(CommandCode.LEVER_LH_SET_TIMEOUT_MODE)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = REPO_ROOT / "firmware"
LABRYNTH_ROOT = ts.find_labrynth_root()


@pytest.fixture
def client():
    """Unmocked app — real SessionManager, real REACHER, real simulator."""
    app = create_app()
    with TestClient(app) as c:
        yield c


def _simulator_session(client, paradigm):
    """Create a session on the SIMULATOR port and connect it.

    The simulator reports its own paradigm on the ``*IDN?`` handshake, so the
    session's paradigm comes from the connect rather than being set by hand.
    """
    resp = client.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": paradigm}, headers=AUTH)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    connect = client.post(
        f"/api/serial/{sid}/connect",
        json={"port": "SIMULATOR", "baudrate": 115200},
        headers=AUTH,
    )
    assert connect.status_code == 200, connect.text
    return sid


def _teardown(client, sid):
    client.post(f"/api/serial/{sid}/disconnect", headers=AUTH)
    client.delete(f"/api/sessions/{sid}", headers=AUTH)


# ---------------------------------------------------------------------------
# 1. The HTTP round trip, end to end
# ---------------------------------------------------------------------------


class TestHttpRoundTrip:
    """POST the mode over HTTP, start the session, read it back over HTTP."""

    @pytest.mark.parametrize("code", [RH, LH])
    @pytest.mark.parametrize("mode", [TIMEOUT_MODE_EVERY_PRESS, TIMEOUT_MODE_REWARD_ONLY])
    def test_mode_survives_post_start_and_readback(self, client, code, mode):
        sid = _simulator_session(client, "fr")
        try:
            sent = client.post(
                f"/api/hardware/{sid}/command", json={"code": code, "value": mode}, headers=AUTH
            )
            assert sent.status_code == 200, sent.text

            started = client.post(f"/api/program/{sid}/start", headers=AUTH)
            assert started.status_code == 200, started.text

            deadline = time.monotonic() + 5.0
            firmware = {}
            while time.monotonic() < deadline:
                firmware = client.get(f"/api/hardware/{sid}/config", headers=AUTH).json()["firmware_info"]
                if "timeout_mode" in firmware:
                    break
                time.sleep(0.02)

            assert firmware.get("timeout_mode") == mode, firmware
        finally:
            client.post(f"/api/program/{sid}/stop", headers=AUTH)
            _teardown(client, sid)

    def test_out_of_range_never_reaches_the_device(self, client):
        """A rejected value must not move the state the device already holds."""
        sid = _simulator_session(client, "fr")
        try:
            ok = client.post(f"/api/hardware/{sid}/command", json={"code": RH, "value": 1}, headers=AUTH)
            assert ok.status_code == 200

            bad = client.post(f"/api/hardware/{sid}/command", json={"code": RH, "value": 2}, headers=AUTH)
            assert bad.status_code == 400
            assert "timeout_mode must be between 0 and 1" in bad.json()["detail"]

            client.post(f"/api/program/{sid}/start", headers=AUTH)
            deadline = time.monotonic() + 5.0
            firmware = {}
            while time.monotonic() < deadline:
                firmware = client.get(f"/api/hardware/{sid}/config", headers=AUTH).json()["firmware_info"]
                if "timeout_mode" in firmware:
                    break
                time.sleep(0.02)
            # Still 1 — the 400 was refused host-side, not clamped on the wire.
            assert firmware.get("timeout_mode") == 1, firmware
        finally:
            client.post(f"/api/program/{sid}/stop", headers=AUTH)
            _teardown(client, sid)


# ---------------------------------------------------------------------------
# 2. The paradigm gate the frontend dispatch depends on
# ---------------------------------------------------------------------------


class TestParadigmGateAgainstARealSessionManager:
    """The UI dispatches lever params unconditionally for any armed lever.

    Whatever the eventual fix for that, this 400 is the contract both sides are
    written against, so it is pinned here against a real session manager rather
    than only against a Mock.
    """

    @pytest.mark.parametrize("paradigm", ["omission", "omission_lite", "pavlovian"])
    @pytest.mark.parametrize("code", [RH, LH])
    def test_rejected_where_the_sketch_has_no_timeout_mode(self, client, paradigm, code):
        resp = client.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": paradigm}, headers=AUTH)
        sid = resp.json()["session_id"]
        try:
            sent = client.post(
                f"/api/hardware/{sid}/command", json={"code": code, "value": 1}, headers=AUTH
            )
            assert sent.status_code == 400, sent.text
            assert "not available for" in sent.json()["detail"]
        finally:
            client.delete(f"/api/sessions/{sid}", headers=AUTH)

    @pytest.mark.parametrize("paradigm", ["fr", "fr_lite", "pr", "pr_lite", "vi", "vi_lite"])
    def test_accepted_everywhere_it_is_declared(self, client, paradigm):
        resp = client.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": paradigm}, headers=AUTH)
        sid = resp.json()["session_id"]
        try:
            sent = client.post(f"/api/hardware/{sid}/command", json={"code": RH, "value": 1}, headers=AUTH)
            # 409 (not connected) means it cleared the paradigm and range gates,
            # which is all this assertion is about.
            assert sent.status_code in (200, 409), sent.text
            if sent.status_code == 400:  # pragma: no cover - explicit failure text
                pytest.fail(f"1077 rejected for declared paradigm {paradigm}: {sent.text}")
        finally:
            client.delete(f"/api/sessions/{sid}", headers=AUTH)


# ---------------------------------------------------------------------------
# 3. Cross-layer invariants
# ---------------------------------------------------------------------------


_SET_TIMEOUT_STEP = re.compile(r"steps\[\d+\]\.type\s*=\s*ActionType::SET_TIMEOUT")


@pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware source not present (installed-wheel run)")
class TestModeOneIsMeaningfulWhereItIsOffered:
    """Mode 1 arms the timeout only when the fired chain carries a SET_TIMEOUT
    step (``Scheduler::ChainAppliesTimeout``). A paradigm that declares 1077 but
    whose ``Config.h`` has no such step would therefore silently mean "no timeout,
    ever" in mode 1 — a config that looks armed and is not.
    """

    def _declaring_paradigms(self):
        base = {p.removesuffix("_lite") for p in COMMAND_REGISTRY[RH].paradigms}
        assert base, "1077 declares no paradigms — registry regression"
        return sorted(base)

    def test_every_declaring_paradigm_has_a_set_timeout_chain_step(self):
        for paradigm in self._declaring_paradigms():
            config = FIRMWARE / paradigm / "Config.h"
            assert config.is_file(), f"{paradigm}/Config.h missing"
            assert _SET_TIMEOUT_STEP.search(config.read_text()), (
                f"{paradigm} declares timeout mode 1077 but {config.relative_to(REPO_ROOT)} "
                "builds no chain with a SET_TIMEOUT step — mode 1 would never arm a timeout"
            )

    def test_golden_negative_the_regex_can_fail(self, tmp_path):
        """A check that has never failed is indistinguishable from one that cannot."""
        drifted = tmp_path / "Config.h"
        drifted.write_text("c->steps[4].type = ActionType::ACTIVATE_DEVICE;\n")
        assert _SET_TIMEOUT_STEP.search(drifted.read_text()) is None

    def test_omission_is_correctly_excluded(self):
        """Omission has no SET_TIMEOUT step, which is exactly why it must not
        declare 1077 — the two facts have to stay consistent."""
        omission = (FIRMWARE / "omission" / "Config.h").read_text()
        assert _SET_TIMEOUT_STEP.search(omission) is None
        assert "omission" not in COMMAND_REGISTRY[RH].paradigms
        assert "omission" not in COMMAND_REGISTRY[LH].paradigms


@pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware source not present (installed-wheel run)")
class TestSimulatorDefaultsMatchFirmwareDefaults:
    """The simulator's default timeout interval used to be inert — it modelled no
    timeout at all, so a wrong default cost nothing. Now that it classifies
    presses, the default decides whether a simulated session emits TIMEOUT
    presses, and ``fr`` ships ``DEFAULT_TIMEOUT_INTERVAL = 0`` while the
    simulator still starts at 20000.
    """

    @staticmethod
    def _firmware_default(paradigm):
        text = (FIRMWARE / paradigm / "Config.h").read_text()
        match = re.search(r"DEFAULT_TIMEOUT_INTERVAL\s*=\s*(\d+)", text)
        assert match, f"{paradigm}/Config.h: DEFAULT_TIMEOUT_INTERVAL not found"
        return int(match.group(1))

    @pytest.mark.parametrize("paradigm", ["pr", "vi"])
    def test_pr_and_vi_defaults_already_agree(self, paradigm):
        """Only fr drifts — pr/vi ship 20000, which is what the simulator uses."""
        assert self._firmware_default(paradigm) == 20000

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "KNOWN DEFECT (QA Phase 3, report team/reports/QA.md): the simulator defaults "
            "lever_rh_timeout/lever_lh_timeout to 20000 for every paradigm, but fr/Config.h "
            "ships DEFAULT_TIMEOUT_INTERVAL = 0. With the new press classification a default "
            "simulated FR run emits 5 ACTIVE / 15 TIMEOUT presses and 1 reward over 20 presses "
            "where the real board at its defaults emits 20 ACTIVE and 4 rewards. Remove this "
            "xfail when the simulator seeds its timeout per paradigm."
        ),
    )
    def test_fr_default_timeout_matches_the_shipped_sketch(self):
        import queue

        sim = FirmwareSimulator(queue.Queue())
        sim.schedule = "FIXED_RATIO"
        assert sim.lever_rh_timeout == self._firmware_default("fr")


# ---------------------------------------------------------------------------
# 4. The cross-repo dispatch contract (QA Phase 3 BLOCKER)
# ---------------------------------------------------------------------------


_PARAMS_RE = re.compile(r"^\s*(rhLever|lhLever)\s*:.*?params\s*:\s*\{([^}]*)\}", re.MULTILINE)
_CODE_RE = re.compile(r"(\w+)\s*:\s*(\d+)")
_PARADIGM_CONST_RE = re.compile(r"const\s+(\w+)\s*=\s*paradigm\s*===\s*\"(\w+)\"")
_LEVER_SKIP_RE = re.compile(r"^\s*if\s*\((.*rhLever.*lhLever.*)\)\s*continue;", re.MULTILINE)
_PARAM_GATE_RE = re.compile(r"PARAM_PARADIGMS[^=]*=\s*\{(.*?)\n\};", re.S)
_PARAM_GATE_ENTRY_RE = re.compile(r"(\w+)\s*:\s*\[([^\]]*)\]")
_PARAM_LOOP_RE = re.compile(r"Object\.entries\(mapping\.params\)")

#: Files carrying a dispatch loop over ``PRESET_COMMAND_MAP[device].params``.
#: Every one of them must consult the param gate, or the table is decoration.
_DISPATCH_SITES = (
    ("web", "src", "components", "monitor", "SessionStartModal.tsx"),
    ("web", "src", "components", "configuration", "ConfigurationPanel.tsx"),
    ("web", "src", "components", "program", "ProgramPanel.tsx"),
)


def _lever_dispatch_codes():
    """Codes labrynth's PRESET_COMMAND_MAP sends for the two lever devices."""
    source = (
        LABRYNTH_ROOT / "web" / "src" / "components" / "program" / "devicePresets.ts"
    ).read_text()
    found = {}
    for lever, body in _PARAMS_RE.findall(source):
        found[lever] = {name: int(code) for name, code in _CODE_RE.findall(body)}
    # Sanity floor: a reformat that defeats the regex must fail here rather than
    # return an empty mapping the comparison below would read as "no drift".
    assert set(found) == {"rhLever", "lhLever"}, f"parsed levers: {sorted(found)}"
    for lever, codes in found.items():
        assert "timeout" in codes, f"{lever}: parsed no timeout code — parser drift"
    return found


def _paradigms_skipped_by_session_start():
    """Paradigms whose levers ``SessionStartModal``'s dispatch loop skips."""
    source = (
        LABRYNTH_ROOT / "web" / "src" / "components" / "monitor" / "SessionStartModal.tsx"
    ).read_text()
    by_const = dict(_PARADIGM_CONST_RE.findall(source))
    guards = _LEVER_SKIP_RE.findall(source)
    assert guards, "no lever `continue` guard found in SessionStartModal — parser drift"
    skipped = {p for const, p in by_const.items() if any(const in g for g in guards)}
    # Sanity floor: pavlovian has been skipped there since before this change.
    assert "pavlovian" in skipped, f"parsed skip set {skipped} — parser drift"
    return skipped


def _param_paradigm_gates():
    """labrynth's per-param dispatch gate: ``{paramKey: {paradigm, ...}}``.

    The device-level skip above is all-or-nothing, and that is the wrong shape for
    a code like 1077: omission still needs its levers armed and still accepts
    ``timeout`` (1074), so only the one param may be withheld. ``PARAM_PARADIGMS``
    in ``devicePresets.ts`` carries that, and ``canDispatchParam`` applies it at
    every dispatch site. Params with no entry are dispatched everywhere.

    Base names are expanded with their ``_lite`` twins because the frontend gate
    runs through ``isParadigm()``, which strips the suffix — the same expansion
    ``commands.with_lite()`` does on this side.
    """
    source = (
        LABRYNTH_ROOT / "web" / "src" / "components" / "program" / "devicePresets.ts"
    ).read_text()
    block = _PARAM_GATE_RE.search(source)
    assert block, "PARAM_PARADIGMS not found in devicePresets.ts — parser drift"
    gates = {}
    for param, body in _PARAM_GATE_ENTRY_RE.findall(block.group(1)):
        bases = re.findall(r'"(\w+)"', body)
        assert bases, f"PARAM_PARADIGMS.{param} parsed as empty — parser drift"
        gates[param] = {
            p for b in bases for p in (b, f"{b}_lite") if p in ALL_PARADIGMS
        }
    # Sanity floor: the entry this whole contract was written for.
    assert "timeoutMode" in gates, f"parsed gates {sorted(gates)} — parser drift"
    return gates


@pytest.mark.skipif(
    LABRYNTH_ROOT is None,
    reason=(
        "labrynth checkout not found — the cross-repo dispatch contract is "
        "UNVERIFIED, not passing"
    ),
)
class TestSessionStartDispatchCannotSendAnUndeclaredCode:
    """``SessionStartModal``'s loop sends every ``PRESET_COMMAND_MAP`` lever param
    whose store value is set, for every paradigm it does not explicitly skip.
    ``MachineApiClient.request`` throws on a non-2xx, and that throw aborts the
    same ``try`` that later calls ``startProgram()`` — so one 400 from this loop
    means the session never starts at all.

    The contract is therefore: *every code in that map must be declared for every
    paradigm it can actually be sent on*. ``1074`` satisfies it for every reachable
    paradigm outright (declared for omission, where it is a firmware no-op).
    ``1077`` does not, and that was the Phase-3 blocker: it is FR/PR/VI-only, so an
    omission session start POSTed it, took a 400 and never reached
    ``startProgram()``. The fix is the per-param gate ``PARAM_PARADIGMS`` /
    ``canDispatchParam`` — a device-level skip would have been wrong, because
    omission still needs its levers armed and still takes 1074.

    So "can be sent on" is now two subtractions: the device-level skip set, then
    the per-param gate. Both are parsed out of the labrynth checkout, and the
    gate is only trusted if every dispatch loop consults it (asserted below).
    """

    def test_lever_dispatch_codes_are_declared_for_every_reachable_paradigm(self):
        codes = _lever_dispatch_codes()
        gates = _param_paradigm_gates()
        reachable = set(ALL_PARADIGMS) - _paradigms_skipped_by_session_start()
        violations = []
        for lever, params in sorted(codes.items()):
            for name, code in sorted(params.items()):
                spec = COMMAND_REGISTRY.get(code)
                if spec is None:
                    violations.append(f"{lever}.{name} = {code} is not in COMMAND_REGISTRY")
                    continue
                dispatched_on = reachable & gates.get(name, reachable)
                undeclared = sorted(p for p in dispatched_on if p not in spec.paradigms)
                if undeclared:
                    violations.append(
                        f"{lever}.{name} ({spec.name} {code}) is dispatched on "
                        f"{undeclared} but declared for {sorted(spec.paradigms)} "
                        "— session start 400s and startProgram() is never reached"
                    )
        assert not violations, "\n".join(violations)

    def test_the_gate_is_applied_at_every_dispatch_loop(self):
        """A gate table no dispatch site consults would pass the check above while
        the 400 still happened. Each loop over ``mapping.params`` must call
        ``canDispatchParam`` — five loops across three files at the time of writing.
        """
        total_loops = 0
        for parts in _DISPATCH_SITES:
            source = LABRYNTH_ROOT.joinpath(*parts).read_text()
            loops = len(_PARAM_LOOP_RE.findall(source))
            guards = source.count("canDispatchParam(paramKey")
            assert loops, f"{parts[-1]}: no `Object.entries(mapping.params)` loop — parser drift"
            assert guards == loops, (
                f"{parts[-1]}: {loops} param dispatch loop(s) but {guards} "
                "canDispatchParam guard(s) — an unguarded loop can still send a "
                "code the backend does not declare for the session's paradigm"
            )
            total_loops += loops
        assert total_loops >= 5, f"only {total_loops} dispatch loops found — parser drift"

    def test_golden_negative_the_parsers_can_fail(self, tmp_path):
        """A check that has never failed is indistinguishable from one that cannot."""
        assert not _PARAMS_RE.findall("rhLever: { arm: 1001, disarm: 1000 },\n")
        assert not _LEVER_SKIP_RE.findall("if (deviceKey === \"laser\") continue;\n")
        assert not _PARADIGM_CONST_RE.findall("const isPavlovian = mode === \"pavlovian\";")
        assert not _PARAM_GATE_RE.search('const PARAM_PARADIGMS = { timeoutMode: ["fr"] };')
        assert not _PARAM_GATE_ENTRY_RE.findall("timeoutMode: FR_LIKE,")
        assert not _PARAM_LOOP_RE.findall("Object.entries(mapping.arm)")

    def test_1074_is_the_example_of_the_contract_being_met(self):
        """The asymmetry that made this a live defect rather than a latent one."""
        reachable = set(ALL_PARADIGMS) - _paradigms_skipped_by_session_start()
        for code in (1074, 1374):
            spec = COMMAND_REGISTRY[code]
            assert not (reachable - set(spec.paradigms)), (
                f"{spec.name} no longer covers every reachable paradigm; the "
                "premise of the 1077 finding has changed"
            )
