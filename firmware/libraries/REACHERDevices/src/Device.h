/**
 * @file Device.h
 * @brief Base hardware peripheral class and shared type definitions.
 * @ingroup devices
 */

#ifndef DEVICE_H
#define DEVICE_H

#include <Arduino.h>

/// @defgroup devices Hardware Devices
/// @{

/// @brief Identifies a specific hardware peripheral instance.
enum class DeviceType : uint8_t {
  LEVER_RH,    ///< Right-hand lever
  LEVER_LH,    ///< Left-hand lever
  LICK,        ///< Lick detection circuit
  CUE,         ///< Primary tone speaker
  CUE_2,       ///< Secondary tone speaker
  PUMP,        ///< Primary syringe pump
  PUMP_2,      ///< Secondary syringe pump
  LASER,       ///< Optogenetic laser
  MICROSCOPE,  ///< Two-photon microscope sync
  NONE         ///< No device / wildcard
};

/// @brief Base class for all hardware peripherals. Manages pin, arm state, and timestamp offset.
/// @see SwitchLever, Cue, Pump, Laser, LickCircuit
class Device {
public:
  /// @brief Construct a device and configure its pin mode.
  /// @param pin  Arduino pin number
  /// @param mode Pin mode (INPUT, OUTPUT, INPUT_PULLUP)
  /// @param device Human-readable name for JSON logging
  Device(int8_t pin, uint8_t mode, const char* device);

  /// @brief Arm or disarm the device and log the state change via serial JSON.
  void ArmToggle(bool arm);
  /// @brief Set the session-relative timestamp offset.
  void SetOffset(uint32_t offset);

  /// @brief Reassign this device to a different Arduino pin at runtime.
  /// Disarms first, drives any OUTPUT pin LOW, applies pinMode(newPin, mode),
  /// and emits a level-`000` config event. SwitchLever overrides to also
  /// refresh its debounce buffers.
  void SetPin(int8_t newPin);

  byte Pin() const;
  bool Armed() const;
  uint32_t Offset() const;

private:
  uint32_t offset;  ///< Session start timestamp for relative logging

protected:
  int8_t pin;          ///< Arduino pin number
  uint8_t mode;        ///< Pin mode (INPUT/OUTPUT/INPUT_PULLUP)
  bool armed;          ///< True when device is active
  const char* device;  ///< Device name for JSON output
};

// Parameter change logging helpers (level "000")
void logParamChange(const __FlashStringHelper* device,
                    const __FlashStringHelper* param,
                    uint32_t value);
void logParamChange(const __FlashStringHelper* device,
                    const __FlashStringHelper* param,
                    bool value);
void logParamChange(const __FlashStringHelper* device,
                    const __FlashStringHelper* param,
                    const __FlashStringHelper* value);

/// @brief Callback fired on input device press/contact.
/// @param source The DeviceType that generated the event
/// @param timestamp millis() at event time
typedef void (*InputEventCallback)(DeviceType source, uint32_t timestamp);

/// @brief Callback fired on input device release.
/// @param source The DeviceType that generated the event
typedef void (*InputReleaseCallback)(DeviceType source);

/// @}

#endif // DEVICE_H
