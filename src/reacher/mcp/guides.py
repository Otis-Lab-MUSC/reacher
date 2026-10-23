"""Change kinds and their edit guides, as data.

Each guide is the ordered list of places a change of that kind has touched in
practice, taken from the commits that built five real features (AUX shock,
lever timeout mode, counted pumps, SLM, laser LH-only). Every step names:

* the file, and an exact ``anchor`` string that occurs once in it, so an agent
  edits at a known spot instead of searching;
* what to do there, as an instruction rather than advice;
* the ``guard`` that would catch a mistake: a compiler, a named check or test,
  or ``none``. A ``none`` step must be verified by hand, and ``verify`` says
  exactly what to look at.

The anchors are tested against the live checkouts (tests/test_mcp_changes.py),
so a refactor that moves one fails CI instead of leaving a guide that points
at nothing.

Placeholders filled by ``plan_change``: ``{device}`` (component key, e.g.
``aux``), ``{DEVICE}`` (command prefix, e.g. ``AUX``), ``{setting}``,
``{SETTING}``, ``{paradigms}``, ``{sketch}``, ``{sketch_path}``.
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

#: Short decision list for sorting a request. First match wins. Written so a
#: small model can follow it without judgement: each question is yes/no.
DECISION_LIST: list[dict[str, str]] = [
    {"ask": "Can the user already get this by changing a setting in the app (a pin, the board, "
            "a paradigm parameter, a limit) with no code change?",
     "kind": "runtime_setting"},
    {"ask": "Is it a new saved session preset built only from settings that already exist?",
     "kind": "preset"},
    {"ask": "Does it only change which pin a device uses by default, or add a board type?",
     "kind": "pin_or_board"},
    {"ask": "Does it change only how the app looks or reads (layout, labels, help text), "
            "with no new command and no new data?",
     "kind": "ui_only"},
    {"ask": "Does it add a new option to a device that already exists (a new mode, a new "
            "duration, which pumps count)?",
     "kind": "device_setting"},
    {"ask": "Is it a new reward schedule or task built from devices that already exist?",
     "kind": "new_paradigm"},
    {"ask": "Does it add hardware the system has never driven or read?",
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
     "why": "A default pin lives in Pins.h and pinMeta.ts; the runtime override already exists."},
    {"request": "Only count pump 1 toward the infusion limit.", "kind": "device_setting",
     "why": "Counted pumps (Oct 14): a new host-side option on existing pumps. No firmware."},
    {"request": "Arm the lever timeout only after rewarded presses.", "kind": "device_setting",
     "why": "Lever timeout mode (Sep 16): a new command on an existing device."},
    {"request": "Let the laser fire on LH presses only.", "kind": "device_setting",
     "why": "Laser LH-only (Jun 18): a new command on an existing device, in some paradigms."},
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

#: Words in a request that mean firmware timing changes.
TIMING_WORDS = ("timing", "delay", "duration", "interval", "latency", "onset", "ms ", "millisecond")


def _step(repo: str, path: str, anchor: str, do: str, guard: str, verify: str,
          **extra: Any) -> dict[str, Any]:
    return {"repo": repo, "path": path, "anchor": anchor, "do": do,
            "guard": guard, "verify": verify, **extra}


_SKETCH_DISPATCH = "if (handleCommonDeviceCommand(devices, command, inputJson)) {"

#: Steps that end every code change. Kept separate so each guide stays readable.
_CLOSE_REACHER = [
    _step("reacher", "firmware/compile.sh", "for sketch in fr pr vi omission pavlovian; do",
          "Do NOT run compile.sh yourself. Tell the user the committed hex files must be "
          "rebuilt with firmware/compile.sh on a branch, then checked with "
          "`git status src/reacher/hex`.",
          "none", "by hand: the user confirms the hex rebuild and that every UNO _lite build "
          "still fits (the tightest sits above 90% of flash).", firmware_only=True),
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
                  "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/components/program/presets/index.ts",
                  "export const SESSION_PRESETS: SessionPreset[] = [",
                  "Import the new const and add it to SESSION_PRESETS in the position the user wants.",
                  "none", "by hand: the preset appears in Program > presets in the running app."),
            _step("labrynth", "cli/presets.py", "SESSION_PRESETS: list[dict] = [",
                  "Add the same preset as a dict with identical id, name and values.",
                  "run:cli_parity", "run_checks(['cli_parity'])"),
            _step("labrynth", "scripts/check-cli-parity.py", "const presets = [",
                  "Add the new const to this list, or the parity script never compares it.",
                  "none", "by hand: the new preset id is printed in the parity script's output, "
                  "or deleting a field from the CLI copy makes the script fail."),
        ],
    },
    "pin_or_board": {
        "title": "Default pin or board variant",
        "code": True,
        "small_model_ok": True,
        "bench_test": "only when the physical wiring changes",
        "summary": "Change the default pin in firmware and the UI mirror together. "
                   "A new board type is a larger change: stop and hand it to a person.",
        "stop_if": ["adds a new board type"],
        "steps": [
            _step("reacher", "firmware/libraries/REACHERDevices/src/Pins.h", "PIN_{DEVICE}",
                  "Change the default pin constant for {device}. Pick a pin from "
                  "get_hardware_map().pin_sets for every board the device runs on; respect "
                  "requires_pwm / requires_interrupt / requires_pcint and never use fixed_pins.",
                  "check:C6", "check_consistency(scopes=['pins'])", firmware_only=True),
            _step("labrynth", "web/src/components/hardware/pinMeta.ts",
                  "export const DEFAULT_PIN: Record<Component, number> = {",
                  "Set {device} to the same pin as Pins.h.",
                  "check:C6", "check_consistency(scopes=['pins'])"),
            *_CLOSE_REACHER,
        ],
    },
    "device_setting": {
        "title": "New setting on an existing device",
        "code": True,
        "small_model_ok": True,
        "bench_test": "only when output timing or an output's on/off behaviour changes",
        "summary": "Add the command in firmware and Python, handle it in each sketch (or once in "
                   "the shared handler), mirror it in the simulator, then wire the UI and CLI.",
        "host_only_note": "If the setting never reaches the board (like counted pumps), skip "
                          "every step marked firmware_only and the simulator step.",
        "steps": [
            _step("reacher", "firmware/libraries/REACHERDevices/src/Commands.h", "namespace Cmd",
                  "Add `constexpr int {DEVICE}_SET_{SETTING} = <code>;` beside the other "
                  "{DEVICE}_ commands. Take <code> from list_commands().free_codes for the "
                  "{DEVICE} range, keeping the x7N parameter suffix.",
                  "check:C1", "check_consistency(scopes=['commands'])", firmware_only=True),
            _step("reacher", "src/reacher/kernel/commands.py", "class CommandCode(IntEnum):",
                  "Add `{DEVICE}_SET_{SETTING} = <code>` with the same code as Commands.h.",
                  "check:C1", "check_consistency(scopes=['commands'])"),
            _step("reacher", "src/reacher/kernel/commands.py",
                  "COMMAND_REGISTRY: Dict[int, CommandSpec] = {",
                  "Add a CommandSpec for the new code: payload_key, payload_type, and paradigms "
                  "set to exactly {paradigms}.",
                  "check:C2", "check_consistency(scopes=['commands', 'firmware'])"),
            _step("reacher", "firmware/libraries/REACHERDevices/src/ReacherHelpers.cpp",
                  "bool handleCommonDeviceCommand(DeviceSet& ds, int command, JsonDocument& inputJson) {",
                  "If the new command behaves the same in every paradigm, handle it here once and "
                  "skip the per-sketch steps. Otherwise handle it in each sketch below.",
                  "check:C14", "check_consistency(scopes=['firmware'])", firmware_only=True),
            _step("reacher", "{sketch_path}", _SKETCH_DISPATCH,
                  "Only if not handled in the shared handler: add `case Cmd::{DEVICE}_SET_{SETTING}:` "
                  "to the switch that follows this line, in {sketch}.",
                  "check:C14", "check_consistency(scopes=['firmware'])",
                  firmware_only=True, each_sketch=True),
            _step("reacher", "src/reacher/kernel/simulator.py", "def handle_command(self, cmd_data: dict):",
                  "Mirror the firmware behaviour for the new code, including any refusal the "
                  "firmware sends (level 006).",
                  "none", "by hand: run a simulator session that sends the command and confirm "
                  "the same config/param lines the firmware would emit. Golden traces will guard "
                  "this later; today nothing does."),
            _step("labrynth", "web/src/types/index.ts", "export interface HardwareUiState {",
                  "Add the setting's field to the {device} UI state interface above this line.",
                  "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/store/useSessionStore.ts",
                  "export const defaultHardwareUiState = (): HardwareUiState => ({",
                  "Give the new field a default that matches the firmware default.",
                  "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/components/program/devicePresets.ts",
                  "export const PRESET_COMMAND_MAP: Record<string, { arm: number; disarm: number; params?: Record<string, number> }> = {",
                  "Add the new param key and code under the {device} entry. If not every paradigm "
                  "supports it, add it to PARAM_PARADIGMS in this file too.",
                  "run:cli_parity", "run_checks(['cli_parity'])"),
            _step("labrynth", "cli/presets.py",
                  "PRESET_COMMAND_MAP: dict[str, dict] = {",
                  "Mirror the same param key, code and PARAM_PARADIGMS entry.",
                  "run:cli_parity", "run_checks(['cli_parity'])"),
            _step("labrynth", "web/src/components/monitor/SessionStartModal.tsx",
                  "export function SessionStartModal() {",
                  "Show the new setting in the start summary if the user needs to see it before "
                  "starting.",
                  "none", "by hand: open the start modal with the device armed."),
            _step("labrynth", "web/src/hooks/useSessionRecovery.ts",
                  "export function useSessionRecovery() {",
                  "If the setting must survive a page reload, confirm it is part of what is saved "
                  "and restored here.",
                  "none", "by hand: set it, reload the page, check it is still set."),
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
                  "test:tests/test_firmware_parity.py", "run_checks(['pytest_parity'])",
                  firmware_only=True),
            _step("reacher", "src/reacher/kernel/commands.py", "SCHEDULE_TO_PARADIGM: Dict[str, str] = {",
                  "Add the paradigm to PARADIGMS / LITE_CAPABLE_PARADIGMS above and to this map.",
                  "test:tests/test_firmware_parity.py", "run_checks(['pytest_parity'])"),
            _step("reacher", "src/reacher/kernel/simulator.py", "class FirmwareSimulator:",
                  "Teach the simulator the new schedule.",
                  "none", "by hand until golden traces exist."),
            _step("labrynth", "web/src/lib/firmwareBoard.ts", "export function boardFromParadigm(",
                  "Map the new paradigm to its board.", "test:web/tests/firmwareBoard.test.mjs",
                  "run_checks(['web_tests'])"),
            _step("labrynth", "web/src/components/monitor/LiveStats.tsx", "interface ParadigmStatsConfig {",
                  "Add the paradigm's stats config.", "none", "by hand: live stats during a simulator session."),
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
            _step("reacher", "firmware/libraries/REACHERDevices/src/Device.h", "enum class DeviceType : uint8_t {",
                  "Add the device type (guard Mega-only types with __AVR_ATmega2560__).",
                  "compiler", "by hand: compile every sketch.", firmware_only=True),
            _step("reacher", "firmware/libraries/REACHERDevices/src/Pins.h", "PIN_PUMP_2",
                  "Add PIN_{DEVICE} after the existing pins.", "check:C6",
                  "check_consistency(scopes=['pins'])", firmware_only=True),
            _step("reacher", "firmware/libraries/REACHERDevices/src/Commands.h", "namespace Cmd",
                  "Add the {DEVICE}_ command block in a free range.", "check:C1",
                  "check_consistency(scopes=['commands'])", firmware_only=True),
            _step("reacher", "src/reacher/kernel/commands.py", "COMMAND_REGISTRY: Dict[int, CommandSpec] = {",
                  "Add CommandCode members and registry specs.", "check:C2",
                  "check_consistency(scopes=['commands'])"),
            _step("reacher", "src/reacher/pin_overrides.py", "PIN_CONSTRAINTS: dict[int, PinConstraint] = {",
                  "Add the pin constraint and SET_PIN mapping.", "check:C3",
                  "check_consistency(scopes=['pins'])"),
            _step("reacher", "src/reacher/schema.py", "PIN_SYMBOL_TO_COMPONENT: dict[str, str] = {",
                  "Map PIN_{DEVICE} to the component key.", "check:C4",
                  "check_consistency(scopes=['pins'])"),
            _step("reacher", "src/reacher/kernel/reacher.py", "def update_behavioral_events(self, event: dict) -> None:",
                  "Handle the device's events (and a new log level if it has one: see code_dict).",
                  "test:tests/test_device_names.py", "run_checks(['pytest_parity'])"),
            _step("reacher", "src/reacher/api/routers/file.py", "def _build_behavior_csv(",
                  "Add export columns or files for the device's data.", "none",
                  "by hand: export a simulator session and open the files."),
            _step("reacher", "src/reacher/kernel/simulator.py", "class FirmwareSimulator:",
                  "Simulate the device, including every firmware refusal.", "none",
                  "by hand until golden traces exist."),
            _step("labrynth", "web/src/components/hardware/pinMeta.ts",
                  "export const SET_PIN_CODE: Record<Component, number> = {",
                  "Add the component to every pinMeta table.", "check:C3",
                  "check_consistency(scopes=['pins'])"),
            _step("labrynth", "web/src/types/index.ts", "export interface HardwareUiState {",
                  "Add the device's UI state.", "tsc", "run_checks(['tsc'])"),
            _step("labrynth", "web/src/components/monitor/hardwareSummary.ts",
                  "export const DEVICE_LABELS: Record<string, string> = {",
                  "Label the device for the summaries.", "test:web/tests/hardwareSummary.test.mjs",
                  "run_checks(['web_tests'])"),
            _step("labrynth", "web/src/components/configuration/ConfigurationPanel.tsx",
                  "export function ConfigurationPanel() {",
                  "Mount the new <Device>Control card.", "none", "by hand: the card shows and arms."),
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
