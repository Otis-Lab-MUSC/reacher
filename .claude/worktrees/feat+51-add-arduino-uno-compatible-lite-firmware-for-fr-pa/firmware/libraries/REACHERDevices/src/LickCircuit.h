/**
 * @file LickCircuit.h
 * @brief Debounced lick sensor for consummatory behavior tracking.
 * @ingroup input_devices
 */

#ifndef LICKCIRCUIT_H
#define LICKCIRCUIT_H

#include <Arduino.h>
#include "Device.h"

/// @brief Debounced lick sensor for consummatory behavior tracking.
///
/// Logs lick events (start/end timestamps) via serial JSON output on release.
class LickCircuit : public Device {
public:
  /// @brief Construct a lick circuit sensor.
  /// @param pin Arduino pin number (configured as INPUT_PULLUP)
  LickCircuit(int8_t pin);

  /// @brief Poll pin state with debouncing; log lick event on release.
  /// @param currentTimestamp Current millis() value
  void Monitor(uint32_t currentTimestamp);

  /// @brief Register a callback fired on lick contact.
  /// @param cb Function called on debounced lick-down
  void SetCallback(InputEventCallback cb);

private:
  bool initState;                   ///< Pin state at construction (idle level)
  bool previousState;               ///< Last raw reading for edge detection
  bool stableState;                 ///< Debounced stable state
  uint32_t lastDebounceTimestamp;   ///< Last time raw state changed
  uint8_t debounceDelay;            ///< Debounce window in ms (default 20)
  uint32_t startTimestamp;          ///< millis() at lick contact
  uint32_t endTimestamp;            ///< millis() at lick release
  InputEventCallback callback;      ///< Lick-down callback

  /// @brief Serialize lick event to serial JSON (level 007).
  void LogOutput();
};

#endif // LICKCIRCUIT_H
