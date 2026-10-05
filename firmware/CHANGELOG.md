# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- `LEVER_RH_SET_TIMEOUT_MODE (1077)` / `LEVER_LH_SET_TIMEOUT_MODE (1377)` — select when the lever timeout window is armed. `0` = every ACTIVE press (the existing behavior, and the default), `1` = only a press that fires the reward chain. Accepted by `fr`, `fr_lite`, `pr`, `pr_lite`, `vi`, `vi_lite`; omission and pavlovian are excluded (omission forces a `0` interval, pavlovian is non-operant and has no lever timeout). Like `1074`/`1374`, the two codes are **not** independent: both write one scheduler-wide flag and the last write wins
- `Scheduler::SetTimeoutMode()` / `TimeoutMode()` and the `TIMEOUT_MODE_EVERY_PRESS` / `TIMEOUT_MODE_REWARD_ONLY` constants (`Scheduler.h`). Runtime state only — no EEPROM; the host re-sends it on every connect, as it does for the timeout interval. Values above `1` are clamped
- `"timeout_mode"` field on the level-`000` CONTROLLER config line emitted at session start by `fr`/`pr`/`vi` and their lite twins
- `Scheduler::ChainAppliesTimeout()` — private helper that identifies a reward chain by the presence of a `SET_TIMEOUT` step. This is what mode `1` keys on, rather than "any trigger fired": in the FR/PR `LASER_RH_ONLY` mode a second trigger fires chain 1 on every active press, and chain 1 has no `SET_TIMEOUT` step, so it correctly is not treated as a reward

### Changed
- `Scheduler::OnInputEvent()` now offers the press to the triggers *before* arming the timeout window. In mode `0` this is behavior-preserving: on a rewarded press the chain's own `SET_TIMEOUT` step writes the same `now + TimeoutInterval()` to the same lever, and nothing inside `FireChain()` reads `timeoutEnd`

### Fixed
- **VI fired no reward chain, ever.** `configureVariableInterval()` in `vi/Config.h` and `vi_lite/Config.h` assigned `t->windowStart = 0; t->windowEnd = 0; t->firedInWindow = false;` — runtime state belonging to `Scheduler::StartSession()` (which seeds the window) and `Trigger::Reset()` (which clears it at `EndSession`). `vi.ino`'s `StartSession()` calls `ReconfigureChain()` *immediately after* `scheduler.StartSession()`, so the helper overwrote the freshly seeded window with zeros. `Trigger::OnTick()` gates its re-arm on `windowEnd > 0`, and nothing else in the firmware writes `windowEnd`, so the zero was permanent: `OnInputEvent()`'s `(int32_t)(windowEnd - timestamp) > 0` evaluated `0 - timestamp` and was always false. The three assignments are removed; the trigger block now sets configuration only (`type`, `chainIndex`, `enabled`, `intervalMin`, `sourceFilter`, `probability`). Removing the writes — rather than reordering `vi.ino` — also fixes the mid-session path, where `SET_VI_INTERVAL (204)` calls `ReconfigureChain()` and would otherwise kill a live window. Regressed in `70fcf35` (2026-06-25, session-start filter-shadow reset), shipped v3.0.1 through v3.4.0-beta.3
- Flash after the fix: `vi` (MEGA) 35920 B / 14%; `vi_lite` (UNO) 30468 B / 94%, down 38 B. `pr_lite` remains the tightest UNO build at 31050 B / 96%. No other hex artifact changed — the untouched sketches recompiled byte-identical

### Notes
- `omission/Config.h` still zeroes `absenceStart` and `fr`/`pr` still zero `pressCount` from the same post-`StartSession` reconfigure. Neither is fatal the way the window trio was: `Trigger::OnInputEvent()` rewrites `absenceStart` on every press, so the omission timer self-heals on the animal's first press, and `pressCount` is already zero at session start. Left alone here rather than bundled into a VI fix — `absenceStart` in particular means the omission interval does not begin counting until the first press, which is a behavior question for the maintainers, not a cleanup
- Regression guard: `tests/test_firmware_parity.py` C16 asserts no paradigm `Config.h` assigns `windowStart`/`windowEnd`/`firedInWindow`, plus a converse check that `Scheduler::StartSession()` still seeds them (so removing the writes cannot silently leave the window uninitialised instead). The existing VI coverage could not catch this: `FirmwareSimulator._run_vi` is a scripted generator that never instantiates the firmware `Trigger` state machine, so it modelled the intended schedule rather than the shipped one and stayed green throughout
- The mode `0` consequence, unchanged but worth stating: a `TIMEOUT`-classified press is logged but never reaches `Trigger::OnInputEvent`, so it does not count toward the ratio. With ratio > 1 and a non-zero timeout the animal must therefore space **every** press by at least the timeout interval to earn anything, and in VI an ACTIVE press just before an availability window opens can cost the reward outright. FR ships `timeout = 0` (`fr/Config.h`) and is immune at defaults; PR and VI ship `20000`
- Not verified on hardware — no rig was available. Behavioral claims here come from code reading plus the host-side simulator

---

## [2.1.0] - 2026-06-09

### Added
- `CUE_SET_LEVER_FILTER (378)`, `CUE2_SET_LEVER_FILTER (388)`, `PUMP_SET_LEVER_FILTER (478)`, `PUMP2_SET_LEVER_FILTER (488)` — per-device lever routing filter commands; accepted by `fr`, `pr`, `vi`, and `omission` sketches; Pavlovian excluded; value 0 = any lever, 1 = RH only, 2 = LH only
- `DeviceType sourceFilter` field on the `Action` struct; `Scheduler` stores `_lastInputSource` in `OnInputEvent()` and skips enqueuing actions in `FireChain()` when the press source does not match the action's filter
- `CUE_SET_ONSET_DELAY (377)`, `CUE2_SET_ONSET_DELAY (387)`, `PUMP_SET_ONSET_DELAY (477)`, `PUMP2_SET_ONSET_DELAY (487)` — per-device onset delay commands (ms from trigger to device activation); sketch-local shadow globals (`CUE_ONSET_DELAY`, `PUMP_ONSET_DELAY`, `PUMP2_ONSET_DELAY`) persist delay across all `ReconfigureChain()` rebuilds; applied as `offsetMs` additive post-fixup after `configureXxx()` in each sketch; accepted by `fr`, `pr`, `vi`, and `omission`; Pavlovian excluded

### Changed
- Lever routing filter implementation rearchitected from trigger-level to action-level: each chain step carries an independent `sourceFilter`; sketch-level shadow globals (`CUE_SOURCE_FILTER`, `PUMP_SOURCE_FILTER`, `PUMP2_SOURCE_FILTER`) persist filter state across all `ReconfigureChain()` rebuilds; `CUE2_SOURCE_FILTER` removed (`CUE_2` is absent from all four operant chain configurations)
- Contingency lever promoted to `ACTIVE` via `SetActiveLever(true)` when a per-device filter is assigned, allowing both levers to count toward the ratio threshold while routing outputs independently
- Board compile target changed from Arduino UNO (ATmega328P, 32 KB flash) to Arduino Mega 2560 (ATmega2560, 256 KB flash); `compile.sh` builds `hex/mega/` only; `hex/uno/` directory removed from repository; `CLAUDE.md` updated to reflect Mega as primary hardware target

### Fixed
- Per-device output filter wiped on every `ReconfigureChain()` call — shadow globals now thread filter state through all `configureXxx()` rebuilds
- LH lever presses not counted toward ratio threshold when an LH-contingent output filter was active — `SetActiveLever(true)` now called at filter assignment time

---

## [2.0.0] - 2025-04-08

_Changelog tracking started at this version. Earlier history not recorded._

### Added
- Unified firmware for five behavioral paradigms: Fixed Ratio (FR), Progressive Ratio (PR), Variable Interval (VI), Omission, and Pavlovian classical conditioning
- Shared `REACHERDevices` C++ library (v2.0.0): `SwitchLever`, `Cue`, `Pump`, `Laser`, `LickCircuit`, `Microscope`, `Scheduler`, `PavlovianScheduler`
- `Scheduler` engine with trigger types: `PRESS_COUNT`, `ABSENCE_TIMER`, `AVAILABILITY_WINDOW`, `MANUAL`
- `PavlovianScheduler` 5-phase trial state machine: `IDLE → ITI → CUE_ON → TRACE → REWARD`; Fisher-Yates shuffle with ≤3 consecutive same-type constraint; ITI sampled from clamped exponential distribution
- Runtime pin and pump overrides via `*_SET_PIN` command family and `SET_ACTIVE_PUMP` (221); pin range clamped to [2, 53]
- Session pause/resume with deadline shifting so paused time does not consume timeouts or absence timers
- Microscope pause/resume across session pause and split events
- 8-second watchdog timer (`wdt_enable(WDTO_8S)`) in all sketches; `wdt_reset()` at top of every `loop()`
- JSON serial protocol at 115200 baud: `*IDN?` handshake, level codes `000/001/006/007/008`, newline-delimited
- Compiled `.hex` artifacts for both Arduino UNO (`hex/uno/`) and Mega 2560 (`hex/mega/`)
- `compile.sh` script compiling all five paradigms for both board targets
- Doxygen configuration for class and function documentation
- `DeviceSet` helpers in `ReacherHelpers.{h,cpp}`: `handleCommonDeviceCommand`, `reportDeviceConfig`, `armToggleDevices`, `captureArmState`/`restoreArmState`

### Changed
- LH lever remapped from pin 12 to pin 13
- Laser gated on `sessionActive` to prevent firing outside session boundaries

### Fixed
- Signed-diff pattern (`(int32_t)(now - deadline) >= 0`) applied to all `millis()` comparisons to survive 49.7-day wrap (Bug 2.2)
- Microscope now uses `Pause`/`Resume` on session end rather than `Trigger` to prevent spurious frame capture
- `armToggleDevices` deferred 200 ms after session end to allow in-flight events to clear
- Deprecated `LASER_SET_TRACE` command (673) removed
- Laser oscillation caused by incorrect pin initialization resolved; `INDEPENDENT` mode gated on session state
- Runtime pump selection via `SET_ACTIVE_PUMP` triggers `ReconfigureChain()` to update action offsets
- Hex artifacts split by board type (`hex/uno/`, `hex/mega/`) for correct backend resolution
