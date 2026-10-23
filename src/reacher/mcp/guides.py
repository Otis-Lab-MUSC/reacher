"""Change kinds and their edit guides, as data.

Each guide is the ordered list of places a change of that kind has touched in
practice, taken from the commits that built five real features (AUX shock,
lever timeout mode, counted pumps, SLM, laser LH-only). Every step names:

* the file, and an exact ``anchor`` string that occurs once in it, so an agent
  edits at a known spot instead of searching;
* what to do there, as an instruction rather than advice;
* the ``guard`` that would catch a mistake: a compiler, a named check or test,
  or ``none``. A ``none`` step must be verified by hand, and ``verify`` says
  exactly what to look at. A guard is only named when leaving the step out
  makes it fail; a check that merely runs near the edit does not count.

The anchors are tested against the live checkouts (tests/test_mcp_changes.py),
so a refactor that moves one fails CI instead of leaving a guide that points
at nothing.

A step may carry ``when``: ``"board"`` steps apply only when the setting
reaches the board (a firmware command), ``"host"`` steps only when it does not
(counted pumps). Steps without ``when`` always apply. A step with ``needs`` is
dropped when the device has no value for that placeholder (no CLI entry, say).

Placeholders filled by ``plan_change`` come from ``DEVICES`` and the request:
``{device}``, ``{PREFIX}``, ``{param_code}``, ``{pin_symbol}``, ``{sim_key}``, ``{code_range}``, ``{ui_key}``, ``{ui_state}``,
``{ui_anchor}``, ``{control}``, ``{cli_id}``, ``{setting}``, ``{SETTING}``,
``{camel_setting}``, ``{paradigms}``, ``{param_paradigms}``, ``{sketch}``,
``{sketch_path}``.
"""

from __future__ import annotations

from typing import Any

#: Guard values. Anything not starting with one of these prefixes is invalid.
#: ``tsc`` / ``compiler``: a type or compile error stops the build.
#: ``check:<id>``: a reacher-mcp consistency check (check_consistency).
#: ``test:<path>``: a pytest file in reacher, or a node test in labrynth.
#: ``run:<key>``: an allowlisted runner command (run_checks).
#: ``none``: nothing catches an omission. Verify by hand.
GUARD_PREFIXES = ("tsc", "compiler", "check:", "test:", "run:", "none")

#: Every placeholder a guide may use. validate_guides() rejects any other.
PLACEHOLDERS = (
    "device", "PREFIX", "param_code", "pin_symbol", "sim_key", "code_range", "ui_key", "ui_state",
    "ui_anchor", "control", "cli_id", "setting", "SETTING", "camel_setting", "paradigms",
    "param_paradigms", "sketch", "sketch_path",
)

#: Each existing device's name in every layer, keyed by the component key that
#: get_hardware_map() and pin overrides use. One row is everything a step needs
#: to name the right enum prefix, UI state, card and CLI entry.
#: ``param_base`` is the first code of the device's parameter block (x70; x76
#: is always SET_PIN). ``sim_key`` is the device's key in the simulator's pin table, where it has
#: one. ``mega_only`` devices have no UNO (_lite) build. The Pins.h symbol is
#: not here: plan_change reads it from the schema dump.
DEVICES: dict[str, dict[str, Any]] = {
    "lever_rh": {"param_base": 1070, "PREFIX": "LEVER_RH", "sim_key": "LEVER_RH", "code_range": "10xx lever RH", "ui_key": "rhLever",
                 "ui_state": "LeverUiState", "control": "LeverControl", "cli_id": "rh-lever"},
    "lever_lh": {"param_base": 1370, "PREFIX": "LEVER_LH", "sim_key": "LEVER_LH", "code_range": "13xx lever LH", "ui_key": "lhLever",
                 "ui_state": "LeverUiState", "control": "LeverControl", "cli_id": "lh-lever"},
    "cue": {"param_base": 370, "PREFIX": "CUE", "sim_key": "CUE", "code_range": "3xx cue", "ui_key": "primaryCue",
            "ui_state": "CueUiState", "control": "CueControl", "cli_id": "primary-cue"},
    "cue2": {"param_base": 380, "PREFIX": "CUE2", "sim_key": "CUE_2", "code_range": "3xx cue", "ui_key": "secondaryCue",
             "ui_state": "CueUiState", "control": "CueControl", "cli_id": "secondary-cue"},
    "pump": {"param_base": 470, "PREFIX": "PUMP", "sim_key": "PUMP", "code_range": "4xx pump", "ui_key": "primaryPump",
             "ui_state": "PumpUiState", "control": "PumpControl", "cli_id": "primary-pump"},
    "pump2": {"param_base": 480, "PREFIX": "PUMP2", "sim_key": "PUMP_2", "code_range": "4xx pump", "ui_key": "secondaryPump",
              "ui_state": "PumpUiState", "control": "PumpControl", "cli_id": "secondary-pump"},
    "lick": {"param_base": 570, "PREFIX": "LICK", "sim_key": "LICK", "code_range": "5xx lick", "ui_key": "lickCircuit",
             "ui_state": "LickUiState", "control": "LickCircuitControl", "cli_id": "lick-circuit",
             # The lick circuit has no UI state type of its own yet.
             "ui_anchor": "  lickCircuit: DeviceArmState;"},
    "laser": {"param_base": 670, "PREFIX": "LASER", "sim_key": "LASER", "code_range": "6xx laser", "ui_key": "laser",
              "ui_state": "LaserUiState", "control": "LaserControl", "cli_id": "laser"},
    "microscope_trigger": {"param_base": 970, "PREFIX": "MICROSCOPE", "sim_key": "MICROSCOPE",
                           "code_range": "9xx microscope",
                           "ui_key": "microscope", "ui_state": "MicroscopeUiState",
                           "control": "MicroscopeControl", "cli_id": "microscope", "mega_only": True},
    "slm": {"param_base": 1170, "PREFIX": "SLM", "code_range": "11xx SLM", "ui_key": "slm", "ui_state": "SlmUiState",
            "control": "SLMControl", "cli_id": "slm", "mega_only": True},
    "aux": {"param_base": 1470, "PREFIX": "AUX", "code_range": "14xx AUX (Mega only)", "ui_key": "aux",
            "ui_state": "AuxUiState", "control": "AuxOutputControl", "cli_id": None, "mega_only": True},
}

#: Short decision list for sorting a request. First "yes" wins. Each question
#: is answered yes or no from the request alone; if the answer is "not sure",
#: ask the user that exact question.
DECISION_LIST: list[dict[str, str]] = [
    {"ask": "Can the user already get this by changing a setting in the app (a pin, the board, "
            "a paradigm parameter, a limit), with no code change?",
     "kind": "runtime_setting"},
    {"ask": "Is it a new saved session preset built only from settings that already exist?",
     "kind": "preset"},
    {"ask": "Does it only change which pin an existing device uses by default?",
     "kind": "pin_or_board"},
    {"ask": "Does it change only how the app looks or reads (layout, labels, help text), "
            "with no new command and no new saved value?",
     "kind": "ui_only"},
    {"ask": "Does it add a new option to a device that already exists (a new mode, a new "
            "duration, which pumps count)?",
     "kind": "device_setting"},
    {"ask": "Is it a new reward schedule or task built from devices that already exist?",
     "kind": "new_paradigm"},
    {"ask": "Does it add hardware the system has never driven or read, or a new board type?",
     "kind": "new_device"},
]

#: Worked examples from the baseline features, one line each.
EXAMPLES: list[dict[str, str]] = [
    {"request": "Use pin 24 for the shock box on rig 3.", "kind": "runtime_setting",
     "why": "Pin overrides are a runtime setting (Hardware > pin field, PUT /pins). No code."},
    {"request": "Run this rig on an UNO.", "kind": "runtime_setting",
     "why": "Board choice is made when uploading firmware. No code."},
    {"request": "Add an 'SA Very High' FR preset.", "kind": "preset",
     "why": "Presets are tables in web and CLI; nothing new reaches firmware."},
    {"request": "Make the laser default to pin 7.", "kind": "pin_or_board",
     "why": "A default pin lives in Pins.h, pinMeta.ts and two Python copies; the runtime "
            "override already exists."},
    {"request": "Only count pump 1 toward the infusion limit.", "kind": "device_setting",
     "why": "Counted pumps (Oct 14): a new host-side option on existing pumps. "
            "reaches_board = false: no firmware, no command code."},
    {"request": "Arm the lever timeout only after rewarded presses.", "kind": "device_setting",
     "why": "Lever timeout mode (Sep 16): a new command on an existing device. "
            "reaches_board = true."},
    {"request": "Let the laser fire on LH presses only.", "kind": "device_setting",
     "why": "Laser LH-only (Jun 18): a new command on an existing device, in some paradigms "
            "only (the others are listed in KNOWN_FIRMWARE_GAPS)."},
    {"request": "Add a random-ratio schedule.", "kind": "new_paradigm",
     "why": "A new schedule from existing devices: a new sketch and paradigm entries."},
    {"request": "Drive a tail-shock box from a TTL pin.", "kind": "new_device",
     "why": "AUX (Oct 16): new firmware class, commands, pins, UI card. Needed a bench test."},
    {"request": "Record SLM frame timestamps.", "kind": "new_device",
     "why": "SLM (Jun 2): new input device, new log level, export columns."},
]

#: Devices whose output can harm an animal or invalidate a session.
SAFETY_DEVICES: dict[str, str] = {
    "aux": "aversive output (tail shock)",
    "laser": "optogenetic stimulation",
    "pump": "drug or reward infusion",
    "pump2": "drug or reward infusion",
}

#: Words in a request that name a safety output even when no device key was
#: given. Matched as whole words.
SAFETY_WORDS: dict[str, str] = {
    "shock": "aversive output (tail shock)",
    "aux": "aversive output (tail shock)",
    "aversive": "aversive output",
    "laser": "optogenetic stimulation",
    "opto": "optogenetic stimulation",
    "optogenetic": "optogenetic stimulation",
    "pump": "drug or reward infusion",
    "pumps": "drug or reward infusion",
    "infusion": "drug or reward infusion",
    "infusions": "drug or reward infusion",
}

#: Words in a request that mean firmware timing changes. Matched as whole words.
#: "timeout" is left out on purpose: it is also a press class (TIMEOUT) that
#: acceptance criteria name without changing any timing.
TIMING_WORDS = ("timing", "delay", "delays", "duration", "interval", "latency", "onset", "ms",
                "millisecond", "milliseconds", "debounce")


def _step(repo: str, path: str, anchor: str, do: str, guard: str, verify: str,
          **extra: Any) -> dict[str, Any]:
    return {"repo": repo, "path": path, "anchor": anchor, "do": do,
            "guard": guard, "verify": verify, **extra}


_SKETCH_DISPATCH = "if (handleCommonDeviceCommand(devices, command, inputJson)) {"
_FW = "firmware/libraries/REACHERDevices/src"

#: Steps that end every firmware change. Kept separate so each guide stays readable.
_CLOSE_REACHER = [
    _step("reacher", "firmware/compile.sh", "for sketch in fr pr vi omission pavlovian; do",
          "Do NOT run compile.sh yourself. Tell the user the committed hex files must be "
          "rebuilt with firmware/compile.sh on a branch, then checked with "
          "`git status src/reacher/hex`.",
          "none", "by hand: the user confirms the hex rebuild and that every UNO _lite build "
          "still fits (the tightest sits above 90% of flash).", when="board"),
]

GUIDES: dict[str, dict[str, Any]] = {
    "runtime_setting": {
        "title": "Already a runtime setting",
        "code": False,
        "small_model_ok": True,
        "bench_test": "none",
        "summary": "Answer the user; change nothing. Name the control and where it is.",
        "answers": [
            {"want": "a different pin for a device",
             "answer": "Hardware panel > the device card's pin field (PUT /sessions/{id}/pins). "
                       "Saved per serial port and replayed on connect. Allowed pins come from "
                       "get_hardware_map (pin_sets, fixed_pins)."},
            {"want": "a different board (UNO or Mega)",
             "answer": "Pick the board when uploading firmware (Session panel > firmware card). "
                       "_lite paradigms are the UNO builds; Microscope, SLM and AUX are Mega only."},
            {"want": "a different ratio, interval, duration or limit",
             "answer": "Program panel > paradigm settings and limits. list_commands(paradigm=...) "
                       "shows which parameters that paradigm's firmware accepts."},
        ],
        "steps": [],
    },
    "preset": {
        "title": "New session preset",
        "code": True,
        "small_model_ok": True,
        "bench_test": "none",
        "summary": "Add the preset to the web tables and the CLI copy; the parity script proves they match.",
        "steps": [
            _step("labrynth", "web/src/components/program/presets/frSaPresets.ts",
                  "export const SA_HIGH_PRESET: SessionPreset = {",
                  "Copy the closest existing preset object (FR: this file; FR lite: frLitePresets.ts; "
                  "Pavlovian: pavlovianPresets.ts) into a new exported const with a new unique id "
                  "and name. Change only the fields the request names.",
                  "run:cli_parity", "run_checks(['tsc', 'cli_parity'])"),
            _step("labrynth", "web/src/components/program/presets/index.ts",
                  "export const SESSION_PRESETS: SessionPreset[] = [",
                  "Import the new const and add it to SESSION_PRESETS in the position the user wants.",
                  "none", "by hand: the preset appears in Program > presets in the running app "
                  "(`npm run dev` in labrynth/web)."),
            _step("labrynth", "cli/presets.py", "SESSION_PRESETS: list[dict] = [",
                  "Add the same preset as a dict with identical id, name and values, at the same "
                  "position as in the parity script's list (next step).",
                  "run:cli_parity", "run_checks(['cli_parity'])"),
            _step("labrynth", "scripts/check-cli-parity.py", "const presets = [",
                  "Add the new const to this list at the same position as in cli/presets.py. "
                  "The script compares the two id lists in order.",
                  "run:cli_parity", "run_checks(['cli_parity'])"),
        ],
    },
    "pin_or_board": {
        "title": "Default pin of an existing device",
        "code": True,
        "small_model_ok": True,
        "bench_test": "only when the physical wiring changes",
        "summary": "Change the default pin in firmware, the UI mirror and the two Python copies "
                   "together. Only Pins.h and pinMeta.ts are compared by a check.",
        "steps": [
            _step("reacher", f"{_FW}/Pins.h", "constexpr int8_t {pin_symbol} ",
                  "Change the default pin constant {pin_symbol} for {device}. Pick a pin from "
                  "get_hardware_map().pin_sets for every board the device runs on; respect "
                  "requires_pwm / requires_interrupt / requires_pcint and never use fixed_pins.",
                  "check:C6", "check_consistency(scopes=['pins'])", when="board"),
            _step("labrynth", "web/src/components/hardware/pinMeta.ts",
                  "export const DEFAULT_PIN: Record<Component, number> = {",
                  "Set {device} to the same pin as Pins.h.",
                  "check:C6", "check_consistency(scopes=['pins'])"),
            _step("reacher", "src/reacher/api/routers/hardware.py", "_DEFAULT_COMPONENT_PINS = {",
                  "Set {device} to the same pin as Pins.h. The pin-conflict check for the shock "
                  "output reads this table.",
                  "none", "by hand: the value for {device} here equals {pin_symbol} in Pins.h."),
            _step("reacher", "src/reacher/kernel/simulator.py", "_DEFAULT_PINS = {",
                  "Set the `{sim_key}` entry to the same pin as Pins.h.",
                  "none", "by hand: the value for {sim_key} here equals {pin_symbol} in Pins.h.",
                  needs="sim_key"),
            *_CLOSE_REACHER,
        ],
    },
    "device_setting": {
        "title": "New setting on an existing device",
        "code": True,
        "small_model_ok": True,
        "bench_test": "only when output timing or an output's on/off behaviour changes",
        "summary": "Board settings: add the command in firmware and Python, handle it in each "
                   "sketch (or once in the shared handler), mirror it in the simulator, then "
                   "wire the UI and CLI. Host-only settings (reaches_board = false): kernel, API, "
                   "UI and CLI only; no command code.",
        "steps": [
            _step("reacher", f"{_FW}/Commands.h", "namespace Cmd",
                  "Add `constexpr int {PREFIX}_SET_{SETTING} = {param_code};` beside the other "
                  "{PREFIX}_ commands.",
                  "check:C1", "check_consistency(scopes=['commands'])", when="board"),
            _step("reacher", "src/reacher/kernel/commands.py", "class CommandCode(IntEnum):",
                  "Add `{PREFIX}_SET_{SETTING} = {param_code}` beside the other {PREFIX}_ members.",
                  "check:C1", "check_consistency(scopes=['commands'])", when="board"),
            _step("reacher", "src/reacher/kernel/commands.py",
                  "COMMAND_REGISTRY: Dict[int, CommandSpec] = {",
                  "Add a CommandSpec for {PREFIX}_SET_{SETTING}: copy the nearest {PREFIX}_SET_ "
                  "spec, then set payload_key='{setting}', payload_type=int and "
                  "paradigms={paradigms}.",
                  "check:C2", "check_consistency(scopes=['commands', 'firmware'])", when="board"),
            _step("reacher", "src/reacher/api/routers/hardware.py", "_VALUE_RANGES = {",
                  "Add `\"{setting}\": (<min>, <max>),` with the lowest and highest value the "
                  "firmware accepts. If `behavior` does not say, ask the user.",
                  "none", "by hand: POST /api/hardware/<id>/command with a value outside the "
                  "range answers 400.", when="board"),
            _step("reacher", f"{_FW}/Scheduler.h", "class Scheduler {",
                  "Does the setting change when outputs fire or how presses are counted? Yes: add "
                  "its state here and the logic in Scheduler.cpp. No: add it to {device}'s own "
                  "class in the same folder instead, beside its other setters.",
                  "none", "by hand: the user compiles with firmware/compile.sh and runs the "
                  "bench check named in `done_when`.", when="board"),
            _step("reacher", f"{_FW}/ReacherHelpers.cpp",
                  "bool handleCommonDeviceCommand(DeviceSet& ds, int command, JsonDocument& inputJson) {",
                  "Does the command do the same thing in every paradigm? Yes: add its case here "
                  "and skip the per-sketch steps that follow. No: skip this step.",
                  "check:C14", "check_consistency(scopes=['firmware'])", when="board"),
            _step("reacher", "{sketch_path}", _SKETCH_DISPATCH,
                  "Skip this step if you handled the command in ReacherHelpers.cpp. Otherwise add "
                  "`case Cmd::{PREFIX}_SET_{SETTING}:` to the switch that follows this line, in "
                  "{sketch}.",
                  "check:C14", "check_consistency(scopes=['firmware'])",
                  when="board", each_sketch=True),
            _step("reacher", "src/reacher/kernel/simulator.py", "def handle_command(self, cmd_data: dict):",
                  "Mirror the firmware behaviour for the new code, including any refusal the "
                  "firmware sends (level 006).",
                  "none", "by hand: run a simulator session that sends the command and confirm "
                  "the same config/param lines the firmware would emit.", when="board"),
            _step("reacher", "src/reacher/kernel/reacher.py", "class REACHER:",
                  "Add the setting's state to REACHER with a setter and a getter (model: "
                  "set_counted_pumps / get_counted_pumps). Apply it where the kernel acts on it.",
                  "none", "by hand: add a pytest that sets the value and asserts its effect, "
                  "then run_checks(['pytest']).", when="host"),
            _step("reacher", "src/reacher/api/routers/program.py", "class LimitRequest(BaseModel):",
                  "If the setting belongs to session limits, add an Optional field here and apply "
                  "it in set_limit (model: counted_pumps). If it does not, stop and ask the user "
                  "which endpoint should set it.",
                  "none", "by hand: a request with a bad value answers 400 (add it to the pytest "
                  "from the previous step).", when="host"),
            _step("reacher", "src/reacher/api/routers/session.py", "async def get_session(",
                  "Return the current value in the session response so the UI can show it "
                  "after a reload.",
                  "none", "by hand: GET /api/sessions/<id> includes the new key.", when="host"),
            _step("labrynth", "web/src/api/client.ts", "export class MachineApiClient {",
                  "Add the new field to the request this setting is sent with (model: "
                  "counted_pumps in setLimit).",
                  "tsc", "run_checks(['tsc'])", when="host"),
            _step("labrynth", "web/src/types/index.ts", "{ui_anchor}",
                  "Add `{camel_setting}` to {ui_state}, the UI state of {device}. If "
                  "{ui_state} does not exist yet, create `export interface {ui_state} extends "
                  "DeviceArmState {}` above HardwareUiState and use it for `{ui_key}` there.",
                  "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/store/useSessionStore.ts",
                  "export const defaultHardwareUiState = (): HardwareUiState => ({",
                  "Give `{ui_key}.{camel_setting}` a default that matches the firmware default.",
                  "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/components/hardware/{control}.tsx", "export function {control}(",
                  "Add the control for `{camel_setting}` to the card and send it the way the card "
                  "sends its other settings. If some paradigms lack it, disable the control there "
                  "(see notes).",
                  "none", "by hand: in the running app the control shows on the {device} card, "
                  "and the event log shows the command when the card is Set."),
            _step("labrynth", "web/src/components/program/devicePresets.ts",
                  "export const PRESET_COMMAND_MAP: Record<string, { arm: number; disarm: number; params?: Record<string, number> }> = {",
                  "Add `{camel_setting}: {param_code}` to params under `{ui_key}`. {param_paradigms}",
                  "run:cli_parity", "run_checks(['cli_parity'])", when="board"),
            _step("labrynth", "cli/presets.py",
                  "PRESET_COMMAND_MAP: dict[str, dict] = {",
                  "Mirror the same param key, code and PARAM_PARADIGMS entry as devicePresets.ts.",
                  "run:cli_parity", "run_checks(['cli_parity'])", when="board"),
            _step("labrynth", "web/src/components/program/presets/frSaPresets.ts",
                  "export const SA_HIGH_PRESET: SessionPreset = {",
                  "Ask the user: should the built-in presets set the new value? No: skip this "
                  "step. Yes: add `{camel_setting}` with the default value under `{ui_key}` in "
                  "every preset in this file, frLitePresets.ts and pavlovianPresets.ts, and the "
                  "same in cli/presets.py.",
                  "run:cli_parity", "run_checks(['cli_parity'])"),
            _step("labrynth", "cli/app.py", "DEVICE_CONFIGS: list[dict] = [",
                  "Under the entry with id '{cli_id}', add a params entry with the same key and "
                  "code as devicePresets.ts.",
                  "none", "by hand: `python -m cli`, open the {cli_id} device menu, the new "
                  "setting is listed.", when="board", needs="cli_id"),
            _step("labrynth", "cli/app.py", "class ReacherCLI:",
                  "Add a menu action that sets the value through the same endpoint as the web "
                  "(model: _set_counted_pumps).",
                  "none", "by hand: `python -m cli`, the action sets the value and GET "
                  "/api/sessions/<id> shows it.", when="host"),
            _step("labrynth", "web/src/components/monitor/hardwareSummary.ts",
                  "export function formatDeviceParams(",
                  "Add the setting to the {device} summary text when it is not the default.",
                  "none", "by hand: the session summary shows the new value."),
            _step("labrynth", "web/src/components/monitor/SessionStartModal.tsx",
                  "export function SessionStartModal() {",
                  "Add the new setting to the {device} line of the start summary.",
                  "none", "by hand: open the start modal with {device} armed."),
            _step("labrynth", "web/src/hooks/useSessionRecovery.ts",
                  "export function useSessionRecovery() {",
                  "Check that `{ui_key}` is saved and restored here as a whole object. If it is, "
                  "change nothing. If fields are copied one by one, add `{camel_setting}`.",
                  "none", "by hand: set it, reload the page, check it is still set."),
            _step("labrynth", "web/src/components/tutorial/help/content.ts",
                  "export const HELP_CONTENT: HelpSection[] = [",
                  "Add one sentence on the new setting to the {device} help entry.",
                  "none", "by hand: the help panel shows it."),
            *_CLOSE_REACHER,
        ],
        "if_some_paradigms_lack_it": (
            "If the firmware for some paradigms will not handle the command, add an entry to "
            "KNOWN_FIRMWARE_GAPS in src/reacher/schema.py and regenerate "
            "web/src/generated/firmwareGaps.ts with `npm run gen:gaps` in labrynth/web. "
            "Disable (do not hide) the control on those paradigms."
        ),
    },
    "new_paradigm": {
        "title": "New paradigm from existing parts",
        "code": True,
        "small_model_ok": False,
        "bench_test": "a scripted session on a rig before first use",
        "summary": "A new sketch plus every paradigm list. Hand this to a person or a larger "
                   "model; the steps below are the map, not a recipe.",
        "stop_if": ["always: paradigms change scheduler behaviour that no check covers"],
        "steps": [
            _step("reacher", "firmware/compile.sh", "for sketch in fr pr vi omission pavlovian; do",
                  "Add the new sketch to the Mega loop (and the _lite loop for an UNO twin).",
                  "test:tests/test_firmware_parity.py", "run_checks(['pytest_parity'])"),
            _step("reacher", "src/reacher/kernel/commands.py", "SCHEDULE_TO_PARADIGM: Dict[str, str] = {",
                  "Add the paradigm to PARADIGMS / LITE_CAPABLE_PARADIGMS above and to this map.",
                  "test:tests/test_firmware_parity.py", "run_checks(['pytest_parity'])"),
            _step("reacher", "src/reacher/kernel/simulator.py", "PARADIGM_TO_SCHEDULE = {",
                  "Add the paradigm here, then teach FirmwareSimulator the new schedule.",
                  "test:tests/test_firmware_parity.py",
                  "run_checks(['pytest_parity']) catches a missing map entry only; check the "
                  "schedule's behaviour by hand with a simulator session."),
            _step("labrynth", "web/src/lib/firmwareBoard.ts",
                  'const BASE_PARADIGMS = ["fr", "pr", "vi", "omission", "pavlovian"];',
                  "Add the paradigm name.", "none",
                  "by hand: the firmware card offers the new paradigm on a Mega."),
            _step("labrynth", "web/src/components/monitor/LiveStats.tsx", "interface ParadigmStatsConfig {",
                  "Add the paradigm's stats config.", "none",
                  "by hand: live stats during a simulator session."),
            _step("labrynth", "web/src/components/program/ParadigmSettings.tsx",
                  "export function ParadigmSettings(",
                  "Add the paradigm's parameter fields.", "none", "by hand: program panel."),
            _step("labrynth", "web/src/api/mock.ts", "export const listParadigms",
                  "Add the paradigm to the demo list.", "none", "by hand: demo site."),
            *_CLOSE_REACHER,
        ],
    },
    "new_device": {
        "title": "New device",
        "code": True,
        "small_model_ok": False,
        "bench_test": "always: one scripted check on a real rig",
        "summary": "Touches every layer (the AUX device took 18 commits and 55 code files). "
                   "Hand this to a person or a larger model; the steps below are the map.",
        "stop_if": ["always"],
        "steps": [
            _step("reacher", f"{_FW}/Device.h", "enum class DeviceType : uint8_t {",
                  "Add the device type (guard Mega-only types with __AVR_ATmega2560__).",
                  "compiler", "by hand: compile every sketch."),
            _step("reacher", f"{_FW}/Pins.h", "constexpr int8_t PIN_AUX",
                  "Add {pin_symbol} after the existing pins.", "check:C6",
                  "check_consistency(scopes=['pins'])"),
            _step("reacher", f"{_FW}/AuxOutput.h", "class Aux : public Device {",
                  "Use this class as the model for the new device class (a new .h/.cpp pair). "
                  "Outputs must start LOW (see Device.cpp).",
                  "compiler", "by hand: compile every sketch."),
            _step("reacher", f"{_FW}/Commands.h", "namespace Cmd",
                  "Add the {PREFIX}_ command block in a free range.", "check:C1",
                  "check_consistency(scopes=['commands'])"),
            _step("reacher", f"{_FW}/Scheduler.cpp", "void Scheduler::EndSession(uint32_t now) {",
                  "Register the device with the scheduler (model: RegisterAux) and make "
                  "EndSession turn its output off.",
                  "none", "by hand: end a session mid-output on a rig; the output goes off."),
            _step("reacher", "{sketch_path}", _SKETCH_DISPATCH,
                  "Construct and register the device and handle its commands in {sketch}.",
                  "check:C14", "check_consistency(scopes=['firmware'])", each_sketch=True),
            _step("reacher", "src/reacher/kernel/commands.py", "COMMAND_REGISTRY: Dict[int, CommandSpec] = {",
                  "Add CommandCode members and registry specs.", "check:C2",
                  "check_consistency(scopes=['commands'])"),
            _step("reacher", "src/reacher/pin_overrides.py", "PIN_CONSTRAINTS: dict[int, PinConstraint] = {",
                  "Add the pin constraint and SET_PIN mapping.", "check:C3",
                  "check_consistency(scopes=['pins'])"),
            _step("reacher", "src/reacher/schema.py", "PIN_SYMBOL_TO_COMPONENT: dict[str, str] = {",
                  "Map {pin_symbol} to the component key.", "check:C6",
                  "check_consistency(scopes=['pins'])"),
            _step("reacher", "src/reacher/kernel/reacher.py", "def update_behavioral_events(self, event: dict) -> None:",
                  "Handle the device's events (and a new log level if it has one: see code_dict).",
                  "none", "by hand: in a simulator session the device's events reach the event "
                  "log. A new log level left out of code_dict fails run_checks(['pytest_parity']); "
                  "a missing event case fails nothing."),
            _step("reacher", "src/reacher/api/routers/hardware.py", "_DEFAULT_COMPONENT_PINS = {",
                  "Add the device's default pin.", "none",
                  "by hand: equals {pin_symbol} in Pins.h."),
            _step("reacher", "src/reacher/api/routers/file.py", "def _build_behavior_csv(",
                  "Add export columns or files for the device's data.", "none",
                  "by hand: export a simulator session and open the files."),
            _step("reacher", "src/reacher/kernel/simulator.py", "class FirmwareSimulator:",
                  "Simulate the device, including every firmware refusal.", "none",
                  "by hand: a simulator session shows the device's events."),
            _step("labrynth", "web/src/components/hardware/pinMeta.ts",
                  "export const SET_PIN_CODE: Record<Component, number> = {",
                  "Add the component to every pinMeta table.", "check:C3",
                  "check_consistency(scopes=['pins'])"),
            _step("labrynth", "web/src/types/index.ts", "export interface HardwareUiState {",
                  "Add the device's UI state.", "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/store/useSessionStore.ts",
                  "export const defaultHardwareUiState = (): HardwareUiState => ({",
                  "Add the device's default UI state.", "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/components/configuration/ConfigurationPanel.tsx",
                  "export function ConfigurationPanel() {",
                  "Mount the new <Device>Control card (model: AuxOutputControl.tsx).", "none",
                  "by hand: the card shows and arms."),
            _step("labrynth", "web/src/components/hardware/usePinOverridesHydration.ts",
                  "export function usePinOverridesHydration(",
                  "Map the component's saved pin override into its UI state.", "none",
                  "by hand: set a pin, reload, the card shows it."),
            _step("labrynth", "web/src/components/monitor/hardwareSummary.ts",
                  "export const DEVICE_LABELS: Record<string, string> = {",
                  "Label the device for the summaries.", "none",
                  "by hand: the session summary names the device."),
            _step("labrynth", "web/src/hooks/useSessionWebSockets.ts",
                  "export function useSessionWebSockets() {",
                  "Route the device's events to the store (counters, state).", "none",
                  "by hand: the device's events reach the event log in a simulator session."),
            _step("labrynth", "web/src/components/monitor/EventTimeline.tsx",
                  "export function EventTimeline(",
                  "Give the device a timeline lane.", "none", "by hand: the timeline shows it."),
            _step("labrynth", "web/src/components/monitor/LiveStats.tsx",
                  "export function LiveStats(",
                  "Add a counter if the user needs one.", "none", "by hand: live stats."),
            _step("labrynth", "web/src/components/monitor/SessionStartModal.tsx",
                  "export function SessionStartModal() {",
                  "Show the device in the start summary.", "none", "by hand: start modal."),
            _step("labrynth", "web/src/components/program/presets/deviceMetadata.ts",
                  "export const PRESET_EXCLUDED_DEVICE_KEYS",
                  "Either exclude the device from presets here or add it to the preset tables "
                  "(then follow the preset guide).", "none", "by hand: save and load a preset."),
            _step("labrynth", "web/src/api/mock.ts", "export const listParadigms",
                  "Add the device to the demo data if the demo site should show it.", "none",
                  "by hand: demo site."),
            *_CLOSE_REACHER,
        ],
    },
    "ui_only": {
        "title": "UI only",
        "code": True,
        "small_model_ok": True,
        "bench_test": "none",
        "summary": "Frontend only. If you find yourself adding a command code or a field the "
                   "backend must store, stop: this is a device_setting.",
        "steps": [
            _step("labrynth", "web/src/types/index.ts", "export interface HardwareUiState {",
                  "Change types only if the view needs a new derived field.",
                  "tsc", "run_checks(['tsc', 'lint', 'web_tests'])"),
        ],
    },
}

KINDS: tuple[str, ...] = tuple(GUIDES)
