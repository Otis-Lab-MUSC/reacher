/**
 * @file LickCircuit.cpp
 * @brief LickCircuit implementation — debounced lick detection and JSON logging.
 */

#include "LickCircuit.h"

LickCircuit::LickCircuit(int8_t pin)
  : Device(pin, INPUT_PULLUP, "LICK_CIRCUIT") {
  initState = digitalRead(pin);
  previousState = digitalRead(pin);
  stableState = digitalRead(pin);
  lastDebounceTimestamp = 0;
  debounceDelay = 20;
  startTimestamp = 0;
  endTimestamp = 0;
  callback = nullptr;
}

void LickCircuit::Monitor(uint32_t currentTimestamp) {
  if (armed) {
    bool currentState = digitalRead(pin);
    if (currentState != previousState) {
      lastDebounceTimestamp = currentTimestamp;
    }
    if ((currentTimestamp - lastDebounceTimestamp) > debounceDelay) {
      if (currentState != stableState) {
        stableState = currentState;
        if (stableState != initState) {
          startTimestamp = currentTimestamp;
          if (callback) {
            callback(DeviceType::LICK, currentTimestamp);
          }
        } else {
          endTimestamp = currentTimestamp;
          LogOutput();
        }
      }
    }
    previousState = currentState;
  }
}

void LickCircuit::SetCallback(InputEventCallback cb) {
  callback = cb;
}

void LickCircuit::LogOutput() {
  Serial.print(F("{\"level\":\"007\",\"device\":\""));
  Serial.print(device);
  Serial.print(F("\",\"pin\":"));
  Serial.print(pin);
  Serial.print(F(",\"event\":\"LICK\",\"start_timestamp\":"));
  Serial.print(startTimestamp - Offset());
  Serial.print(F(",\"end_timestamp\":"));
  Serial.print(endTimestamp - Offset());
  Serial.println('}');
}
