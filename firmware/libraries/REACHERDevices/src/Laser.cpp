/**
 * @file Laser.cpp
 * @brief Laser implementation — oscillation, mode switching, and test pulses.
 */

#include "Laser.h"

Laser::Laser(int8_t pin, uint32_t frequency, uint32_t duration)
  : Device(pin, OUTPUT, "LASER") {
  this->frequency = frequency;
  this->duration = duration;
  this->onsetDelay = 0;
  startTimestamp = 0;
  endTimestamp = 0;
  halfCycleStartTimestamp = 0;
  halfCycleEndTimestamp = 0;
  mode = CONTINGENT;
  state = false;
  halfState = false;
  isTesting = false;
  sessionActive = false;
}

void Laser::Activate(uint32_t startTs, uint32_t dur) {
  if (mode == CONTINGENT) {
    startTimestamp = startTs;
    endTimestamp = startTs + dur;
    state = true;
    UpdateHalfCycle(startTs);
  }
}

void Laser::Await(uint32_t currentTimestamp) {
  if (armed || isTesting) {
    if (mode == INDEPENDENT && !isTesting) {
      if (sessionActive) {
        Cycle(currentTimestamp);
        Oscillate(currentTimestamp);
      } else {
        Off();
      }
    } else {
      Oscillate(currentTimestamp);
    }
  } else {
    startTimestamp = currentTimestamp;
    endTimestamp = currentTimestamp;
    Off();
  }
}

void Laser::Test(uint32_t currentTimestamp) {
  startTimestamp = currentTimestamp;
  endTimestamp = currentTimestamp + duration;
  state = true;
  UpdateHalfCycle(startTimestamp);
  isTesting = true;
}

void Laser::Cycle(uint32_t currentTimestamp) {
  // Overflow-safe: see Scheduler.cpp:232 for rationale (Bug 2.2)
  if ((int32_t)(currentTimestamp - endTimestamp) >= 0) {
    startTimestamp = currentTimestamp;
    endTimestamp = currentTimestamp + duration;
    state = !state;

    // Log ON-cycle start for session visibility (matches Scheduler::LogDeviceActivation format)
    if (state && Offset() > 0) {
      Serial.print(F("{\"level\":\"007\",\"device\":\"LASER\",\"pin\":"));
      Serial.print(pin);
      Serial.print(F(",\"event\":\"STIM\",\"start_timestamp\":"));
      Serial.print(startTimestamp - Offset());
      Serial.print(F(",\"end_timestamp\":"));
      Serial.print(endTimestamp - Offset());
      Serial.println('}');
    }
  }
}

void Laser::Oscillate(uint32_t currentTimestamp) {
  // Overflow-safe: see Scheduler.cpp:232 for rationale (Bug 2.2)
  bool inWindow = (int32_t)(currentTimestamp - startTimestamp) >= 0 &&
                  (int32_t)(currentTimestamp - endTimestamp) <= 0;
  if (inWindow && state) {
    if (frequency == 1) {
      On();
    } else {
      if ((int32_t)(currentTimestamp - halfCycleEndTimestamp) >= 0) {
        UpdateHalfCycle(currentTimestamp);
      }
      if (halfState) {
        On();
      } else {
        Off();
      }
    }
  } else {
    Off();
    bool pastEnd = (int32_t)(currentTimestamp - endTimestamp) > 0;
    if (state && pastEnd) {
      state = false;
    }
    if (isTesting && pastEnd) {
      isTesting = false;
    }
  }
}

void Laser::Reset() {
  startTimestamp = 0;
  endTimestamp = 0;
  halfCycleStartTimestamp = 0;
  halfCycleEndTimestamp = 0;
  state = false;
  halfState = false;
  isTesting = false;
}

void Laser::SetSessionActive(bool active) {
  sessionActive = active;
}

void Laser::SetFrequency(uint32_t frequency) {
  if (frequency > 0) {
    this->frequency = frequency;
  }
}

void Laser::SetDuration(uint32_t duration) {
  this->duration = duration;
}

void Laser::SetMode(bool contingent) {
  mode = contingent ? CONTINGENT : INDEPENDENT;
}

uint32_t Laser::Frequency() const {
  return frequency;
}

uint32_t Laser::Duration() const {
  return duration;
}

bool Laser::IsContingent() const {
  return mode == CONTINGENT;
}

void Laser::On() {
  digitalWrite(pin, HIGH);
}

void Laser::Off() {
  digitalWrite(pin, LOW);
  halfState = false;
}

void Laser::UpdateHalfCycle(uint32_t currentTimestamp) {
  // Unconfigured (frequency == 0, the FR blank-state default): stay off rather
  // than divide by zero and cast +/-inf to uint32_t (undefined behavior).
  if (frequency == 0) {
    halfCycleStartTimestamp = currentTimestamp;
    halfCycleEndTimestamp = currentTimestamp;
    halfState = false;
    return;
  }
  // Frequency quantization due to integer millisecond timing:
  //   Freq(Hz) | Half-cycle(ms) | Actual(Hz) | Error
  //   ---------|----------------|------------|------
  //       1    |    500         |    1.00    |  0.0%
  //      10    |     50         |   10.00    |  0.0%
  //      20    |     25         |   20.00    |  0.0%
  //      25    |     20         |   25.00    |  0.0%
  //      30    |     17         |   29.41    |  2.0%
  //      40    |     13         |   38.46    |  3.8%
  //      50    |     10         |   50.00    |  0.0%
  // Round half-cycle to nearest ms to minimize error.
  float halfCycleLength = (1.0f / frequency) / 2.0f * 1000.0f;
  halfCycleStartTimestamp = currentTimestamp;
  halfCycleEndTimestamp = currentTimestamp + static_cast<uint32_t>(halfCycleLength + 0.5f);
  halfState = !halfState;
}
