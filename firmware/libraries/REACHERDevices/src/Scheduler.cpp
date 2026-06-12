/**
 * @file Scheduler.cpp
 * @brief Scheduler implementation — contingency logic, chain execution, and JSON logging.
 */

#include "Scheduler.h"
#include "SwitchLever.h"
#include "LickCircuit.h"
#include "Cue.h"
#include "Pump.h"
#include "Laser.h"
#include "Microscope.h"

Scheduler::Scheduler() {
  leverRH = nullptr;
  leverLH = nullptr;
  lickCircuit = nullptr;
  cue = nullptr;
  cue2 = nullptr;
  pump = nullptr;
  pump2 = nullptr;
  laser = nullptr;
  microscope = nullptr;
  sessionOffset = 0;
  timeoutInterval = 20000;
  sessionActive = false;
  testMode = false;
  sessionPaused = false;
  pauseStart = 0;
  lastPressClassRH = PressClass::INACTIVE;
  lastPressClassLH = PressClass::INACTIVE;
  _lastInputSource = DeviceType::NONE;

  for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
    triggers[i].enabled = false;
    triggers[i].pressCount = 0;
  }
  for (uint8_t i = 0; i < MAX_CHAINS; i++) {
    chains[i].numSteps = 0;
  }
  for (uint8_t i = 0; i < MAX_PENDING; i++) {
    pending[i].active = false;
  }
}

void Scheduler::RegisterLever(SwitchLever* lever, DeviceType type) {
  if (type == DeviceType::LEVER_RH) leverRH = lever;
  else if (type == DeviceType::LEVER_LH) leverLH = lever;
}

void Scheduler::RegisterLickCircuit(LickCircuit* lick) {
  lickCircuit = lick;
}

void Scheduler::RegisterCue(Cue* c) {
  cue = c;
}

void Scheduler::RegisterCue2(Cue* c) {
  cue2 = c;
}

void Scheduler::RegisterPump(Pump* p) {
  pump = p;
}

void Scheduler::RegisterPump2(Pump* p) {
  pump2 = p;
}

void Scheduler::RegisterLaser(Laser* l) {
  laser = l;
}

void Scheduler::RegisterMicroscope(Microscope* mic) {
  microscope = mic;
}

void Scheduler::Update(uint32_t now) {
  if (!sessionPaused) {
    // Tick time-based triggers
    for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
      if (triggers[i].OnTick(now)) {
        FireChain(triggers[i].chainIndex, now);
      }
    }

    // Execute pending actions whose time has come
    TickPending(now);
  }

  // Tick output device state machines unconditionally (so pumps finish current infusion)
  TickOutputs(now);
}

void Scheduler::OnInputEvent(DeviceType source, uint32_t timestamp) {
  if (!sessionActive && !testMode) return;
  if (sessionPaused) return;

  // Only handle lever events for triggering
  if (source != DeviceType::LEVER_RH && source != DeviceType::LEVER_LH) return;

  _lastInputSource = source;
  PressClass cls = ClassifyPress(source, timestamp);

  // Store classification for logging on release
  if (source == DeviceType::LEVER_RH) {
    lastPressClassRH = cls;
  } else {
    lastPressClassLH = cls;
  }

  // Every ACTIVE press resets the timeout window (matching original behavior)
  if (cls == PressClass::ACTIVE) {
    SwitchLever* lever = GetLever(source);
    if (lever) {
      lever->SetTimeoutEnd(timestamp + timeoutInterval);
    }

    // Offer to triggers; fire chain if threshold met
    for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
      if (triggers[i].OnInputEvent(source, timestamp)) {
        FireChain(triggers[i].chainIndex, timestamp);
      }
    }
  }
}

void Scheduler::OnInputRelease(DeviceType source) {
  if (!sessionActive && !testMode) return;
  if (sessionPaused) return;

  if (source == DeviceType::LEVER_RH) {
    LogLeverPress(source, lastPressClassRH);
  } else if (source == DeviceType::LEVER_LH) {
    LogLeverPress(source, lastPressClassLH);
  }
}

PressClass Scheduler::ClassifyPress(DeviceType source, uint32_t timestamp) {
  SwitchLever* lever = GetLever(source);
  if (!lever) return PressClass::INACTIVE;

  if (!lever->IsReinforced()) {
    return PressClass::INACTIVE;
  }

  if (lever->InTimeout(timestamp)) {
    return PressClass::TIMEOUT;
  }

  return PressClass::ACTIVE;
}

void Scheduler::FireChain(uint8_t chainIndex, uint32_t now) {
  if (chainIndex >= MAX_CHAINS) return;
  const Chain& chain = chains[chainIndex];

  for (uint8_t i = 0; i < chain.numSteps; i++) {
    const Action& action = chain.steps[i];
    if (action.type == ActionType::NONE) continue;
    if (action.sourceFilter != DeviceType::NONE && action.sourceFilter != _lastInputSource) continue;

    if (action.offsetMs == 0) {
      ExecuteAction(action, now);
    } else {
      SchedulePending(action, now + action.offsetMs);
    }
  }
}

void Scheduler::ExecuteAction(const Action& action, uint32_t now) {
  switch (action.type) {
    case ActionType::ACTIVATE_DEVICE:
      switch (action.target) {
        case DeviceType::CUE:
          if (cue && cue->Armed()) {
            cue->Activate(now, action.param);
            LogDeviceActivation(DeviceType::CUE, now, now + action.param);
          }
          break;
        case DeviceType::CUE_2:
          if (cue2 && cue2->Armed()) {
            cue2->Activate(now, action.param);
            LogDeviceActivation(DeviceType::CUE_2, now, now + action.param);
          }
          break;
        case DeviceType::PUMP:
          if (pump && pump->Armed()) {
            pump->Activate(now, action.param);
            LogDeviceActivation(DeviceType::PUMP, now, now + action.param);
          }
          break;
        case DeviceType::PUMP_2:
          if (pump2 && pump2->Armed()) {
            pump2->Activate(now, action.param);
            LogDeviceActivation(DeviceType::PUMP_2, now, now + action.param);
          }
          break;
        case DeviceType::LASER:
          if (laser && laser->Armed() && laser->IsContingent()) {
            laser->Activate(now, action.param);
            LogDeviceActivation(DeviceType::LASER, now, now + action.param);
          }
          break;
        default:
          break;
      }
      break;

    case ActionType::SET_TIMEOUT: {
      SwitchLever* lever = GetLever(action.target);
      if (lever) {
        lever->SetTimeoutEnd(now + action.param);
      }
      break;
    }

    // WARNING: Resets ALL triggers, not just the one associated with the firing
    // chain. Currently unused by any chain.
    case ActionType::RESET_TRIGGER:
      for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
        triggers[i].Reset();
      }
      break;

    default:
      break;
  }
}

void Scheduler::TickPending(uint32_t now) {
  for (uint8_t i = 0; i < MAX_PENDING; i++) {
    // Overflow-safe comparison: (int32_t)(now - target) >= 0 handles millis() wrap
    if (pending[i].active && (int32_t)(now - pending[i].executeAt) >= 0) {
      pending[i].active = false;
      ExecuteAction(pending[i].action, now);
    }
  }
}

void Scheduler::TickOutputs(uint32_t now) {
  if (cue) cue->Await(now);
  if (cue2) cue2->Await(now);
  if (pump) pump->Await(now);
  if (pump2) pump2->Await(now);
  if (laser) laser->Await(now);
}

void Scheduler::SchedulePending(const Action& action, uint32_t fireTime) {
  for (uint8_t i = 0; i < MAX_PENDING; i++) {
    if (!pending[i].active) {
      pending[i].action = action;
      pending[i].executeAt = fireTime;
      pending[i].active = true;
      return;
    }
  }
  // Fix: FW-002 — Enhanced error with action type and scheduled time
  Serial.print(F("{\"level\":\"006\",\"error_code\":\"E_PENDING_FULL\",\"desc\":\"Pending action queue full — action dropped\",\"action_type\":"));
  Serial.print((uint8_t)action.type);
  Serial.print(F(",\"target\":"));
  Serial.print((uint8_t)action.target);
  Serial.print(F(",\"scheduled_at\":"));
  Serial.print(fireTime);
  Serial.println('}');
}

void Scheduler::LogLeverPress(DeviceType source, PressClass cls) {
  SwitchLever* lever = GetLever(source);
  if (!lever) return;

  const __FlashStringHelper* clsStr;
  switch (cls) {
    case PressClass::ACTIVE:   clsStr = F("ACTIVE");   break;
    case PressClass::INACTIVE: clsStr = F("INACTIVE"); break;
    case PressClass::TIMEOUT:  clsStr = F("TIMEOUT");  break;
  }

  const __FlashStringHelper* leverDevice =
    (source == DeviceType::LEVER_RH) ? F("LEVER_RH") : F("LEVER_LH");
  Serial.print(F("{\"level\":\"007\",\"device\":\""));
  Serial.print(leverDevice);
  Serial.print(F("\",\"pin\":"));
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

void Scheduler::LogDeviceActivation(DeviceType target, uint32_t startTs, uint32_t endTs) {
  const __FlashStringHelper* device;
  const __FlashStringHelper* event;
  int8_t pinNum;

  switch (target) {
    case DeviceType::CUE:
      device = F("CUE_1"); event = F("TONE"); pinNum = cue->Pin(); break;
    case DeviceType::CUE_2:
      device = F("CUE_2"); event = F("TONE"); pinNum = cue2->Pin(); break;
    case DeviceType::PUMP:
      device = F("PUMP_1"); event = F("INFUSION"); pinNum = pump->Pin(); break;
    case DeviceType::PUMP_2:
      device = F("PUMP_2"); event = F("INFUSION"); pinNum = pump2->Pin(); break;
    case DeviceType::LASER:
      device = F("LASER"); event = F("STIM"); pinNum = laser->Pin(); break;
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

void Scheduler::SetTimeoutInterval(uint32_t interval) {
  timeoutInterval = interval;
  for (uint8_t ci = 0; ci < MAX_CHAINS; ci++) {
    Chain* c = GetChain(ci);
    if (c) {
      for (uint8_t i = 0; i < c->numSteps; i++) {
        if (c->steps[i].type == ActionType::SET_TIMEOUT) {
          c->steps[i].param = interval;
        }
      }
    }
  }
}

void Scheduler::SetRatio(uint8_t ratio) {
  for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
    if (triggers[i].type == TriggerType::PRESS_COUNT) {
      triggers[i].threshold = ratio;
      triggers[i].pressCount = 0;
      return;
    }
  }
}

uint32_t Scheduler::TimeoutInterval() const {
  return timeoutInterval;
}

uint32_t Scheduler::SessionOffset() const {
  return sessionOffset;
}

Trigger* Scheduler::GetTrigger(uint8_t index) {
  if (index < MAX_TRIGGERS) return &triggers[index];
  return nullptr;
}

Chain* Scheduler::GetChain(uint8_t index) {
  if (index < MAX_CHAINS) return &chains[index];
  return nullptr;
}

void Scheduler::ClearPending() {
  for (uint8_t i = 0; i < MAX_PENDING; i++) {
    pending[i].active = false;
  }
}

void Scheduler::SetPaused(bool paused, uint32_t now) {
  if (!sessionActive) return;
  sessionPaused = paused;
  if (paused) {
    pauseStart = now;
    ClearPending();
    // Silence cues immediately; let pumps finish current infusion
    if (cue) noTone(cue->Pin());
    if (cue2) noTone(cue2->Pin());
  } else {
    // Fix: FW-006 — Compensate time-based triggers for pause duration
    uint32_t pausedDuration = now - pauseStart;
    for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
      if (!triggers[i].enabled) continue;
      if (triggers[i].type == TriggerType::ABSENCE_TIMER && triggers[i].absenceStart > 0) {
        triggers[i].absenceStart += pausedDuration;
      } else if (triggers[i].type == TriggerType::AVAILABILITY_WINDOW) {
        triggers[i].windowStart += pausedDuration;
        triggers[i].windowEnd += pausedDuration;
      }
    }
  }
}

bool Scheduler::IsPaused() const {
  return sessionPaused;
}

bool Scheduler::IsSessionActive() const {
  return sessionActive;
}

void Scheduler::StartSession(uint32_t now) {
  testMode = false;
  sessionPaused = false;
  sessionOffset = now;
  sessionActive = true;

  // Reset all triggers
  for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
    triggers[i].Reset();
  }

  // Initialize time-based triggers
  for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
    if (!triggers[i].enabled) continue;
    if (triggers[i].type == TriggerType::ABSENCE_TIMER) {
      triggers[i].absenceStart = now;
    } else if (triggers[i].type == TriggerType::AVAILABILITY_WINDOW) {
      triggers[i].windowStart = now + random(0, triggers[i].intervalMin);
      triggers[i].windowEnd = now + triggers[i].intervalMin;
      triggers[i].firedInWindow = false;
    }
  }

  // Clear pending queue
  for (uint8_t i = 0; i < MAX_PENDING; i++) {
    pending[i].active = false;
  }

  // Reset lever timeouts
  if (leverRH) leverRH->SetTimeoutEnd(0);
  if (leverLH) leverLH->SetTimeoutEnd(0);

  // Reset laser oscillation state from previous session
  if (laser) laser->Reset();
  if (laser) laser->SetSessionActive(true);
}

void Scheduler::EndSession(uint32_t now) {
  sessionActive = false;
  sessionPaused = false;

  // Clear pending queue
  for (uint8_t i = 0; i < MAX_PENDING; i++) {
    pending[i].active = false;
  }

  // Force all outputs off
  if (cue) noTone(cue->Pin());
  if (cue2) noTone(cue2->Pin());
  if (pump) digitalWrite(pump->Pin(), LOW);
  if (pump2) digitalWrite(pump2->Pin(), LOW);
  if (laser) laser->SetSessionActive(false);
  if (laser) digitalWrite(laser->Pin(), LOW);
}

void Scheduler::TestChain(uint32_t now) {
  if (sessionActive) return;
  ClearPending();
  sessionOffset = now;
  FireChain(0, now);
}

void Scheduler::SetTestMode(bool enable, uint32_t now) {
  if (sessionActive) return;
  testMode = enable;
  if (enable) {
    sessionOffset = now;
    ClearPending();
    for (uint8_t i = 0; i < MAX_TRIGGERS; i++) {
      triggers[i].Reset();
    }
    if (leverRH) leverRH->SetTimeoutEnd(0);
    if (leverLH) leverLH->SetTimeoutEnd(0);
  } else {
    ClearPending();
    if (cue) noTone(cue->Pin());
    if (cue2) noTone(cue2->Pin());
    if (pump) digitalWrite(pump->Pin(), LOW);
    if (pump2) digitalWrite(pump2->Pin(), LOW);
    if (laser) digitalWrite(laser->Pin(), LOW);
  }
}

bool Scheduler::IsTestMode() const {
  return testMode;
}

SwitchLever* Scheduler::GetLever(DeviceType type) {
  if (type == DeviceType::LEVER_RH) return leverRH;
  if (type == DeviceType::LEVER_LH) return leverLH;
  return nullptr;
}
