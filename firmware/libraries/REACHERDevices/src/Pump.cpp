/**
 * @file Pump.cpp
 * @brief Pump implementation — relay-driven syringe pump control.
 */

#include "Pump.h"

Pump::Pump(int8_t pin, uint32_t duration)
  : Device(pin, OUTPUT, "PUMP") {
  this->duration = duration;
  startTimestamp = 0;
  endTimestamp = 0;
  isTesting = false;
}

void Pump::Activate(uint32_t startTs, uint32_t dur) {
  startTimestamp = startTs;
  endTimestamp = startTs + dur;
}

void Pump::Await(uint32_t currentTimestamp) {
  if (armed || isTesting) {
    // Overflow-safe: see Scheduler.cpp:232 for rationale (Bug 2.2)
    if ((int32_t)(currentTimestamp - startTimestamp) >= 0 &&
        (int32_t)(currentTimestamp - endTimestamp) <= 0) {
      On();
    } else {
      Off();
      if (isTesting) isTesting = false;
    }
  }
}

void Pump::Test(uint32_t currentTimestamp) {
  startTimestamp = currentTimestamp;
  endTimestamp = currentTimestamp + duration;
  isTesting = true;
}

void Pump::SetDuration(uint32_t duration) {
  this->duration = duration;
}

uint32_t Pump::Duration() const {
  return duration;
}

void Pump::On() {
  digitalWrite(pin, HIGH);
}

void Pump::Off() {
  digitalWrite(pin, LOW);
}
