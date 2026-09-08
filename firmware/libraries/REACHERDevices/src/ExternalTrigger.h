/**
 * @file ExternalTrigger.h
 * @brief External TTL session-start input on a Mega external-interrupt pin.
 * @ingroup devices
 */

#ifndef EXTERNAL_TRIGGER_H
#define EXTERNAL_TRIGGER_H

#include <Arduino.h>

/// @brief External TTL input that starts a session on a rising edge.
///
/// Input-only device (no output pin). When armed, a rising edge on the
/// configured pin latches a flag in the ISR; the sketch's loop() consumes it
/// via Consume() and runs the normal StartSession() path, so an externally
/// triggered session is byte-identical to a software-started one apart from
/// the "source" field on the level-`007` START event.
///
/// **One-shot by construction.** Consume() self-disarms before returning true.
/// A session start is not a repeatable event, and leaving the interrupt live
/// would let a stray edge re-enter StartSession() mid-run — which re-pulses the
/// microscope trigger (a toggle, not a level) and would stop the scope
/// scanning. Re-arming is an explicit operator action.
///
/// **Pin policy.** Assignable only to 18/19/20/21 (Mega INT5/INT4/INT3/INT2).
/// INT0 (pin 2) is excluded because it carries Microscope::TimestampISR:
/// attachInterrupt() *replaces* the handler for a pin, so allowing pin 2 would
/// silently kill two-photon frame logging with no error anywhere. INT1 (pin 3)
/// is the cue output. See Pins.h for the Serial1/I2C caveat on 18-21.
///
/// Not derived from Device: it has no arm-for-the-session semantics, is not a
/// member of DeviceSet, and must survive armToggleDevices(false) at session end.
///
/// @warning All ISR-accessed fields must be volatile.
class ExternalTrigger {
public:
  explicit ExternalTrigger(int8_t pin);

  /// @brief Static ISR handler for the rising edge. Latches `fired` only.
  static void TriggerISR();

  /// @brief Consume a latched edge — call from loop().
  /// @return true exactly once per armed edge; self-disarms before returning.
  bool Consume();

  /// @brief Arm or disarm the trigger. Attaches/detaches the interrupt and
  /// emits a level-`001` state event carrying the current pin.
  void ArmToggle(bool armed);

  /// @brief Reassign the input pin at runtime. Rejects any pin outside
  /// 18/19/20/21 with a level-`006` error rather than silently substituting.
  /// Re-attaches the interrupt on the new pin if currently armed.
  void SetPin(int8_t newPin);

  bool Armed() const;
  byte Pin() const;

  /// @brief True if `pin` is one of the four assignable external-interrupt pins.
  static bool IsAssignablePin(int8_t pin);

  static ExternalTrigger* instance;  ///< Singleton for ISR dispatch

private:
  int8_t pin;              ///< TTL input pin (18/19/20/21)
  volatile bool fired;     ///< ISR flag: true when a rising edge was latched
  // Written in main context only, but read by TriggerISR — volatile per the
  // class warning above, since this core builds with -flto.
  volatile bool armed;     ///< True while the interrupt is attached

  void attach();
  void detach();

  /// @brief Serialize arm state to serial JSON (level 001).
  void LogState() const;
};

#endif // EXTERNAL_TRIGGER_H
