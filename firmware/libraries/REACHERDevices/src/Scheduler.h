/**
 * @file Scheduler.h
 * @brief Central contingency engine — classifies presses, fires chains, manages pending actions.
 * @ingroup scheduling
 */

#ifndef SCHEDULER_H
#define SCHEDULER_H

#include <Arduino.h>
#include "Device.h"
#include "Trigger.h"
#include "Action.h"

/// Forward declaration (full include in .cpp to avoid circular deps)
class SwitchLever;
/// Forward declaration
class LickCircuit;
/// Forward declaration
class Cue;
/// Forward declaration
class Pump;
/// Forward declaration
class Laser;
/// Forward declaration
class Microscope;

/// Maximum number of configurable triggers.
static constexpr uint8_t MAX_TRIGGERS = 2;
/// Maximum number of reward chains.
static constexpr uint8_t MAX_CHAINS   = 2;
/// Maximum number of deferred actions in the pending queue.
static constexpr uint8_t MAX_PENDING  = 16;

/// @brief Central contingency engine: classifies lever presses, fires reward chains,
/// manages pending action queue and device tick loop.
/// @see Trigger, Chain, Action, Config.h
class Scheduler {
public:
  Scheduler();

  /// @brief Register a lever with the scheduler.
  /// @param lever Pointer to the SwitchLever instance
  /// @param type DeviceType::LEVER_RH or DeviceType::LEVER_LH
  void RegisterLever(SwitchLever* lever, DeviceType type);

  /// @brief Register the lick circuit.
  /// @param lick Pointer to the LickCircuit instance
  void RegisterLickCircuit(LickCircuit* lick);

  /// @brief Register the primary cue.
  void RegisterCue(Cue* cue);
  /// @brief Register the secondary cue.
  void RegisterCue2(Cue* cue2);
  /// @brief Register the primary pump.
  void RegisterPump(Pump* pump);
  /// @brief Register the secondary pump.
  void RegisterPump2(Pump* pump2);
  /// @brief Register the laser.
  void RegisterLaser(Laser* laser);
  /// @brief Register the microscope.
  void RegisterMicroscope(Microscope* mic);

  /// @brief Main loop tick — advances triggers, pending queue, and output devices.
  /// @param now Current millis() value
  void Update(uint32_t now);

  /// @brief Handle a lever press event from an input device callback.
  /// @param source DeviceType of the lever that was pressed
  /// @param timestamp millis() at press time
  void OnInputEvent(DeviceType source, uint32_t timestamp);

  /// @brief Handle a lever release event from an input device callback.
  /// @param source DeviceType of the lever that was released
  void OnInputRelease(DeviceType source);

  /// @brief Get a pointer to a trigger by index.
  /// @param index Trigger index (0 to MAX_TRIGGERS-1)
  /// @return Pointer to the trigger, or nullptr if out of range
  Trigger* GetTrigger(uint8_t index);

  /// @brief Get a pointer to a chain by index.
  /// @param index Chain index (0 to MAX_CHAINS-1)
  /// @return Pointer to the chain, or nullptr if out of range
  Chain*   GetChain(uint8_t index);

  /// @brief Set the timeout interval and update all SET_TIMEOUT chain steps.
  void     SetTimeoutInterval(uint32_t interval);

  /// @brief Set the press ratio for the first PRESS_COUNT trigger found.
  void     SetRatio(uint8_t ratio);

  /// @brief Deactivate all pending actions in the queue.
  void ClearPending();

  /// @brief Initialize session state: reset triggers, pending queue, and lever timeouts.
  /// @param now Session start timestamp (millis)
  void StartSession(uint32_t now);

  /// @brief End session: clear pending queue and force all outputs off.
  /// @param now Session end timestamp (millis)
  void EndSession(uint32_t now);

  /// @brief Fire chain 0 for pre-session testing (respects armed state).
  /// @param now Current millis() value
  void TestChain(uint32_t now);

  /// @brief Pause or resume the session (gates inputs, triggers, and pending actions).
  /// @param paused True to pause, false to resume
  /// @param now Current millis() value
  void SetPaused(bool paused, uint32_t now);

  /// @brief Check if session is currently paused.
  bool IsPaused() const;

  /// @brief Enable/disable full-pipeline test mode (lever->trigger->chain pre-session).
  /// @param enable True to enter test mode, false to exit
  /// @param now Current millis() value
  void SetTestMode(bool enable, uint32_t now);

  /// @brief Check if test mode is active.
  bool IsTestMode() const;

  uint32_t TimeoutInterval() const;
  uint32_t SessionOffset() const;

  /// @brief Check if a session is currently active.
  bool IsSessionActive() const;

private:
  // Device pointers
  SwitchLever* leverRH;     ///< Right-hand lever
  SwitchLever* leverLH;     ///< Left-hand lever
  LickCircuit* lickCircuit;  ///< Lick detection sensor
  Cue*         cue;          ///< Primary tone output
  Cue*         cue2;         ///< Secondary tone output
  Pump*        pump;         ///< Primary syringe pump
  Pump*        pump2;        ///< Secondary syringe pump
  Laser*       laser;        ///< Optogenetic laser
  Microscope*  microscope;   ///< Microscope sync interface

  // Triggers and chains
  Trigger triggers[MAX_TRIGGERS];  ///< Configurable trigger conditions
  Chain   chains[MAX_CHAINS];      ///< Reward action sequences

  // Pending action queue
  PendingAction pending[MAX_PENDING];  ///< Deferred action slots

  // Session state
  uint32_t sessionOffset;    ///< millis() at session start (for relative timestamps)
  uint32_t timeoutInterval;  ///< Post-reward timeout duration in ms
  bool     sessionActive;    ///< True between StartSession/EndSession
  bool     testMode;         ///< True when full-pipeline test mode is active
  bool     sessionPaused;    ///< True when session is paused via SESSION_PAUSE
  uint32_t pauseStart;       ///< millis() when pause began

  PressClass lastPressClassRH;  ///< Classification stored on press-down, logged on release
  PressClass lastPressClassLH;  ///< Classification stored on press-down, logged on release
  DeviceType _lastInputSource;  ///< Lever source of the most recent OnInputEvent call

  /// @brief Classify a lever press as ACTIVE, INACTIVE, or TIMEOUT.
  PressClass ClassifyPress(DeviceType source, uint32_t timestamp);
  /// @brief Execute all steps in a chain (immediate or deferred).
  void FireChain(uint8_t chainIndex, uint32_t now);
  /// @brief Dispatch a single action to the appropriate device.
  void ExecuteAction(const Action& action, uint32_t now);
  /// @brief Check and execute any pending actions whose time has come.
  void TickPending(uint32_t now);
  /// @brief Advance all output device state machines (Await calls).
  void TickOutputs(uint32_t now);
  /// @brief Serialize lever press event to serial JSON (level 007).
  void LogLeverPress(DeviceType source, PressClass cls);
  /// @brief Serialize device activation event to serial JSON (level 007).
  void LogDeviceActivation(DeviceType target, uint32_t startTs, uint32_t endTs);
  /// @brief Queue an action for deferred execution.
  void SchedulePending(const Action& action, uint32_t fireTime);
  /// @brief Resolve a DeviceType to its SwitchLever pointer.
  SwitchLever* GetLever(DeviceType type);
};

#endif // SCHEDULER_H
