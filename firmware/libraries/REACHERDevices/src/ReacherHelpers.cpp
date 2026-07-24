/**
 * @file ReacherHelpers.cpp
 * @brief Shared session helpers used by all REACHER sketch variants.
 */

#include "ReacherHelpers.h"
#include "SwitchLever.h"
#include "Cue.h"
#include "Pump.h"
#include "Laser.h"
#include "LickCircuit.h"
#include "Microscope.h"

void setDeviceTimestampOffset(DeviceSet& ds, uint32_t ts) {
  ds.rLever->SetOffset(ts);
  ds.lLever->SetOffset(ts);
  ds.cue->SetOffset(ts);
  ds.cue2->SetOffset(ts);
  ds.pump->SetOffset(ts);
  ds.pump2->SetOffset(ts);
  ds.lickCircuit->SetOffset(ts);
  if (ds.laser) ds.laser->SetOffset(ts);
  if (ds.microscope) ds.microscope->SetOffset(ts);
  // SLM is excluded: Slm does not inherit Device; each sketch calls slm.SetOffset() directly.
}

ArmSnapshot captureArmState(const DeviceSet& ds) {
  ArmSnapshot snap;
  snap.rLever      = ds.rLever->Armed();
  snap.lLever      = ds.lLever->Armed();
  snap.cue         = ds.cue->Armed();
  snap.cue2        = ds.cue2->Armed();
  snap.pump        = ds.pump->Armed();
  snap.pump2       = ds.pump2->Armed();
  snap.lickCircuit = ds.lickCircuit->Armed();
  snap.laser       = ds.laser ? ds.laser->Armed() : false;
  snap.microscope  = ds.microscope ? ds.microscope->Armed() : false;
  snap.slm         = false;  // SLM excluded (Slm does not inherit Device)
  return snap;
}

void restoreArmState(DeviceSet& ds, const ArmSnapshot& snap) {
  ds.rLever->ArmToggle(snap.rLever);
  ds.lLever->ArmToggle(snap.lLever);
  ds.cue->ArmToggle(snap.cue);
  ds.cue2->ArmToggle(snap.cue2);
  ds.pump->ArmToggle(snap.pump);
  ds.pump2->ArmToggle(snap.pump2);
  ds.lickCircuit->ArmToggle(snap.lickCircuit);
  if (ds.laser) ds.laser->ArmToggle(snap.laser);
  if (ds.microscope) ds.microscope->ArmToggle(snap.microscope);
}

void armToggleDevices(DeviceSet& ds, bool toggle) {
  ds.rLever->ArmToggle(toggle);
  ds.lLever->ArmToggle(toggle);
  ds.cue->ArmToggle(toggle);
  ds.cue2->ArmToggle(toggle);
  ds.pump->ArmToggle(toggle);
  ds.pump2->ArmToggle(toggle);
  ds.lickCircuit->ArmToggle(toggle);
  if (ds.laser) ds.laser->ArmToggle(toggle);
  if (ds.microscope) ds.microscope->ArmToggle(toggle);
}

void reportDeviceConfig(const __FlashStringHelper* dev, bool armed,
                        uint32_t freq, uint32_t dur) {
  Serial.print(F("{\"level\":\"000\",\"device\":\""));
  Serial.print(dev);
  Serial.print(F("\",\"armed\":"));
  Serial.print(armed ? F("true") : F("false"));
  Serial.print(F(",\"frequency\":"));
  Serial.print(freq);
  Serial.print(F(",\"duration\":"));
  Serial.print(dur);
  Serial.println('}');
}

void reportDeviceConfig(const __FlashStringHelper* dev, bool armed,
                        uint32_t dur) {
  Serial.print(F("{\"level\":\"000\",\"device\":\""));
  Serial.print(dev);
  Serial.print(F("\",\"armed\":"));
  Serial.print(armed ? F("true") : F("false"));
  Serial.print(F(",\"duration\":"));
  Serial.print(dur);
  Serial.println('}');
}

void reportDeviceConfig(const __FlashStringHelper* dev, bool armed) {
  Serial.print(F("{\"level\":\"000\",\"device\":\""));
  Serial.print(dev);
  Serial.print(F("\",\"armed\":"));
  Serial.print(armed ? F("true") : F("false"));
  Serial.println('}');
}

void reportDeviceLever(const __FlashStringHelper* dev, bool armed,
                       bool reinforced) {
  Serial.print(F("{\"level\":\"000\",\"device\":\""));
  Serial.print(dev);
  Serial.print(F("\",\"armed\":"));
  Serial.print(armed ? F("true") : F("false"));
  Serial.print(F(",\"reinforced\":"));
  Serial.print(reinforced ? F("true") : F("false"));
  Serial.println('}');
}

static uint32_t clampParam(JsonDocument& doc, const char* key, uint32_t minVal, uint32_t maxVal) {
  uint32_t val = doc[key] | minVal;
  if (val < minVal) val = minVal;
  if (val > maxVal) val = maxVal;
  return val;
}

bool handleCommonDeviceCommand(DeviceSet& ds, int command, JsonDocument& inputJson) {
  switch (command) {
    // --- Cue ---
    case Cmd::CUE_ARM:            ds.cue->ArmToggle(true); break;
    case Cmd::CUE_DISARM:         ds.cue->ArmToggle(false); break;
    case Cmd::CUE_SET_FREQUENCY: {
      uint32_t freq = clampParam(inputJson, "frequency", 1, 65535);
      ds.cue->SetFrequency(freq);
      logParamChange(F("CUE"), F("frequency"), freq); break;
    }
    case Cmd::CUE_SET_DURATION: {
      uint32_t dur = clampParam(inputJson, "duration", 1, 600000);
      ds.cue->SetDuration(dur);
      logParamChange(F("CUE"), F("duration"), dur); break;
    }
    case Cmd::CUE_TEST:
      ds.cue->Test(millis());
      logParamChange(F("CUE"), F("test"), F("FIRED")); break;
    case Cmd::CUE_SET_TRACE:      break;

    // --- Cue 2 ---
    case Cmd::CUE2_ARM:           ds.cue2->ArmToggle(true); break;
    case Cmd::CUE2_DISARM:        ds.cue2->ArmToggle(false); break;
    case Cmd::CUE2_SET_FREQUENCY: {
      uint32_t freq = clampParam(inputJson, "frequency", 1, 65535);
      ds.cue2->SetFrequency(freq);
      logParamChange(F("CUE2"), F("frequency"), freq); break;
    }
    case Cmd::CUE2_SET_DURATION: {
      uint32_t dur = clampParam(inputJson, "duration", 1, 600000);
      ds.cue2->SetDuration(dur);
      logParamChange(F("CUE2"), F("duration"), dur); break;
    }
    case Cmd::CUE2_TEST:
      ds.cue2->Test(millis());
      logParamChange(F("CUE2"), F("test"), F("FIRED")); break;

    // --- Pump ---
    case Cmd::PUMP_ARM:           ds.pump->ArmToggle(true); break;
    case Cmd::PUMP_DISARM:        ds.pump->ArmToggle(false); break;
    case Cmd::PUMP_SET_DURATION: {
      uint32_t dur = clampParam(inputJson, "duration", 1, 600000);
      ds.pump->SetDuration(dur);
      logParamChange(F("PUMP"), F("duration"), dur); break;
    }
    case Cmd::PUMP_TEST:
      ds.pump->Test(millis());
      logParamChange(F("PUMP"), F("test"), F("FIRED")); break;
    case Cmd::PUMP_SET_TRACE:     break;

    // --- Pump 2 ---
    case Cmd::PUMP2_ARM:          ds.pump2->ArmToggle(true); break;
    case Cmd::PUMP2_DISARM:       ds.pump2->ArmToggle(false); break;
    case Cmd::PUMP2_SET_DURATION: {
      uint32_t dur = clampParam(inputJson, "duration", 1, 600000);
      ds.pump2->SetDuration(dur);
      logParamChange(F("PUMP2"), F("duration"), dur); break;
    }
    case Cmd::PUMP2_TEST:
      ds.pump2->Test(millis());
      logParamChange(F("PUMP2"), F("test"), F("FIRED")); break;

    // --- Lick ---
    case Cmd::LICK_ARM:    ds.lickCircuit->ArmToggle(true); break;
    case Cmd::LICK_DISARM: ds.lickCircuit->ArmToggle(false); break;

    // --- Laser ---
    case Cmd::LASER_ARM:
      if (ds.laser) ds.laser->ArmToggle(true); break;
    case Cmd::LASER_DISARM:
      if (ds.laser) ds.laser->ArmToggle(false); break;
    case Cmd::LASER_TEST:
      if (ds.laser) { ds.laser->Test(millis()); logParamChange(F("LASER"), F("test"), F("FIRED")); } break;
    case Cmd::LASER_SET_FREQUENCY: {
      uint32_t freq = clampParam(inputJson, "frequency", 1, 65535);
      if (ds.laser) { ds.laser->SetFrequency(freq); logParamChange(F("LASER"), F("frequency"), freq); } break;
    }
    case Cmd::LASER_SET_DURATION: {
      uint32_t dur = clampParam(inputJson, "duration", 1, 600000);
      if (ds.laser) { ds.laser->SetDuration(dur); logParamChange(F("LASER"), F("duration"), dur); } break;
    }
    case Cmd::LASER_MODE_CONTINGENT:
      if (ds.laser) { ds.laser->SetMode(true); logParamChange(F("LASER"), F("mode"), F("CONTINGENT")); } break;
    case Cmd::LASER_MODE_INDEPENDENT:
      if (ds.laser) { ds.laser->SetMode(false); logParamChange(F("LASER"), F("mode"), F("INDEPENDENT")); } break;

    // --- Microscope ---
    // Boards without a microscope (e.g. fr_lite on UNO) report these as unhandled,
    // matching how SLM commands (absent from this shared helper) already fall
    // through to the sketch's own "Command not found" default, rather than
    // silently acking a command that changed nothing.
    case Cmd::MICROSCOPE_ARM:
      if (!ds.microscope) return false;
      ds.microscope->ArmToggle(true); break;
    case Cmd::MICROSCOPE_DISARM:
      if (!ds.microscope) return false;
      ds.microscope->ArmToggle(false); break;
    case Cmd::MICROSCOPE_TEST:
      if (!ds.microscope) return false;
      ds.microscope->Trigger(); logParamChange(F("MICROSCOPE"), F("test"), F("FIRED")); break;

    // --- Pin reassignment (suffix x76) ---
    // Backend validates board / role / collisions; firmware clamps to 2-53.
    case Cmd::CUE_SET_PIN: {
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.cue->SetPin((int8_t)p); break;
    }
    case Cmd::CUE2_SET_PIN: {
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.cue2->SetPin((int8_t)p); break;
    }
    case Cmd::PUMP_SET_PIN: {
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.pump->SetPin((int8_t)p); break;
    }
    case Cmd::PUMP2_SET_PIN: {
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.pump2->SetPin((int8_t)p); break;
    }
    case Cmd::LICK_SET_PIN: {
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.lickCircuit->SetPin((int8_t)p); break;
    }
    case Cmd::LASER_SET_PIN: {
      if (!ds.laser) break;
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.laser->SetPin((int8_t)p); break;
    }
    case Cmd::MICROSCOPE_SET_TRIG_PIN: {
      if (!ds.microscope) return false;
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.microscope->SetTriggerPin((int8_t)p); break;
    }

    case Cmd::LEVER_RH_SET_PIN: {
      if (!ds.rLever) break;
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.rLever->SetPin((int8_t)p); break;
    }
    case Cmd::LEVER_LH_SET_PIN: {
      if (!ds.lLever) break;
      uint32_t p = clampParam(inputJson, "pin", 2, 53);
      ds.lLever->SetPin((int8_t)p); break;
    }

    default:
      return false;  // Not handled — let sketch handle it
  }
  return true;
}
