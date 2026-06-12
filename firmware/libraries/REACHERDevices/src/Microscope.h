/**
 * @file Microscope.h
 * @brief Two-photon microscope sync via trigger pulses and frame-signal ISR.
 * @ingroup devices
 */

#ifndef MICROSCOPE_H
#define MICROSCOPE_H

#include <Arduino.h>

/// @brief Two-photon microscope sync via trigger pulses and frame-signal ISR.
///
/// Not derived from Device. Manages two pins: trigger (output) and timestamp
/// (ISR input on INT0, pin 2).
/// @warning All ISR-accessed fields must be volatile. 32-bit reads require
/// interrupt protection on AVR.
class Microscope {
public:
  Microscope(int8_t triggerPin, int8_t timestampPin);

  /// @brief Static ISR handler for INT0 rising edge. Captures millis() timestamp.
  static void TimestampISR();

  /// @brief Process ISR-captured frame timestamps — call from loop().
  void HandleFrameSignal();

  void ArmToggle(bool armed);
  void SetOffset(uint32_t offset);

  /// @brief Initiate a non-blocking trigger pulse (50ms HIGH).
  void Trigger();

  /// @brief Tick the trigger state machine — call from loop(). Fix: FW-001
  void TickTrigger(uint32_t now);

  /// @brief Reassign the trigger output pin at runtime.
  /// Drives the old pin LOW (terminating any in-flight trigger pulse),
  /// applies pinMode(newPin, OUTPUT), and emits a level-`000` config event.
  /// The timestamp pin is intentionally NOT remappable — see comment on
  /// `timestampPin` below.
  void SetTriggerPin(int8_t newPin);

  /// @brief Stop scope scanning on session pause; no-op if disarmed or already paused.
  void Pause(uint32_t now);

  /// @brief Restart scope scanning on session resume and advance offset by the
  /// paused interval so emitted timestamps stay session-live. No-op if disarmed
  /// or not currently paused.
  void Resume(uint32_t now);

  bool Armed() const;
  bool Paused() const;
  byte TriggerPin() const;
  byte TimestampPin() const;

private:
  int8_t triggerPin;              ///< Output pin for trigger pulse (runtime-remappable via SetTriggerPin)
  // The timestamp pin is fixed at INT0 (UNO pin 2 / Mega pin 2). Remapping
  // to INT1 (pin 3) is the only UNO alternative and collides with PIN_CUE
  // (PWM). Mega has additional INT pins (18-21) but cross-board portability
  // is preserved by keeping this pin fixed.
  int8_t timestampPin;            ///< ISR input pin (INT0, pin 2 — fixed)
  volatile bool received;         ///< ISR flag: true when new frame signal captured
  bool armed;                     ///< True when microscope logging is active
  volatile uint32_t timestamp;    ///< ISR-captured frame timestamp (session-relative)
  volatile uint32_t offset;       ///< Session start offset (volatile — read in ISR)

  // Fix: FW-001 — Non-blocking trigger state machine
  bool triggerActive;             ///< True while trigger pulse is HIGH
  uint32_t triggerStart;          ///< millis() when trigger went HIGH
  static constexpr uint32_t TRIGGER_DURATION_MS = 50;

  bool paused;                    ///< True while session is paused (scope stopped)
  uint32_t pauseStart;            ///< millis() when pause began, for offset compensation

  static Microscope* instance;    ///< Singleton for ISR dispatch

  /// @brief Serialize frame timestamp to serial JSON (level 008).
  void LogOutput(uint32_t ts);
};

#endif // MICROSCOPE_H
