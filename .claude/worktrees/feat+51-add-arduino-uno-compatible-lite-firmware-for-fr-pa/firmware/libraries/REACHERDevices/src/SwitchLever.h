/**
 * @file SwitchLever.h
 * @brief Debounced lever input with press/release callbacks and timeout tracking.
 * @ingroup input_devices
 */

#ifndef SWITCHLEVER_H
#define SWITCHLEVER_H

#include <Arduino.h>
#include "Device.h"

/// @defgroup input_devices Input Devices
/// @{

/// @brief Debounced lever input with press/release callbacks and timeout tracking.
///
/// Fires press callback on press-down (classification stored), release callback on
/// release (where the press event is logged). This matches the original operant_FR
/// behavior of logging presses on release.
/// @note Press is logged on release; classification is stored on press-down.
class SwitchLever : public Device {
public:
  /// @brief Construct a lever input device.
  /// @param pin Arduino pin number (configured as INPUT_PULLUP)
  /// @param orientation "RH" or "LH" label for JSON output
  /// @param type DeviceType::LEVER_RH or DeviceType::LEVER_LH
  SwitchLever(int8_t pin, const char* orientation, DeviceType type);

  /// @brief Poll pin state with debouncing; fire callbacks on transitions.
  /// @param currentTimestamp Current millis() value
  void Monitor(uint32_t currentTimestamp);

  /// @brief Register the press-down callback.
  /// @param pressCb Function called on debounced press-down
  void SetCallback(InputEventCallback pressCb);

  /// @brief Register the release callback.
  /// @param releaseCb Function called on debounced release
  void SetReleaseCallback(InputReleaseCallback releaseCb);

  /// @brief Reassign the lever to a different pin and refresh debounce state.
  /// Calls Device::SetPin then re-reads initState/previousState/stableState
  /// from the new pin so a stale debounced state can't fire a phantom
  /// press/release event the next time the lever is armed.
  void SetPin(int8_t newPin);

  /// @brief Mark this lever as reinforced (active) or inactive.
  void SetActiveLever(bool reinforced);

  /// @brief Set the timeout window end timestamp.
  void SetTimeoutEnd(uint32_t endTs);

  bool IsReinforced() const;

  /// @brief Check if a timestamp falls within the current timeout window.
  /// @param ts Timestamp to check
  /// @return true if ts <= timeoutEnd
  bool InTimeout(uint32_t ts) const;

  const char* Orientation() const;
  DeviceType Type() const;
  uint32_t StartTimestamp() const;
  uint32_t EndTimestamp() const;

private:
  DeviceType devType;               ///< LEVER_RH or LEVER_LH
  bool initState;                   ///< Pin state at construction (idle level)
  bool previousState;               ///< Last raw reading for edge detection
  bool stableState;                 ///< Debounced stable state
  char orientation[3];              ///< "RH" or "LH" label
  bool reinforced;                  ///< True if this is the active/reinforced lever
  uint32_t timeoutEnd;              ///< Timeout window end timestamp (0 = no timeout)
  uint32_t lastDebounceTimestamp;   ///< Last time raw state changed
  uint8_t debounceDelay;            ///< Debounce window in ms (default 20)
  uint32_t startTimestamp;          ///< millis() at press-down
  uint32_t endTimestamp;            ///< millis() at release
  InputEventCallback callback;      ///< Press-down callback
  InputReleaseCallback releaseCallback;  ///< Release callback
};

/// @}

#endif // SWITCHLEVER_H
