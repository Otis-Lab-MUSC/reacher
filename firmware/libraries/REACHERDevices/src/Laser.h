/**
 * @file Laser.h
 * @brief Optogenetic laser with contingent/independent modes and oscillation.
 * @ingroup output_devices
 */

#ifndef LASER_H
#define LASER_H

#include <Arduino.h>
#include "Device.h"

/// @brief Optogenetic laser with contingent/independent modes and oscillation.
/// @note frequency == 1 means continuous ON (no oscillation).
class Laser : public Device {
public:
  Laser(int8_t pin, uint32_t frequency, uint32_t duration);

  /// @brief Schedule laser activation (CONTINGENT mode only).
  /// @param startTs Start timestamp (millis)
  /// @param dur Duration in ms
  void Activate(uint32_t startTs, uint32_t dur);

  /// @brief Advance laser state machine. Called every loop tick.
  void Await(uint32_t currentTimestamp);

  /// @brief Fire a single test pulse using default duration.
  void Test(uint32_t currentTimestamp);

  /// @brief Reset internal oscillation state for a new session.
  /// Clears timestamps and state flags. Preserves mode, frequency, duration, armed, and sessionActive.
  void Reset();

  /// @brief Set session-active flag (gates INDEPENDENT cycling).
  /// @param active true when session is running, false otherwise
  void SetSessionActive(bool active);

  void SetFrequency(uint32_t frequency);
  void SetDuration(uint32_t duration);
  inline void SetOnsetDelay(uint32_t delayMs) { onsetDelay = delayMs; }
  inline uint32_t OnsetDelay() const { return onsetDelay; }

  /// @brief Set operating mode.
  /// @param contingent true = CONTINGENT (fires only via chain), false = INDEPENDENT (free-running cycle)
  void SetMode(bool contingent);

  uint32_t Frequency() const;
  uint32_t Duration() const;
  bool IsContingent() const;

private:
  uint32_t frequency;               ///< Oscillation frequency in Hz (1 = continuous)
  uint32_t duration;                ///< Default activation duration in ms
  uint32_t onsetDelay;              ///< Delay (ms) from RH press to laser onset in RH-only mode
  uint32_t startTimestamp;          ///< Current activation window start
  uint32_t endTimestamp;            ///< Current activation window end
  uint32_t halfCycleStartTimestamp; ///< Current half-cycle start for oscillation
  uint32_t halfCycleEndTimestamp;   ///< Current half-cycle end for oscillation

  /// @brief Operating mode for the laser.
  enum Mode : uint8_t {
    CONTINGENT,  ///< Fires only via chain activation (behavioral event)
    INDEPENDENT  ///< Cycles continuously on own on/off schedule
  };

  Mode mode;        ///< Current operating mode
  bool state;       ///< True during an active on-period (macro cycle)
  bool halfState;   ///< Current half of oscillation square wave (true = ON half)
  bool isTesting;     ///< True during a manual test pulse
  bool sessionActive; ///< True when a session is running (gates INDEPENDENT cycling)

  void On();
  void Off();
  /// @brief Free-running on/off macro cycle (INDEPENDENT mode).
  void Cycle(uint32_t currentTimestamp);
  /// @brief Square-wave oscillation within an active window.
  void Oscillate(uint32_t currentTimestamp);
  /// @brief Advance to next half-cycle and toggle halfState.
  void UpdateHalfCycle(uint32_t currentTimestamp);
};

#endif // LASER_H
