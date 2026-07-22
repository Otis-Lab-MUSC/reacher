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
  laserFrequency = 0;
  laserDuration  = 0;
  instance    = this;

  // Enable the PCINT0 group interrupt for the configured pin. The default
  // pin (Pins.h) is a compile-time PORTB pin, so no runtime guard is needed
  // here — SetPin() enforces the PCINT0-group constraint at runtime.
  applyPinRegisters();
}

void Slm::applyPinRegisters() {
  // Board-correct pin->register mapping via the Arduino core macros, rather
  // than assuming the UNO "PB0–PB5 == pins 8–13" layout. On the Mega 2560,
  // pins 10–13 map to PB4–PB7; digitalPinToPCMSKbit() yields the right bit.
  pinInputReg = portInputRegister(digitalPinToPort(timestampPin));
  pinBitMask  = digitalPinToBitMask(timestampPin);
  PCMSK0 |= (1 << digitalPinToPCMSKbit(timestampPin));
  PCICR  |= (1 << PCIE0);
}

void Slm::HandlePCINT() {
  // Read the current level of the configured pin from its cached PINx register.
  bool pinHigh = (*pinInputReg & pinBitMask) != 0;

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
  // The single ISR vector is PCINT0_vect, so the pin must be in the PCINT0
  // group (PORTB) — pins 10–13 on the Mega. Reject anything else and keep
  // the current pin so capture is never silently misrouted.
  if (digitalPinToPCMSK(newPin) != &PCMSK0) {
    Serial.print(F("{\"level\":\"006\",\"device\":\"SLM\",\"error\":\"pin_not_pcint0\",\"value\":"));
    Serial.print(newPin);
    Serial.println('}');
    return;
  }

  // Assumes it is called from loop()-level code (ParseCommands) with interrupts
  // enabled and no outer critical section — the unconditional interrupts() below
  // would otherwise re-enable them early. True for every current call site.
  noInterrupts();
  // Disable interrupt for the old pin, then swap in the new one atomically so
  // the ISR never sees a half-updated (pinInputReg, pinBitMask, timestampPin).
  PCMSK0 &= ~(1 << digitalPinToPCMSKbit(timestampPin));
  timestampPin = newPin;
  pinMode(timestampPin, INPUT_PULLUP);
  lastPinState = (digitalRead(timestampPin) == HIGH);
  received     = false;  // discard any stale capture from the old pin
  applyPinRegisters();
  interrupts();

  Serial.print(F("{\"level\":\"000\",\"device\":\"SLM\",\"param\":\"timestamp_pin\",\"value\":"));
  Serial.print(timestampPin);
  Serial.println('}');
}

void Slm::SetLaserFrequency(uint32_t hz) { laserFrequency = hz; }
void Slm::SetLaserDuration(uint32_t ms)  { laserDuration = ms; }
uint32_t Slm::LaserFrequency() const { return laserFrequency; }
uint32_t Slm::LaserDuration() const  { return laserDuration; }

bool Slm::Armed() const { return armed; }

byte Slm::TimestampPin() const { return timestampPin; }

void Slm::LogOutput(uint32_t ts) {
  Serial.print(F("{\"level\":\"009\",\"device\":\"SLM\",\"pin\":"));
  Serial.print(timestampPin);
  Serial.print(F(",\"event\":\"TIMESTAMP\",\"timestamp\":"));
  Serial.print(ts);
  Serial.println('}');
}
