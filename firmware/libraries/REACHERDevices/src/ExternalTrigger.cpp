/**
 * @file ExternalTrigger.cpp
 * @brief ExternalTrigger implementation — ISR latch, arm state, pin policy.
 */

#include "ExternalTrigger.h"

ExternalTrigger* ExternalTrigger::instance = nullptr;

ExternalTrigger::ExternalTrigger(int8_t pin) {
  this->pin = pin;
  fired = false;
  armed = false;
  // INPUT_PULLUP matches Microscope's timestamp input: a disconnected line
  // idles high and can never produce the rising edge, so an unplugged trigger
  // fails safe (never fires) rather than floating into a spurious session start.
  pinMode(this->pin, INPUT_PULLUP);
  instance = this;
}

void ExternalTrigger::TriggerISR() {
  if (instance && instance->armed) {
    instance->fired = true;
  }
}

bool ExternalTrigger::Consume() {
  if (!armed) return false;
  noInterrupts();
  bool got = fired;
  fired = false;
  interrupts();
  if (!got) return false;
  // Self-disarm before returning: see the one-shot rationale in the header.
  armed = false;
  detach();
  LogState();
  return true;
}

void ExternalTrigger::ArmToggle(bool armed) {
  if (armed) {
    // Drop any edge latched while disarmed so arming never fires immediately.
    noInterrupts();
    fired = false;
    interrupts();
    this->armed = true;
    attach();
  } else {
    this->armed = false;
    detach();
    noInterrupts();
    fired = false;
    interrupts();
  }
  LogState();
}

void ExternalTrigger::SetPin(int8_t newPin) {
  if (!IsAssignablePin(newPin)) {
    Serial.print(F("{\"level\":\"006\",\"device\":\"EXT_TRIGGER\",\"error_code\":\"EXT_PIN_INVALID\",\"desc\":\"External trigger pin must be 18, 19, 20 or 21\",\"got\":"));
    Serial.print(newPin);
    Serial.println('}');
    return;
  }
  bool wasArmed = armed;
  if (wasArmed) detach();
  pin = newPin;
  pinMode(pin, INPUT_PULLUP);
  if (wasArmed) attach();
  Serial.print(F("{\"level\":\"000\",\"device\":\"EXT_TRIGGER\",\"param\":\"pin\",\"value\":"));
  Serial.print(pin);
  Serial.println('}');
}

bool ExternalTrigger::IsAssignablePin(int8_t pin) {
  return pin == 18 || pin == 19 || pin == 20 || pin == 21;
}

bool ExternalTrigger::Armed() const { return armed; }

byte ExternalTrigger::Pin() const { return pin; }

void ExternalTrigger::attach() {
  attachInterrupt(digitalPinToInterrupt(pin), TriggerISR, RISING);
}

void ExternalTrigger::detach() {
  detachInterrupt(digitalPinToInterrupt(pin));
}

void ExternalTrigger::LogState() const {
  Serial.print(F("{\"level\":\"001\",\"device\":\"EXT_TRIGGER\",\"event\":\""));
  Serial.print(armed ? F("ARMED") : F("DISARMED"));
  Serial.print(F("\",\"pin\":"));
  Serial.print(pin);
  Serial.println('}');
}
