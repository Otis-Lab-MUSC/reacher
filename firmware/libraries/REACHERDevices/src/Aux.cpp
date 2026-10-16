/**
 * @file Aux.cpp
 * @brief Aux implementation — seeded TTL pulse output with forced-off safety.
 */

#include "Aux.h"

#if defined(__AVR_ATmega2560__)

Aux::Aux(int8_t pin)
  : Device(pin, OUTPUT, "AUX") {
  // Defaults can never shock: disarmed, p = 0, k = 0, test-fire off.
  duration = 500;
  onsetDelay = 0;
  mode = AUX_MODE_INDEPENDENT;
  probability = 0;
  blockK = 0;
  blockN = 10;
  seed = 0;
  effectiveSeed = 0;
  testFire = false;
  active = false;
  startTimestamp = 0;
  endTimestamp = 0;
  draw.Reset(1, blockK, blockN);
}

AuxOutcome Aux::Fire(uint32_t now, bool testCtx) {
  if (testCtx && !testFire) return AuxOutcome::SUPPRESSED;
  bool shock = (mode == AUX_MODE_BLOCK) ? draw.DrawBlock() : draw.DrawIndependent(probability);
  if (!shock) return AuxOutcome::OMITTED;
  StartPulse(now);
  return AuxOutcome::FIRED;
}

void Aux::Await(uint32_t now) {
  if (!active) return;
  // Overflow-safe: see Scheduler.cpp:232 for rationale (Bug 2.2)
  if ((int32_t)(now - endTimestamp) >= 0) {
    digitalWrite(pin, LOW);
    active = false;
  } else if ((int32_t)(now - startTimestamp) >= 0) {
    digitalWrite(pin, HIGH);
  }
}

void Aux::ForceOff() {
  digitalWrite(pin, LOW);
  active = false;
  startTimestamp = 0;
  endTimestamp = 0;
}

void Aux::Test(uint32_t now) {
  StartPulse(now);
}

void Aux::StartPulse(uint32_t now) {
  startTimestamp = now;
  endTimestamp = now + duration;
  active = true;
  digitalWrite(pin, HIGH);
}

void Aux::ResetSession() {
  effectiveSeed = seed ? seed : (((uint32_t)analogRead(A1) ^ micros()) | 1);
  draw.Reset(effectiveSeed, blockK, blockN);
}

void Aux::ReportConfig() const {
  Serial.print(F("{\"level\":\"000\",\"device\":\"AUX\",\"armed\":"));
  Serial.print(armed ? F("true") : F("false"));
  Serial.print(F(",\"duration\":"));
  Serial.print(duration);
  Serial.print(F(",\"onset_delay\":"));
  Serial.print(onsetDelay);
  Serial.print(F(",\"mode\":"));
  Serial.print(mode);
  Serial.print(F(",\"probability\":"));
  Serial.print(probability);
  Serial.print(F(",\"block_k\":"));
  Serial.print(blockK);
  Serial.print(F(",\"block_n\":"));
  Serial.print(blockN);
  Serial.print(F(",\"seed\":"));
  Serial.print(effectiveSeed);
  Serial.print(F(",\"test_fire\":"));
  Serial.print(testFire ? F("true") : F("false"));
  Serial.println('}');
}

void Aux::SetDuration(uint32_t ms) {
  if (ms < 1) ms = 1;
  if (ms > AUX_MAX_DURATION) ms = AUX_MAX_DURATION;
  duration = ms;
}

void Aux::SetOnsetDelay(uint32_t ms) {
  onsetDelay = (ms > AUX_MAX_ONSET_DELAY) ? AUX_MAX_ONSET_DELAY : ms;
}

void Aux::SetMode(uint32_t m) {
  mode = (m > AUX_MODE_BLOCK) ? AUX_MODE_BLOCK : (uint8_t)m;
}

void Aux::SetProbability(uint32_t p) {
  probability = (p > 100) ? 100 : (uint8_t)p;
}

void Aux::SetBlockK(uint32_t k) {
  blockK = (k > blockN) ? blockN : (uint8_t)k;
  draw.SetBlock(blockK, blockN);
}

bool Aux::SetBlockN(uint32_t n) {
  if (n < 1) n = 1;
  if (n > AUX_MAX_BLOCK_N) n = AUX_MAX_BLOCK_N;
  blockN = (uint8_t)n;
  bool kChanged = blockK > blockN;
  if (kChanged) blockK = blockN;
  draw.SetBlock(blockK, blockN);
  return kChanged;
}

void Aux::SetSeed(uint32_t s) {
  seed = s;
  effectiveSeed = s;
}

void Aux::SetTestFire(bool enable) {
  testFire = enable;
}

uint32_t Aux::Duration() const { return duration; }
uint32_t Aux::OnsetDelay() const { return onsetDelay; }
uint8_t  Aux::Mode() const { return mode; }
uint8_t  Aux::Probability() const { return probability; }
uint8_t  Aux::BlockK() const { return blockK; }
uint8_t  Aux::BlockN() const { return blockN; }
uint32_t Aux::Seed() const { return seed; }
uint32_t Aux::EffectiveSeed() const { return effectiveSeed; }
bool     Aux::TestFire() const { return testFire; }

#endif // __AVR_ATmega2560__
