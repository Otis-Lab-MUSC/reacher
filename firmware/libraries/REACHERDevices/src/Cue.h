/**
 * @file Cue.h
 * @brief Tone output device using Arduino tone().
 * @ingroup output_devices
 */

#ifndef CUE_H
#define CUE_H

#include <Arduino.h>
#include "Device.h"

/// @defgroup output_devices Output Devices
/// @{

/// @brief Tone output device using Arduino tone().
/// @warning tone() uses Timer2 (singleton on ATmega328P). Dual cues in PAVLOVIAN
/// mode cannot overlap — the second tone() call silently kills the first.
class Cue : public Device {
public:
  Cue(int8_t pin, uint32_t frequency, uint32_t duration);

  /// @brief Schedule tone playback for a time window.
  /// @param startTs Start timestamp (millis)
  /// @param dur Duration in ms
  void Activate(uint32_t startTs, uint32_t dur);

  /// @brief Turns off tone after duration elapses. Called every loop tick.
  void Await(uint32_t currentTimestamp);

  /// @brief Startup confirmation tone sequence (three ascending tones).
  void Jingle();

  /// @brief Fire a test tone for configured duration (bypasses armed check).
  void Test(uint32_t currentTimestamp);

  void SetFrequency(uint32_t frequency);
  void SetDuration(uint32_t duration);
  uint32_t Frequency() const;
  uint32_t Duration() const;

  /// @brief Enable or disable pulsed tone mode (e.g. for CS- in Pavlovian).
  /// @param pulsed true to enable pulsed mode
  /// @param onMs Pulse ON duration in ms (default 200)
  /// @param offMs Pulse OFF duration in ms (default 200)
  void SetPulsed(bool pulsed, uint16_t onMs = 200, uint16_t offMs = 200);
  bool IsPulsed() const;

private:
  uint32_t frequency;       ///< Tone frequency in Hz
  uint32_t duration;        ///< Default tone duration in ms
  uint32_t startTimestamp;  ///< Active window start
  uint32_t endTimestamp;    ///< Active window end
  bool     playing;         ///< Edge-only Off() flag to prevent cross-Cue noTone() kills
  bool     pulsed;          ///< Pulsed tone mode (on/off cycling)
  bool     pulseIsOn;       ///< Current pulse state (edge detection for tone/noTone)
  uint16_t pulseOnMs;       ///< Pulse ON duration in ms
  uint16_t pulseOffMs;      ///< Pulse OFF duration in ms
  bool     isTesting;       ///< Bypass armed check for pre-session test

  void On();
  void Off();
};

/// @}

#endif // CUE_H
