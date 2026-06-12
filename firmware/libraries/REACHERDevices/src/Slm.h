/**
 * @file Slm.h
 * @brief SLM timestamp capture via PCINT0 rising-edge ISR on a configurable pin.
 * @ingroup devices
 */

#ifndef SLM_H
#define SLM_H

#include <Arduino.h>

/// @brief SLM timestamp input via PCINT0 rising-edge detection.
///
/// Input-only device (no trigger output). Listens for rising edges on a
/// configurable pin in the PCINT0 group (Arduino pins 8–13, PB0–PB5).
/// Because PCINT0_vect fires on any PB port change, HandlePCINT() reads
/// the actual pin state and compares to lastPinState to detect only
/// RISING edges — preventing spurious captures from other PB pins
/// (notably lever pins 10/13 which share this vector).
///
/// @warning All ISR-accessed fields must be volatile. 32-bit reads require
/// interrupt protection on AVR.
class Slm {
public:
  explicit Slm(int8_t timestampPin);

  /// @brief Global PCINT0 ISR dispatches here. Checks for RISING edge on
  /// the configured pin only.
  void HandlePCINT();

  /// @brief Process ISR-captured SLM timestamps — call from loop().
  void HandleTimestampSignal();

  void ArmToggle(bool armed);
  void SetOffset(uint32_t offset);

  /// @brief Reassign the timestamp input pin at runtime (pins 8–13 only).
  /// Disables the old PCINT0 bit, sets INPUT_PULLUP on the new pin,
  /// enables the new PCINT0 bit, and emits a level-000 config event.
  void SetPin(int8_t newPin);

  bool Armed() const;
  byte TimestampPin() const;

  static Slm* instance;  ///< Singleton for ISR dispatch

private:
  int8_t timestampPin;            ///< ISR input pin (PCINT0 group, pins 8–13)
  volatile bool received;         ///< ISR flag: true when new SLM timestamp captured
  bool armed;                     ///< True when SLM logging is active
  volatile uint32_t timestamp;    ///< ISR-captured timestamp (session-relative ms)
  volatile uint32_t offset;       ///< Session start offset (volatile — read in ISR)
  volatile bool lastPinState;     ///< Previous PINB state for the pin — RISING guard

  /// @brief Serialize SLM timestamp to serial JSON (level 009).
  void LogOutput(uint32_t ts);
};

#endif // SLM_H
