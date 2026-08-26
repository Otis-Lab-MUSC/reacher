/**
 * @file ReacherHelpers.h
 * @brief Shared session helpers used by all REACHER sketch variants.
 */

#ifndef REACHER_HELPERS_H
#define REACHER_HELPERS_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include "Device.h"
#include "Commands.h"

// Forward declarations
class SwitchLever;
class Cue;
class Pump;
class Laser;
class LickCircuit;
class Microscope;
class Slm;

/// @brief Aggregate of device pointers for shared helper functions.
/// Set unused pointers to nullptr (e.g. laser in pavlovian).
struct DeviceSet {
  SwitchLever* rLever;
  SwitchLever* lLever;
  Cue* cue;
  Cue* cue2;
  Pump* pump;
  Pump* pump2;
  LickCircuit* lickCircuit;
  Laser* laser;         ///< nullptr if not used (pavlovian)
  Microscope* microscope;
  Slm* slm;             ///< nullptr if SLM plugin not installed
};

/// @brief Set session-relative timestamp offset on all devices in the set.
void setDeviceTimestampOffset(DeviceSet& ds, uint32_t ts);

/// @brief Arm or disarm all devices in the set.
void armToggleDevices(DeviceSet& ds, bool toggle);

/// @brief Report device with frequency + duration (Cue, Laser).
void reportDeviceConfig(const __FlashStringHelper* dev, bool armed,
                        uint32_t freq, uint32_t dur);

/// @brief Report device with duration only (Pump).
void reportDeviceConfig(const __FlashStringHelper* dev, bool armed,
                        uint32_t dur);

/// @brief Report device with no params (Lick, Microscope).
void reportDeviceConfig(const __FlashStringHelper* dev, bool armed);

/// @brief Report lever config (armed + reinforced).
void reportDeviceLever(const __FlashStringHelper* dev, bool armed,
                       bool reinforced);

/// @brief Snapshot of arm states for all devices in a DeviceSet.
/// Used to preserve arm state across session boundaries (EndSession disarms all).
struct ArmSnapshot {
  bool rLever;
  bool lLever;
  bool cue;
  bool cue2;
  bool pump;
  bool pump2;
  bool lickCircuit;
  bool laser;
  bool microscope;
  bool slm;
};

/// @brief Capture the current arm state of all devices.
ArmSnapshot captureArmState(const DeviceSet& ds);

/// @brief Restore a previously captured arm state to all devices.
void restoreArmState(DeviceSet& ds, const ArmSnapshot& snap);

/// @brief Handle device commands common to all paradigms (cue, pump, lick, laser, microscope).
/// @param ds Device set
/// @param command Command code from serial JSON
/// @param inputJson Parsed JSON document
/// @return true if the command was handled, false if the sketch should handle it.
bool handleCommonDeviceCommand(DeviceSet& ds, int command, JsonDocument& inputJson);

#endif // REACHER_HELPERS_H
