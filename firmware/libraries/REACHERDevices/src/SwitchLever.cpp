/**
 * @file SwitchLever.cpp
 * @brief SwitchLever implementation — debounced lever polling and callback dispatch.
 */

#include "SwitchLever.h"

SwitchLever::SwitchLever(int8_t pin, const char* orientation, DeviceType type)
  : Device(pin, INPUT_PULLUP, "SWITCH_LEVER") {
  this->devType = type;
  strncpy(this->orientation, orientation, sizeof(this->orientation) - 1);
  this->orientation[sizeof(this->orientation) - 1] = '\0';
  initState = digitalRead(pin);
  previousState = digitalRead(pin);
  stableState = digitalRead(pin);
  reinforced = false;
  timeoutEnd = 0;
  lastDebounceTimestamp = 0;
  debounceDelay = 20;
  startTimestamp = 0;
  endTimestamp = 0;
  callback = nullptr;
  releaseCallback = nullptr;
}

void SwitchLever::Monitor(uint32_t currentTimestamp) {
  if (armed) {
    bool currentState = digitalRead(pin);
    if (currentState != previousState) {
      lastDebounceTimestamp = currentTimestamp;
    }
    if ((currentTimestamp - lastDebounceTimestamp) > debounceDelay) {
      if (currentState != stableState) {
        stableState = currentState;
        if (stableState != initState) {
          // Press down
          startTimestamp = currentTimestamp;
          if (callback) {
            callback(devType, currentTimestamp);
          }
        } else {
          // Release
          endTimestamp = currentTimestamp;
          if (releaseCallback) {
            releaseCallback(devType);
          }
        }
      }
    }
    previousState = currentState;
  }
}

void SwitchLever::SetPin(int8_t newPin) {
  Device::SetPin(newPin);
  initState = digitalRead(pin);
  previousState = digitalRead(pin);
  stableState = digitalRead(pin);
  lastDebounceTimestamp = 0;
}

void SwitchLever::SetCallback(InputEventCallback pressCb) {
  callback = pressCb;
}

void SwitchLever::SetReleaseCallback(InputReleaseCallback releaseCb) {
  releaseCallback = releaseCb;
}

void SwitchLever::SetActiveLever(bool reinforced) {
  this->reinforced = reinforced;
}

void SwitchLever::SetTimeoutEnd(uint32_t endTs) {
  timeoutEnd = endTs;
}

bool SwitchLever::IsReinforced() const {
  return reinforced;
}

bool SwitchLever::InTimeout(uint32_t ts) const {
  return ts <= timeoutEnd;
}

const char* SwitchLever::Orientation() const {
  return orientation;
}

DeviceType SwitchLever::Type() const {
  return devType;
}

uint32_t SwitchLever::StartTimestamp() const {
  return startTimestamp;
}

uint32_t SwitchLever::EndTimestamp() const {
  return endTimestamp;
}
