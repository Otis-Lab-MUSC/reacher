/**
 * @file Slm.cpp
 * @brief SLM implementation — PCINT0 rising-edge capture and timestamp logging.
 */

#include "Slm.h"

Slm* Slm::instance = nullptr;

Slm::Slm(int8_t timestampPin) {
  this->timestampPin = timestampPin;
  pinMode(this->timestampPin, INPUT_PULLUP);
  received    = false;
  armed       = false;
  timestamp   = 0;
  offset      = 0;
  lastPinState = (digitalRead(this->timestampPin) == HIGH);
  instance    = this;

  // Enable PCINT0 group interrupt for the configured pin.
  // The PCINT0 group covers PB0–PB5 (Arduino pins 8–13).
  PCICR  |= (1 << PCIE0);
  PCMSK0 |= (1 << (this->timestampPin - 8));
}

void Slm::HandlePCINT() {
  // Read the current state of the configured pin from PINB.
  // PCINT0 group: Arduino pin N -> PINB bit (N - 8).
  bool pinHigh = (PINB >> (timestampPin - 8)) & 1;

  // Capture only on RISING edge (LOW -> HIGH transition).
  if (pinHigh && !lastPinState) {
    received  = true;
    timestamp = millis() - offset;
  }
  lastPinState = pinHigh;
}

void Slm::HandleTimestampSignal() {
  if (armed && received) {
    noInterrupts();
    received      = false;
    uint32_t ts   = timestamp;  // atomic copy of volatile 32-bit value
    interrupts();
    LogOutput(ts);
  }
}

void Slm::ArmToggle(bool armed) {
  this->armed = armed;
  Serial.print(F("{\"level\":\"001\",\"device\":\"SLM\",\"pin\":"));
  Serial.print(timestampPin);
  Serial.print(F(",\"desc\":\""));
  Serial.print(armed ? F("ARMED") : F("DISARMED"));
  Serial.println(F("\"}"));
}

void Slm::SetOffset(uint32_t offset) {
  this->offset = offset;
}

void Slm::SetPin(int8_t newPin) {
  // Disable interrupt for the old pin.
  PCMSK0 &= ~(1 << (timestampPin - 8));
  if (PCMSK0 == 0) {
    PCICR &= ~(1 << PCIE0);  // no PCINT0 pins remain — disable group
  }

  timestampPin = newPin;
  pinMode(timestampPin, INPUT_PULLUP);
  lastPinState = (digitalRead(timestampPin) == HIGH);

  // Enable interrupt for the new pin.
  PCICR  |= (1 << PCIE0);
  PCMSK0 |= (1 << (timestampPin - 8));

  Serial.print(F("{\"level\":\"000\",\"device\":\"SLM\",\"param\":\"timestamp_pin\",\"value\":"));
  Serial.print(timestampPin);
  Serial.println('}');
}

bool Slm::Armed() const { return armed; }

byte Slm::TimestampPin() const { return timestampPin; }

void Slm::LogOutput(uint32_t ts) {
  Serial.print(F("{\"level\":\"009\",\"device\":\"SLM\",\"pin\":"));
  Serial.print(timestampPin);
  Serial.print(F(",\"event\":\"TIMESTAMP\",\"timestamp\":"));
  Serial.print(ts);
  Serial.println('}');
}
