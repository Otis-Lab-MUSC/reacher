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
/// configurable pin in the PCINT0 group (PORTB). On the Mega 2560 target
/// board these are pins 10–13 (PB4–PB7); the default is pin 11 (PB5).
/// The pin→register mapping is derived from the Arduino core macros
/// (digitalPinToPCMSK / digitalPinToBitMask), so this stays correct on any
/// board rather than assuming the UNO PB0–PB5 == pins 8–13 layout.
/// PCINT hardware cannot distinguish edge direction — a pin enabled in PCMSK0
/// interrupts on both rising and falling transitions. HandlePCINT() therefore
/// reads the actual pin level and compares it to lastPinState so only the
/// RISING half is captured. Note this guard is NOT what keeps other PORTB
/// devices out: PCMSK0 gates which pins can raise PCINT0_vect at all, and the
/// levers on pins 10/13 are polled via digitalRead() in loop() rather than
/// registered in PCMSK0, so they never reach this ISR — unless the SLM is
/// itself assigned to pin 10 or 13, in which case every lever edge is a
/// genuine capture on the same physical pin. Backend-side collision
/// validation is what prevents that.
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

  /// @brief Reassign the timestamp input pin at runtime (PCINT0/PORTB pins
  /// only; 10–13 on the Mega). Rejects non-PCINT0 pins with a level-006
  /// error because the ISR vector is fixed to PCINT0_vect. On success,
  /// disables the old PCINT0 bit, sets INPUT_PULLUP on the new pin, enables
  /// the new PCINT0 bit, and emits a level-000 config event.
  void SetPin(int8_t newPin);

  /// @brief Record the laser frequency (Hz) this SLM sync corresponds to.
  /// Bookkeeping only — not tied to actual LASER control.
  void SetLaserFrequency(uint32_t hz);
  /// @brief Record the laser duration (ms) this SLM sync corresponds to.
  /// Bookkeeping only — not tied to actual LASER control.
  void SetLaserDuration(uint32_t ms);
  uint32_t LaserFrequency() const;
  uint32_t LaserDuration() const;

  bool Armed() const;
  byte TimestampPin() const;

  static Slm* instance;  ///< Singleton for ISR dispatch

private:
  int8_t timestampPin;                     ///< ISR input pin (PCINT0/PORTB group; 10–13 on Mega)
  volatile uint8_t* volatile pinInputReg;  ///< Cached PINx register for timestampPin. Both
                                           ///< qualifiers are load-bearing: the pointee is a
                                           ///< hardware register (re-read every ISR entry) and
                                           ///< the pointer itself is written by SetPin() in
                                           ///< main context and read in the ISR.
  volatile uint8_t pinBitMask;             ///< Cached bit mask within pinInputReg. Written in
                                           ///< main context, read in the ISR — volatile per the
                                           ///< class warning above (this core builds with -flto).
  volatile bool received;         ///< ISR flag: true when new SLM timestamp captured
  bool armed;                     ///< True when SLM logging is active
  volatile uint32_t timestamp;    ///< ISR-captured timestamp (session-relative ms)
  volatile uint32_t offset;       ///< Session start offset (volatile — read in ISR)
  volatile bool lastPinState;     ///< Previous pin level — RISING guard
  uint32_t laserFrequency;        ///< Bookkeeping only — recorded laser frequency (Hz)
  uint32_t laserDuration;         ///< Bookkeeping only — recorded laser duration (ms)

  /// @brief Cache the PINx register + bit mask for the current timestampPin
  /// and enable its PCINT0 interrupt. Derived from Arduino core macros so the
  /// pin→register mapping is board-correct.
  void applyPinRegisters();

  /// @brief Serialize SLM timestamp to serial JSON (level 009).
  void LogOutput(uint32_t ts);
};

#endif // SLM_H
