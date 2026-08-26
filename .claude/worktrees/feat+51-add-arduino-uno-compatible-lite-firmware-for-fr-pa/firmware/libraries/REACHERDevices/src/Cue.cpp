/**
 * @file Cue.cpp
 * @brief Cue implementation — tone playback via Arduino tone().
 */

#include "Cue.h"

Cue::Cue(int8_t pin, uint32_t frequency, uint32_t duration)
  : Device(pin, OUTPUT, "CUE") {
  this->frequency = frequency;
  this->duration = duration;
  startTimestamp = UINT32_MAX;
  endTimestamp = 0;
  playing = false;
  pulsed = false;
  pulseIsOn = false;
  pulseOnMs = 200;
  pulseOffMs = 200;
  isTesting = false;
}

void Cue::Activate(uint32_t startTs, uint32_t dur) {
  startTimestamp = startTs;
  endTimestamp = startTs + dur;
}

// The `playing` flag ensures Off() is only called once on the falling edge.
// Without it, a non-playing Cue would call noTone() every tick, which kills
// tone output on ANY pin (Arduino tone() is a singleton on ATmega328P).
void Cue::Await(uint32_t currentTimestamp) {
  if (armed || isTesting) {
    // Overflow-safe: see Scheduler.cpp:232 for rationale (Bug 2.2)
    if ((int32_t)(currentTimestamp - startTimestamp) >= 0 &&
        (int32_t)(currentTimestamp - endTimestamp) <= 0) {
      if (pulsed) {
        uint16_t cycleLen = pulseOnMs + pulseOffMs;
        if (cycleLen == 0) cycleLen = 1;  // guard against div-by-zero
        uint32_t elapsed = currentTimestamp - startTimestamp;
        bool shouldBeOn = ((uint16_t)(elapsed % cycleLen) < pulseOnMs);
        // Edge-only transitions to avoid repeated tone()/noTone() calls
        if (shouldBeOn && !pulseIsOn) { On(); pulseIsOn = true; }
        else if (!shouldBeOn && pulseIsOn) { Off(); pulseIsOn = false; }
      } else {
        On();
      }
      playing = true;
    } else if (playing) {
      Off();
      playing = false;
      pulseIsOn = false;
      if (isTesting) isTesting = false;
    }
  }
}

void Cue::Jingle() {
  int32_t pitch = 500;
  for (int32_t i = 0; i < 3; i++) {
    tone(pin, pitch, 100);
    delay(100);
    noTone(pin);
    pitch += 500;
  }
}

void Cue::Test(uint32_t currentTimestamp) {
  startTimestamp = currentTimestamp;
  endTimestamp = currentTimestamp + duration;
  isTesting = true;
}

void Cue::SetFrequency(uint32_t frequency) {
  this->frequency = frequency;
}

void Cue::SetDuration(uint32_t duration) {
  this->duration = duration;
}

uint32_t Cue::Frequency() const {
  return frequency;
}

uint32_t Cue::Duration() const {
  return duration;
}

void Cue::SetPulsed(bool pulsed, uint16_t onMs, uint16_t offMs) {
  this->pulsed = pulsed;
  pulseOnMs = onMs;
  pulseOffMs = offMs;
}

bool Cue::IsPulsed() const {
  return pulsed;
}

void Cue::On() {
  tone(pin, frequency);
}

void Cue::Off() {
  noTone(pin);
}
