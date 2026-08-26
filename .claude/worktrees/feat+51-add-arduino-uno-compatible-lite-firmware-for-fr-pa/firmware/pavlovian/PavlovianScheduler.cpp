/**
 * @file PavlovianScheduler.cpp
 * @brief PavlovianScheduler implementation — trial state machine, ITI sampling, JSON logging.
 */

#include "PavlovianScheduler.h"
#include <SwitchLever.h>
#include <LickCircuit.h>
#include <Cue.h>
#include <Pump.h>
#include <Laser.h>
#include <Microscope.h>

PavlovianScheduler::PavlovianScheduler() {
  leverRH = nullptr;
  leverLH = nullptr;
  lickCircuit = nullptr;
  cue = nullptr;
  cue2 = nullptr;
  pump = nullptr;
  pump2 = nullptr;
  laser = nullptr;
  microscope = nullptr;
  laserTrialFilter = LaserTrialFilter::CS_BOTH;
  laserPhase = LaserPhase::REWARD;
  sessionOffset = 0;
  sessionActive = false;
  sessionPaused = false;
  pauseStart = 0;
  lastPressClassRH = PressClass::INACTIVE;
  lastPressClassLH = PressClass::INACTIVE;

  pavCsPlusCount = 50;
  pavCsMinusCount = 50;
  pavCsPlusProb = 100;
  pavCsMinusProb = 0;
  pavCounterbalance = false;
  pavCueDuration = 2000;
  pavTraceInterval = 1000;
  pavConsumptionMs = 3000;
  pavItiMean = 30000;
  pavItiMin = 10000;
  pavItiMax = 90000;

  pavPhase = PavlovPhase::IDLE;
  pavTrialIndex = 0;
  pavTotalTrials = 0;
  pavRewardThisTrial = false;
  pavPhaseStart = 0;
  pavCurrentIti = 0;
  memset(pavTrialTypes, 0, TRIAL_ARRAY_BYTES);
}

void PavlovianScheduler::RegisterLever(SwitchLever* lever, DeviceType type) {
  if (type == DeviceType::LEVER_RH) leverRH = lever;
  else if (type == DeviceType::LEVER_LH) leverLH = lever;
}

void PavlovianScheduler::RegisterLickCircuit(LickCircuit* lick) {
  lickCircuit = lick;
}

void PavlovianScheduler::RegisterCue(Cue* c) {
  cue = c;
}

void PavlovianScheduler::RegisterCue2(Cue* c) {
  cue2 = c;
}

void PavlovianScheduler::RegisterPump(Pump* p) {
  pump = p;
}

void PavlovianScheduler::RegisterPump2(Pump* p) {
  pump2 = p;
}

void PavlovianScheduler::RegisterLaser(Laser* l) {
  laser = l;
}

void PavlovianScheduler::RegisterMicroscope(Microscope* mic) {
  microscope = mic;
}

void PavlovianScheduler::SetLaserTrialFilter(LaserTrialFilter filter) {
  laserTrialFilter = filter;
}

void PavlovianScheduler::SetLaserPhase(LaserPhase phase) {
  laserPhase = phase;
}

bool PavlovianScheduler::ShouldFireLaser(bool isCsMinus) const {
  return (laserTrialFilter == LaserTrialFilter::CS_BOTH)
      || (laserTrialFilter == LaserTrialFilter::CS_PLUS && !isCsMinus)
      || (laserTrialFilter == LaserTrialFilter::CS_MINUS && isCsMinus);
}

void PavlovianScheduler::Update(uint32_t now) {
  if (sessionActive && !sessionPaused) PavTick(now);
  TickOutputs(now);
}

void PavlovianScheduler::OnInputEvent(DeviceType source, uint32_t timestamp) {
  if (!sessionActive) return;
  if (sessionPaused) return;
  if (source != DeviceType::LEVER_RH && source != DeviceType::LEVER_LH) return;

  PressClass cls = ClassifyPress(source);

  if (source == DeviceType::LEVER_RH) {
    lastPressClassRH = cls;
  } else {
    lastPressClassLH = cls;
  }
  // Presses are logged but don't affect trial progression
}

void PavlovianScheduler::OnInputRelease(DeviceType source) {
  if (!sessionActive) return;
  if (sessionPaused) return;

  if (source == DeviceType::LEVER_RH) {
    LogLeverPress(source, lastPressClassRH);
  } else if (source == DeviceType::LEVER_LH) {
    LogLeverPress(source, lastPressClassLH);
  }
}

PressClass PavlovianScheduler::ClassifyPress(DeviceType source) {
  SwitchLever* lever = GetLever(source);
  if (!lever) return PressClass::INACTIVE;
  // In Pavlovian mode, both levers are reinforced and there's no timeout
  return PressClass::ACTIVE;
}

bool PavlovianScheduler::IsSessionActive() const {
  return sessionActive;
}

bool PavlovianScheduler::IsComplete() const {
  return sessionActive && pavPhase == PavlovPhase::IDLE;
}

void PavlovianScheduler::Configure(uint8_t csPlusCount, uint8_t csMinusCount,
                                   uint32_t csPlusFreq, uint32_t csMinusFreq,
                                   uint16_t cueDuration, uint16_t traceInterval, uint16_t consumptionMs,
                                   uint32_t itiMean, uint32_t itiMin, uint32_t itiMax,
                                   uint8_t csPlusProb, uint8_t csMinusProb, bool counterbalance) {
  if (sessionActive) {
    // Safe mid-session updates — skip counts & counterbalance; clamp values
    pavCueDuration = max(cueDuration, (uint16_t)1);
    pavTraceInterval = traceInterval;
    pavConsumptionMs = consumptionMs;
    pavItiMin = itiMin;
    pavItiMax = max(itiMax, pavItiMin);
    pavItiMean = constrain(itiMean, pavItiMin, pavItiMax);
    pavCsPlusProb = min(csPlusProb, (uint8_t)100);
    pavCsMinusProb = min(csMinusProb, (uint8_t)100);
    if (cue) cue->SetFrequency(csPlusFreq);
    if (cue2) cue2->SetFrequency(csMinusFreq);
    return;
  }

  // Full reconfiguration (session inactive) — clamp to safe ranges
  pavCsPlusCount = min(csPlusCount, (uint8_t)(MAX_PAVLOV_TRIALS));
  pavCsMinusCount = min(csMinusCount, (uint8_t)(MAX_PAVLOV_TRIALS - pavCsPlusCount));
  pavCueDuration = max(cueDuration, (uint16_t)1);
  pavTraceInterval = traceInterval;
  pavConsumptionMs = consumptionMs;
  pavItiMin = itiMin;
  pavItiMax = max(itiMax, pavItiMin);
  pavItiMean = constrain(itiMean, pavItiMin, pavItiMax);
  pavCsPlusProb = min(csPlusProb, (uint8_t)100);
  pavCsMinusProb = min(csMinusProb, (uint8_t)100);
  pavCounterbalance = counterbalance;

  // Configure CS+ cue: continuous tone
  if (cue) {
    cue->SetFrequency(csPlusFreq);
    cue->SetPulsed(false);
  }
  // Configure CS- cue: pulsed tone
  if (cue2) {
    cue2->SetFrequency(csMinusFreq);
    cue2->SetPulsed(true, 200, 200);
  }
}

void PavlovianScheduler::SetPaused(bool paused, uint32_t now) {
  if (!sessionActive) return;
  if (paused) {
    sessionPaused = true;
    pauseStart = now;
    if (cue) noTone(cue->Pin());
    if (cue2) noTone(cue2->Pin());
    if (laser) digitalWrite(laser->Pin(), LOW);
  } else {
    // Adjust phase timer to account for paused duration
    uint32_t pausedDuration = now - pauseStart;
    pavPhaseStart += pausedDuration;
    sessionPaused = false;
  }
}

void PavlovianScheduler::StartSession(uint32_t now) {
  sessionOffset = now;
  sessionActive = true;
  sessionPaused = false;

  // Gate trial counts on cue device armed status.
  // Counterbalance swaps which cue is CS+ vs CS-.
  bool csPlusCueArmed  = pavCounterbalance
    ? (cue2 && cue2->Armed()) : (cue && cue->Armed());
  bool csMinusCueArmed = pavCounterbalance
    ? (cue && cue->Armed()) : (cue2 && cue2->Armed());

  uint8_t effectiveCsPlus  = csPlusCueArmed  ? pavCsPlusCount  : 0;
  uint8_t effectiveCsMinus = csMinusCueArmed ? pavCsMinusCount : 0;

  // Temporarily override counts for trial generation
  uint8_t savedCsPlus  = pavCsPlusCount;
  uint8_t savedCsMinus = pavCsMinusCount;
  pavCsPlusCount  = effectiveCsPlus;
  pavCsMinusCount = effectiveCsMinus;

  pavTotalTrials = pavCsPlusCount + pavCsMinusCount;
  if (pavTotalTrials > MAX_PAVLOV_TRIALS) pavTotalTrials = MAX_PAVLOV_TRIALS;

  if (pavTotalTrials == 0) {
    pavPhase = PavlovPhase::IDLE;
    pavCsPlusCount  = savedCsPlus;
    pavCsMinusCount = savedCsMinus;
    return;
  }

  randomSeed(analogRead(A0) ^ now);
  PavGenerateTrialOrder();

  // Restore configured counts
  pavCsPlusCount  = savedCsPlus;
  pavCsMinusCount = savedCsMinus;

  pavTrialIndex = 0;
  pavCurrentIti = PavSampleIti();
  pavPhase = PavlovPhase::ITI;
  pavPhaseStart = now;

  if (laser) laser->SetSessionActive(true);
}

void PavlovianScheduler::EndSession(uint32_t now) {
  sessionActive = false;
  sessionPaused = false;
  pavPhase = PavlovPhase::IDLE;

  // Force all outputs off
  if (cue) noTone(cue->Pin());
  if (cue2) noTone(cue2->Pin());
  if (pump) digitalWrite(pump->Pin(), LOW);
  if (pump2) digitalWrite(pump2->Pin(), LOW);
  if (laser) laser->SetSessionActive(false);
  if (laser) digitalWrite(laser->Pin(), LOW);
}

uint32_t PavlovianScheduler::SessionOffset() const {
  return sessionOffset;
}

SwitchLever* PavlovianScheduler::GetLever(DeviceType type) {
  if (type == DeviceType::LEVER_RH) return leverRH;
  if (type == DeviceType::LEVER_LH) return leverLH;
  return nullptr;
}

void PavlovianScheduler::TickOutputs(uint32_t now) {
  if (cue) cue->Await(now);
  if (cue2) cue2->Await(now);
  if (pump) pump->Await(now);
  if (pump2) pump2->Await(now);
  if (laser) laser->Await(now);
}

// --- Trial generation ---

bool PavlovianScheduler::PavIsCSMinus(uint8_t index) const {
  if (index >= pavTotalTrials) return false;
  return (pavTrialTypes[index / 8] >> (index % 8)) & 1;
}

void PavlovianScheduler::PavSetTrialType(uint8_t index, bool isCsMinus) {
  if (index >= MAX_PAVLOV_TRIALS) return;
  uint8_t byteIdx = index / 8;
  uint8_t bitIdx = index % 8;
  if (isCsMinus) {
    pavTrialTypes[byteIdx] |= (1 << bitIdx);
  } else {
    pavTrialTypes[byteIdx] &= ~(1 << bitIdx);
  }
}

void PavlovianScheduler::PavGenerateTrialOrder() {
  uint8_t total = pavCsPlusCount + pavCsMinusCount;
  uint8_t csPlus = pavCsPlusCount;
  uint8_t csMinus = pavCsMinusCount;

  // Proportionally scale both counts if total exceeds MAX_PAVLOV_TRIALS
  if (total > MAX_PAVLOV_TRIALS) {
    csPlus = (uint8_t)((uint16_t)pavCsPlusCount * MAX_PAVLOV_TRIALS / total);
    csMinus = MAX_PAVLOV_TRIALS - csPlus;
    total = MAX_PAVLOV_TRIALS;
  }

  for (uint8_t attempt = 0; attempt < 50; attempt++) {
    // Fill array: CS+ first, CS- second
    memset(pavTrialTypes, 0, TRIAL_ARRAY_BYTES);
    for (uint8_t i = csPlus; i < total; i++) {
      PavSetTrialType(i, true);
    }

    // Fisher-Yates shuffle
    for (uint8_t i = total - 1; i > 0; i--) {
      uint8_t j = random(i + 1);
      bool a = PavIsCSMinus(i);
      bool b = PavIsCSMinus(j);
      PavSetTrialType(i, b);
      PavSetTrialType(j, a);
    }

    // Check max consecutive constraint
    uint8_t maxRun = 1;
    uint8_t run = 1;
    for (uint8_t i = 1; i < total; i++) {
      if (PavIsCSMinus(i) == PavIsCSMinus(i - 1)) {
        run++;
        if (run > maxRun) maxRun = run;
      } else {
        run = 1;
      }
    }

    if (maxRun <= 3) return;  // Valid shuffle
  }
  // After 50 attempts, accept last shuffle (best-effort)
}

// Approximate -ln(U) for U uniform on (0,1] without pulling in libm's log().
// Uses reduction to [1,2) range via repeated halving, then Taylor series.
static float approxNegLnU() {
  uint16_t k = random(1, 10001);
  float x = (float)k;
  float lnK = 0.0f;
  while (x >= 2.0f) {
    x *= 0.5f;
    lnK += 0.6931472f;  // ln(2)
  }
  float d = x - 1.0f;
  lnK += d - 0.5f * d * d + 0.333333f * d * d * d;
  return 9.21034f - lnK;  // ln(10000) - ln(k) = -ln(k/10000)
}

uint32_t PavlovianScheduler::PavSampleIti() {
  float negLnU = approxNegLnU();
  float iti = (float)pavItiMean * negLnU;
  return constrain((uint32_t)iti, pavItiMin, pavItiMax);
}

// --- Trial state machine ---

void PavlovianScheduler::PavTick(uint32_t now) {
  if (pavPhase == PavlovPhase::IDLE) return;

  uint32_t elapsed = now - pavPhaseStart;

  switch (pavPhase) {
    case PavlovPhase::ITI:
      if (elapsed >= pavCurrentIti) {
        PavStartTrial(now);
        pavPhase = PavlovPhase::CUE_ON;
        pavPhaseStart = now;
      }
      break;

    case PavlovPhase::CUE_ON:
      if (elapsed >= pavCueDuration) {
        LogPavlovianEvent(F("TRACE_START"), now);
        pavPhase = PavlovPhase::TRACE;
        pavPhaseStart = now;
      }
      break;

    case PavlovPhase::TRACE:
      if (elapsed >= pavTraceInterval) {
        // Deliver reward or log omission
        bool isCsMinus = PavIsCSMinus(pavTrialIndex);
        if (pavRewardThisTrial) {
          // Select pump: CS+ -> pump, CS- -> pump2 (swapped if counterbalanced)
          Pump* activePump;
          DeviceType pumpType;
          if (isCsMinus != pavCounterbalance) {
            activePump = pump2;
            pumpType = DeviceType::PUMP_2;
          } else {
            activePump = pump;
            pumpType = DeviceType::PUMP;
          }
          if (activePump && activePump->Armed()) {
            activePump->Activate(now, activePump->Duration());
            LogDeviceActivation(pumpType, now, now + activePump->Duration());
          }
          LogPavlovianEvent(F("REWARD_DELIVERED"), now);
        } else {
          LogPavlovianEvent(F("REWARD_OMITTED"), now);
        }

        // Laser activation during REWARD phase (onset delay deferred via Activate start ts, #69).
        // Clamp delay to keep onset within the REWARD window so it can't slip into a later phase/trial.
        if (laser && laser->Armed() && laser->IsContingent() && laserPhase == LaserPhase::REWARD) {
          if (ShouldFireLaser(isCsMinus)) {
            uint32_t laserDelay = laser->OnsetDelay();
            if (laserDelay >= pavConsumptionMs) laserDelay = (pavConsumptionMs > 1) ? (uint32_t)pavConsumptionMs - 1 : 0;
            uint32_t laserStart = now + laserDelay;
            laser->Activate(laserStart, laser->Duration());
            LogDeviceActivation(DeviceType::LASER, laserStart, laserStart + laser->Duration());
          }
        }

        pavPhase = PavlovPhase::REWARD;
        pavPhaseStart = now;
      }
      break;

    case PavlovPhase::REWARD:
      if (elapsed >= pavConsumptionMs) {
        pavTrialIndex++;
        if (pavTrialIndex >= pavTotalTrials) {
          LogPavlovianEvent(F("ALL_TRIALS_COMPLETE"), now);
          pavPhase = PavlovPhase::IDLE;
        } else {
          pavCurrentIti = PavSampleIti();
          pavPhase = PavlovPhase::ITI;
          pavPhaseStart = now;
        }
      }
      break;

    default:
      break;
  }
}

void PavlovianScheduler::PavStartTrial(uint32_t now) {
  bool isCsMinus = PavIsCSMinus(pavTrialIndex);

  // Resolve reward probability
  uint8_t prob = isCsMinus ? pavCsMinusProb : pavCsPlusProb;
  pavRewardThisTrial = ((uint8_t)random(100) < prob);

  // Fix: FW-003 — Silence any active cue before starting a new trial's cue
  if (cue) noTone(cue->Pin());
  if (cue2) noTone(cue2->Pin());

  // Select cue: CS+ -> cue, CS- -> cue2 (swapped if counterbalanced)
  Cue* activeCue;
  DeviceType cueType;
  if (isCsMinus != pavCounterbalance) {
    activeCue = cue2;
    cueType = DeviceType::CUE_2;
  } else {
    activeCue = cue;
    cueType = DeviceType::CUE;
  }

  if (activeCue && activeCue->Armed()) {
    activeCue->Activate(now, pavCueDuration);
    LogDeviceActivation(cueType, now, now + pavCueDuration);
  }

  // Laser activation during CUE phase (onset delay deferred via Activate start ts, #69).
  // Clamp delay to keep onset within the CUE window so it can't slip into a later phase/trial.
  if (laser && laser->Armed() && laser->IsContingent() && laserPhase == LaserPhase::CUE) {
    if (ShouldFireLaser(isCsMinus)) {
      uint32_t laserDelay = laser->OnsetDelay();
      if (laserDelay >= pavCueDuration) laserDelay = (pavCueDuration > 1) ? (uint32_t)pavCueDuration - 1 : 0;
      uint32_t laserStart = now + laserDelay;
      laser->Activate(laserStart, laser->Duration());
      LogDeviceActivation(DeviceType::LASER, laserStart, laserStart + laser->Duration());
    }
  }

  LogPavlovianTrial(now, isCsMinus);
}

// --- Logging ---

void PavlovianScheduler::LogLeverPress(DeviceType source, PressClass cls) {
  SwitchLever* lever = GetLever(source);
  if (!lever) return;

  const __FlashStringHelper* clsStr;
  switch (cls) {
    case PressClass::ACTIVE:   clsStr = F("ACTIVE");   break;
    case PressClass::INACTIVE: clsStr = F("INACTIVE"); break;
    case PressClass::TIMEOUT:  clsStr = F("TIMEOUT");  break;
  }

  Serial.print(F("{\"level\":\"007\",\"device\":\"SWITCH_LEVER\",\"pin\":"));
  Serial.print(lever->Pin());
  Serial.print(F(",\"event\":\"PRESS\",\"class\":\""));
  Serial.print(clsStr);
  Serial.print(F("\",\"start_timestamp\":"));
  Serial.print(lever->StartTimestamp() - sessionOffset);
  Serial.print(F(",\"end_timestamp\":"));
  Serial.print(lever->EndTimestamp() - sessionOffset);
  Serial.print(F(",\"orientation\":\""));
  Serial.print(lever->Orientation());
  Serial.println(F("\"}"));
}

void PavlovianScheduler::LogDeviceActivation(DeviceType target, uint32_t startTs, uint32_t endTs) {
  const __FlashStringHelper* device;
  const __FlashStringHelper* event;
  int8_t pinNum;

  switch (target) {
    case DeviceType::CUE:
      device = F("CUE"); event = F("TONE"); pinNum = cue->Pin(); break;
    case DeviceType::CUE_2:
      device = F("CUE_2"); event = F("TONE"); pinNum = cue2->Pin(); break;
    case DeviceType::PUMP:
      device = F("PUMP"); event = F("INFUSION"); pinNum = pump->Pin(); break;
    case DeviceType::PUMP_2:
      device = F("PUMP_2"); event = F("INFUSION"); pinNum = pump2->Pin(); break;
    case DeviceType::LASER:
      device = F("LASER"); event = F("STIMULATION"); pinNum = laser->Pin(); break;
    default:
      return;
  }

  Serial.print(F("{\"level\":\"007\",\"device\":\""));
  Serial.print(device);
  Serial.print(F("\",\"pin\":"));
  Serial.print(pinNum);
  Serial.print(F(",\"event\":\""));
  Serial.print(event);
  Serial.print(F("\",\"start_timestamp\":"));
  Serial.print(startTs - sessionOffset);
  Serial.print(F(",\"end_timestamp\":"));
  Serial.print(endTs - sessionOffset);
  Serial.println('}');
}

void PavlovianScheduler::LogPavlovianTrial(uint32_t now, bool isCsMinus) {
  Serial.print(F("{\"level\":\"007\",\"device\":\"PAVLOV\",\"event\":\"TRIAL_START\",\"trial\":"));
  Serial.print(pavTrialIndex);
  Serial.print(F(",\"trial_type\":\""));
  Serial.print(isCsMinus ? F("CS_MINUS") : F("CS_PLUS"));
  Serial.print(F("\",\"reward_scheduled\":"));
  Serial.print(pavRewardThisTrial ? F("true") : F("false"));
  Serial.print(F(",\"iti_ms\":"));
  Serial.print(pavCurrentIti);
  Serial.print(F(",\"timestamp\":"));
  Serial.print(now - sessionOffset);
  Serial.println('}');
}

void PavlovianScheduler::LogPavlovianEvent(const __FlashStringHelper* event, uint32_t now) {
  Serial.print(F("{\"level\":\"007\",\"device\":\"PAVLOV\",\"event\":\""));
  Serial.print(event);
  Serial.print(F("\",\"trial\":"));
  Serial.print(pavTrialIndex);
  Serial.print(F(",\"timestamp\":"));
  Serial.print(now - sessionOffset);
  Serial.println(F("\"}"));
}
