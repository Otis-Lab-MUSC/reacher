# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Operant-conditioning firmware for Arduino Mega 2560 (ATmega2560, 8 KB RAM, 256 KB flash). Five sketches share one C++ library and produce five `.hex` files consumed by the REACHER backend.

This `firmware/` tree lives inside the `reacher` repo (folded in from the now-archived `Otis-Lab-MUSC/reacher-firmware`). `compile.sh` writes hex artifacts up into the backend package-data directory `../src/reacher/hex/<board>/`, which is committed and shipped in the `reacher` wheel. For the wider Workbench (paradigm names, command-code ranges, event levels, serial framing), see `../../CLAUDE.md` (workspace) and `../CLAUDE.md` (backend). The README.md here is the canonical reference for hardware pinout, paradigm semantics, command-code list, and Pavlovian parameters — read it before authoring any sketch-level changes.

## Commands

```bash
./compile.sh                        # builds all 5 paradigms (mega) + fr_lite (uno) -> ../src/reacher/hex/{mega,uno}/<paradigm>.hex
arduino-cli core install arduino:avr  # one-time: install AVR toolchain
doxygen Doxyfile                    # regenerate docs/ (git-ignored)

# Single sketch (FQBN: arduino:avr:mega:cpu=atmega2560):
arduino-cli compile --fqbn arduino:avr:mega:cpu=atmega2560 --libraries libraries --output-dir ../src/reacher/hex/mega fr/fr.ino
# arduino-cli emits <sketch>.ino.hex; compile.sh renames to <sketch>.hex — match that when scripting

# Manual upload (backend normally handles this; path includes the board subdir):
arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:mega:cpu=atmega2560 --input-file ../src/reacher/hex/mega/fr.hex
```

There is no Arduino-side test framework — verification is done in-loop on hardware. From the backend, `tests/test_command_parity.py` asserts `Commands.h` matches the Python `CommandCode` enum. The committed `../src/reacher/hex/<board>/*.hex` files are tracked artifacts the backend ships; **recompile and commit them** when firmware logic or library code changes (run `bash compile.sh`). The `uno/` hex set includes the new `fr_lite.hex` (built by compile.sh); the legacy uno hex artifacts for the five full paradigms (fr.hex, pr.hex, vi.hex, omission.hex, pavlovian.hex) are left untouched in `uno/` for backwards compatibility.

**Versioning is coupled to the `reacher` package version.** `library.properties` and each sketch's `SendIdentification()` version string are stamped by `../scripts/bump-version.py`; after a bump, recompile the hex so the shipped binaries report the new version.

## Architecture

### Sketch ↔ shared-library split

Each paradigm directory (`fr/`, `pr/`, `vi/`, `omission/`, `pavlovian/`) is an Arduino sketch with the same skeleton:

1. Instantiate devices at the pins declared in `libraries/REACHERDevices/src/Pins.h`: two `SwitchLever`s (RH/LH), two `Cue`s (`cue`, `cue2`), two `Pump`s (`pump`, `pump2`), `LickCircuit`, `Laser`, `Microscope`. Pin assignments are compile-time defaults; they can be remapped at runtime (see "Runtime pin & pump overrides" below).
2. Hold an `activePump*` pointer (default `&pump`) and an `activePumpTarget` `DeviceType`. The chain configuration uses these so `SET_ACTIVE_PUMP` (221) can swap pumps mid-session — every `configureXxx()` call must take the active pump, not `pump` directly.
3. Build a `DeviceSet` aggregate (from `ReacherHelpers.h`) of pointers — pass `nullptr` for unused devices (e.g. `laser` in pavlovian).
4. Register devices with the paradigm's scheduler instance.
5. In `loop()`: `wdt_reset()` → `Monitor()` inputs → `scheduler.Update(now)` → `microscope.HandleFrameSignal()` → `microscope.TickTrigger(now)` → `ParseCommands()`.

The 8-second watchdog (`wdt_enable(WDTO_8S)`) is enabled at the end of `setup()` — every sketch must call `wdt_reset()` first thing in `loop()`.

### Two scheduling engines

| Engine | Used by | File | Mechanism |
|---|---|---|---|
| `Scheduler` | fr, pr, vi, omission | `libraries/REACHERDevices/src/Scheduler.{h,cpp}` | Trigger → Chain → Action with a deferred-action queue |
| `PavlovianScheduler` | pavlovian only | `pavlovian/PavlovianScheduler.{h,cpp}` | 5-phase trial state machine: `IDLE → ITI → CUE_ON → TRACE → REWARD` |

The shared `Scheduler` exposes `MAX_TRIGGERS=2`, `MAX_CHAINS=2`, `MAX_CHAIN_STEPS=6`, `MAX_PENDING=16`. Each paradigm wires up its specific trigger/chain config in its own `Config.h` via a `configureXxx()` helper (see `fr/Config.h::configureFixedRatio` for the canonical pattern). The four operant `TriggerType`s — `PRESS_COUNT`, `ABSENCE_TIMER`, `AVAILABILITY_WINDOW`, `MANUAL` — map onto FR/PR, Omission, VI, and the test-chain command respectively. PR is implemented by setting `prStep > 0` on a `PRESS_COUNT` trigger (arithmetic, not Richardson-Roberts).

### Command parsing pattern

Every sketch's `ParseCommands()` does:

1. `Serial.readBytesUntil('\n', buf, 128)` then `*IDN?` short-circuit.
2. `deserializeJson()` — on parse error, log a level-`006` event and drain serial.
3. `handleCommonDeviceCommand(devices, command, inputJson)` — handles cue/pump/lick/laser/microscope arm/disarm/test/set-frequency/set-duration. **Returns `true` if handled.**
4. If true, the sketch's `switch` updates **shadow variables** (e.g. `CUE_DURATION`) for any duration changes, then calls a sketch-local `ReconfigureChain()` to rebuild the chain timing — chains store absolute offsets, so changing a duration mid-session requires re-applying the `configureXxx()` helper with the new value.
5. If false, the sketch handles paradigm-specific (lever, session-setup, controller) commands directly.

When adding a new command code, edit `libraries/REACHERDevices/src/Commands.h` and update every sketch that should respond — there is no central dispatcher.

### Time-arithmetic convention

All timestamp comparisons use the signed-diff pattern, e.g. `(int32_t)(now - deadline) >= 0` rather than `now >= deadline`, to remain correct across the 49.7-day `millis()` wrap. See `Trigger::OnTick` and the `Scheduler.cpp:232` reference noted in code comments (Bug 2.2). Preserve this pattern in new time checks.

### Press classification & lever callbacks

Levers fire callbacks via function pointers (`SetCallback` / `SetReleaseCallback`) → sketch-local `onLeverPress` / `onLeverRelease` → forwards to `scheduler.OnInputEvent` / `OnInputRelease`. The scheduler classifies each press at press-down time (`ACTIVE` / `INACTIVE` / `TIMEOUT`), stores the class in `lastPressClass{RH,LH}`, and logs it on release — so that reported timestamps reflect a complete press-release cycle. `PressClass` is defined twice (in `Trigger.h` for the operant scheduler, in `PavlovianScheduler.h` for pavlovian) — they are independent enums in different translation units.

### Session pause / resume

`Cmd::SESSION_PAUSE` (105) gates trigger evaluation, pending-action firing, and microscope frame capture. The scheduler tracks `pauseStart` and shifts deadlines on resume so that paused time does not consume timeouts or absence timers. The microscope is paused/resumed independently via `microscope.Pause(now)` / `microscope.Resume(now)` from each sketch's pause handler.

### DeviceSet helpers

`ReacherHelpers.{h,cpp}` provides cross-paradigm helpers operating on a `DeviceSet`: `setDeviceTimestampOffset`, `armToggleDevices`, `captureArmState` / `restoreArmState` (preserve arm state across session boundaries), `reportDeviceConfig` overloads for the level-`000` config dump emitted at session start, and `handleCommonDeviceCommand`. Adding a device-class command should go through `handleCommonDeviceCommand` so all sketches inherit it.

### Runtime pin & pump overrides

The library exposes per-device pin reassignment via the `*_SET_PIN` family (all suffixed `x76`: `CUE_SET_PIN=376`, `CUE2_SET_PIN=386`, `PUMP_SET_PIN=476`, `PUMP2_SET_PIN=486`, `LICK_SET_PIN=576`, `LASER_SET_PIN=676`, `MICROSCOPE_SET_TRIG_PIN`, `LEVER_RH_SET_PIN=1076`, `LEVER_LH_SET_PIN=1376`). `handleCommonDeviceCommand` clamps the pin number to `[2, 53]` and calls `Device::SetPin` / `SwitchLever::SetPin` / `Microscope::SetTriggerPin`, which re-applies `pinMode` and clears state. The backend is responsible for validating board/role/collision rules — firmware only enforces the numeric range. `SET_ACTIVE_PUMP` (221) is handled in the sketch (not in `handleCommonDeviceCommand`) because it swaps the sketch-local `activePump`/`activePumpTarget` and must trigger `ReconfigureChain()`. Both override paths are intended to run at session start before arming.

## Conventions

- **Memory**: avoid `String`, prefer `F("...")` flash strings for all literal serial output, keep new arrays inside the existing `MAX_*` budgets. Target board is Mega 2560 (ATmega2560, 8 KB RAM / 256 KB flash).
- **Serial**: print one JSON object per line, terminated with `\n`. Use the existing level conventions (`000` config / `001` state / `006` error / `007` behavioral / `008` frame).
- **Versioning**: `library.properties` and the `version` field in each sketch's `SendIdentification()` must match — both are stamped from the `reacher` package version by `../scripts/bump-version.py`. Do not hand-edit; recompile hex after a bump.
- **Hex artifacts**: `hex/mega/<paradigm>.hex` is committed. The companion `*.ino.eep` and `*.ino.with_bootloader.bin` files are also tracked (`.gitignore` does not exclude them); leave them in place unless you are recompiling.
- **Bug-fix tags**: in-code comments like `Fix: FW-001` / `Bug 2.2` reference issues tracked outside the repo — preserve them when editing surrounding code.
