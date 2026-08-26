/**
 * @file PavlovianScheduler.h
 * @brief Timer-driven classical conditioning trial engine (ITI -> CUE -> TRACE -> REWARD).
 */

#ifndef PAVLOVIAN_SCHEDULER_H
#define PAVLOVIAN_SCHEDULER_H

#include <Arduino.h>
#include <Device.h>

/// Forward declarations
class SwitchLever;
class LickCircuit;
class Cue;
class Pump;
class Laser;
class Microscope;

/// @brief Which trial types the Pavlovian laser fires on.
enum class LaserTrialFilter : uint8_t { CS_PLUS, CS_MINUS, CS_BOTH };
/// @brief Which phase the Pavlovian laser fires during.
enum class LaserPhase : uint8_t { REWARD, CUE };

/// Maximum number of Pavlovian trials per session.
static constexpr uint8_t MAX_PAVLOV_TRIALS = 128;
/// Byte count for bit-packed trial type array.
static constexpr uint8_t TRIAL_ARRAY_BYTES = MAX_PAVLOV_TRIALS / 8;  // 16

/// @brief Classification of a lever press event.
enum class PressClass : uint8_t {
  ACTIVE,    ///< Press on reinforced lever outside timeout
  INACTIVE,  ///< Press on non-reinforced lever
  TIMEOUT    ///< Press during timeout period
};

/// @brief Pavlovian trial phase state machine states.
enum class PavlovPhase : uint8_t {
  IDLE,        ///< Not running or session inactive
  ITI,         ///< Inter-trial interval
  CUE_ON,      ///< CS tone playing
  TRACE,       ///< Silent delay between cue offset and reward
  REWARD       ///< Reward delivery + consumption period
};

/// @brief Timer-driven classical conditioning scheduler.
///
/// Manages CS+/CS- trials with Fisher-Yates shuffle (max-3-consecutive constraint),
/// exponential ITI distribution, and phase-based state machine. Lever presses are
/// logged but do not affect trial progression.
class PavlovianScheduler {
public:
  PavlovianScheduler();

  /// @brief Register a lever with the scheduler.
  void RegisterLever(SwitchLever* lever, DeviceType type);
  /// @brief Register the lick circuit.
  void RegisterLickCircuit(LickCircuit* lick);
  /// @brief Register the primary cue (CS+ continuous tone).
  void RegisterCue(Cue* cue);
  /// @brief Register the secondary cue (CS- pulsed tone).
  void RegisterCue2(Cue* cue2);
  /// @brief Register the primary pump.
  void RegisterPump(Pump* pump);
  /// @brief Register the secondary pump.
  void RegisterPump2(Pump* pump2);
  /// @brief Register the laser.
  void RegisterLaser(Laser* laser);
  /// @brief Register the microscope.
  void RegisterMicroscope(Microscope* mic);

  /// @brief Set which trial types the laser fires on.
  void SetLaserTrialFilter(LaserTrialFilter filter);
  /// @brief Set which phase the laser fires during.
  void SetLaserPhase(LaserPhase phase);

  /// @brief Main loop tick — advances trial state machine and output devices.
  void Update(uint32_t now);

  /// @brief Handle a lever press event (logged but does not affect trial progression).
  void OnInputEvent(DeviceType source, uint32_t timestamp);
  /// @brief Handle a lever release event (logs the press).
  void OnInputRelease(DeviceType source);

  /// @brief Configure all Pavlovian trial parameters.
  void Configure(uint8_t csPlusCount, uint8_t csMinusCount,
                 uint32_t csPlusFreq, uint32_t csMinusFreq,
                 uint16_t cueDuration, uint16_t traceInterval, uint16_t consumptionMs,
                 uint32_t itiMean, uint32_t itiMin, uint32_t itiMax,
                 uint8_t csPlusProb, uint8_t csMinusProb, bool counterbalance);

  /// @brief Check if a session is currently active.
  bool IsSessionActive() const;

  /// @brief True when session started but all trials have finished (IDLE after completion).
  bool IsComplete() const;

  /// @brief Pause or resume the session (gates trial progression and inputs).
  /// @param paused True to pause, false to resume (adjusts phase timer)
  /// @param now Current millis() value
  void SetPaused(bool paused, uint32_t now);

  /// @brief Initialize session state and generate trial order.
  void StartSession(uint32_t now);
  /// @brief End session and force all outputs off.
  void EndSession(uint32_t now);

  uint32_t SessionOffset() const;

private:
  // Device pointers
  SwitchLever* leverRH;
  SwitchLever* leverLH;
  LickCircuit* lickCircuit;
  Cue*         cue;         ///< CS+ cue (continuous tone)
  Cue*         cue2;        ///< CS- cue (pulsed tone)
  Pump*        pump;        ///< CS+ reward pump
  Pump*        pump2;       ///< CS- reward pump
  Laser*       laser;
  Microscope*  microscope;

  LaserTrialFilter laserTrialFilter;
  LaserPhase       laserPhase;

  // Session state
  uint32_t sessionOffset;
  bool     sessionActive;
  bool     sessionPaused;    ///< True when session is paused via SESSION_PAUSE
  uint32_t pauseStart;       ///< millis() when pause began

  PressClass lastPressClassRH;
  PressClass lastPressClassLH;

  // Trial parameters
  uint8_t  pavCsPlusCount;
  uint8_t  pavCsMinusCount;
  uint8_t  pavCsPlusProb;       ///< CS+ reward probability (0-100)
  uint8_t  pavCsMinusProb;      ///< CS- reward probability (0-100)
  bool     pavCounterbalance;   ///< Swap CS+/CS- cue assignments
  uint16_t pavCueDuration;
  uint16_t pavTraceInterval;
  uint16_t pavConsumptionMs;
  uint32_t pavItiMean;
  uint32_t pavItiMin;
  uint32_t pavItiMax;

  // Trial state machine
  PavlovPhase  pavPhase;
  uint8_t      pavTrialIndex;
  uint8_t      pavTotalTrials;
  bool         pavRewardThisTrial;
  uint32_t     pavPhaseStart;
  uint32_t     pavCurrentIti;
  uint8_t      pavTrialTypes[TRIAL_ARRAY_BYTES];  ///< Bit-packed: 0=CS+, 1=CS-

  /// @brief Core trial state machine tick.
  void PavTick(uint32_t now);
  /// @brief Activate cue, resolve reward, log trial metadata.
  void PavStartTrial(uint32_t now);
  /// @brief Fisher-Yates shuffle with max-3-consecutive constraint.
  void PavGenerateTrialOrder();
  /// @brief Sample ITI from clamped exponential distribution.
  uint32_t PavSampleIti();
  /// @brief Read trial type bit from packed array.
  bool PavIsCSMinus(uint8_t index) const;
  /// @brief Write trial type bit in packed array.
  void PavSetTrialType(uint8_t index, bool isCsMinus);

  /// @brief Classify a lever press as ACTIVE or INACTIVE.
  PressClass ClassifyPress(DeviceType source);
  /// @brief Resolve a DeviceType to its SwitchLever pointer.
  SwitchLever* GetLever(DeviceType type);
  /// @brief Advance output device state machines (Cue::Await / Pump::Await / Laser::Await).
  void TickOutputs(uint32_t now);
  /// @brief Check whether the laser should fire for this trial type.
  bool ShouldFireLaser(bool isCsMinus) const;

  /// @brief Serialize lever press event to serial JSON (level 007).
  void LogLeverPress(DeviceType source, PressClass cls);
  /// @brief Serialize device activation event to serial JSON (level 007).
  void LogDeviceActivation(DeviceType target, uint32_t startTs, uint32_t endTs);
  /// @brief Log a Pavlovian event (TRACE_START, REWARD_DELIVERED, etc.).
  void LogPavlovianEvent(const __FlashStringHelper* event, uint32_t now);
  /// @brief Log Pavlovian trial start metadata.
  void LogPavlovianTrial(uint32_t now, bool isCsMinus);
};

#endif // PAVLOVIAN_SCHEDULER_H
