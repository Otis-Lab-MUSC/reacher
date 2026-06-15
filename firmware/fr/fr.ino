/**
 * @file fr.ino
 * @brief REACHER v2.0.0 Fixed Ratio (FR) operant conditioning controller.
 *
 * N active lever presses produce a reward (cue -> pump + laser).
 * The ratio is configurable via serial command (SET_RATIO).
 *
 * **Baud rate:** 115200
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <avr/wdt.h>

#include <Commands.h>
#include <Pins.h>
#include <Device.h>
#include <SwitchLever.h>
#include <LickCircuit.h>
#include <Cue.h>
#include <Pump.h>
#include <Laser.h>
#include <Microscope.h>
#include <Slm.h>
#include <Scheduler.h>
#include <ReacherHelpers.h>
#include "Config.h"

// Configurable parameters (updated via serial commands)
uint32_t CUE_DURATION       = DEFAULT_CUE_DURATION;
uint32_t CUE_FREQUENCY      = DEFAULT_CUE_FREQUENCY;
uint32_t PUMP_DURATION      = DEFAULT_PUMP_DURATION;
uint8_t  LASER_FREQUENCY    = DEFAULT_LASER_FREQUENCY;
uint32_t LASER_DURATION     = DEFAULT_LASER_DURATION;
bool     LASER_RH_ONLY_MODE = false;
uint32_t TIMEOUT_INTERVAL   = DEFAULT_TIMEOUT_INTERVAL;
uint32_t TRACE_INTERVAL     = 0;

// Per-device onset delay shadows (ms) — survive ReconfigureChain()
uint32_t CUE_ONSET_DELAY   = 0;
uint32_t PUMP_ONSET_DELAY  = 0;
uint32_t PUMP2_ONSET_DELAY = 0;

// Per-device lever source filter shadows — survive ReconfigureChain()
DeviceType CUE_SOURCE_FILTER   = DeviceType::NONE;
DeviceType PUMP_SOURCE_FILTER  = DeviceType::NONE;
DeviceType PUMP2_SOURCE_FILTER = DeviceType::NONE;

// Device instances
SwitchLever rLever(PIN_LEVER_RH, "RH", DeviceType::LEVER_RH);
SwitchLever lLever(PIN_LEVER_LH, "LH", DeviceType::LEVER_LH);
SwitchLever* activeLever    = &rLever;

Cue         cue(PIN_CUE, CUE_FREQUENCY, CUE_DURATION);
Cue         cue2(PIN_CUE_2, CUE_FREQUENCY, CUE_DURATION);
Pump        pump(PIN_PUMP, PUMP_DURATION);
Pump        pump2(PIN_PUMP_2, PUMP_DURATION);
Pump*        activePump      = &pump;
DeviceType   activePumpTarget = DeviceType::PUMP;
LickCircuit lickCircuit(PIN_LICK_CIRCUIT);
Laser       laser(PIN_LASER, LASER_FREQUENCY, LASER_DURATION);
Microscope  microscope(PIN_MICROSCOPE_TRIG, PIN_MICROSCOPE_TS);
Slm         slm(PIN_SLM_TS);

Scheduler scheduler;

DeviceSet devices = { &rLever, &lLever, &cue, &cue2, &pump, &pump2, &lickCircuit, &laser, &microscope, &slm };

uint32_t SESSION_START_TIMESTAMP;
uint32_t SESSION_END_TIMESTAMP;
bool sessionEndPending = false;
#define SCOPE_DRAIN_MS 200


// Forward declarations
void ParseCommands();
void StartSession();
void EndSession();
void ReconfigureChain();
void SendIdentification();

ISR(PCINT0_vect) { Slm::instance->HandlePCINT(); }

void onLeverPress(DeviceType source, uint32_t timestamp) {
  scheduler.OnInputEvent(source, timestamp);
}

void onLeverRelease(DeviceType source) {
  scheduler.OnInputRelease(source);
}

void SendIdentification() {
  Serial.println(F("{\"level\":\"000\",\"device\":\"CONTROLLER\",\"sketch\":\"fr.ino\",\"version\":\"v3.0.0-beta.1\",\"baud_rate\":115200,\"schedule\":\"FIXED_RATIO\"}"));
}

void setup() {
  delay(100);
  Serial.begin(115200);
  Serial.setTimeout(100);
  delay(100);
  while (Serial.available()) Serial.read();  // Drain bootloader residue
  randomSeed(analogRead(A0) ^ micros());  // Fix: FW-004

  cue.Jingle();

  scheduler.RegisterLever(&rLever, DeviceType::LEVER_RH);
  scheduler.RegisterLever(&lLever, DeviceType::LEVER_LH);
  scheduler.RegisterLickCircuit(&lickCircuit);
  scheduler.RegisterCue(&cue);
  scheduler.RegisterCue2(&cue2);
  scheduler.RegisterPump(&pump);
  scheduler.RegisterPump2(&pump2);
  scheduler.RegisterLaser(&laser);
  scheduler.RegisterMicroscope(&microscope);

  rLever.SetCallback(onLeverPress);
  rLever.SetReleaseCallback(onLeverRelease);
  lLever.SetCallback(onLeverPress);
  lLever.SetReleaseCallback(onLeverRelease);

  rLever.SetActiveLever(true);
  lLever.SetActiveLever(false);

  scheduler.SetTimeoutInterval(TIMEOUT_INTERVAL);
  configureFixedRatio(scheduler, cue, *activePump, laser, 1, DeviceType::LEVER_RH, TRACE_INTERVAL, activePumpTarget);

  SendIdentification();
  wdt_enable(WDTO_8S);
}

void loop() {
  wdt_reset();
  uint32_t currentTimestamp = millis();

  rLever.Monitor(currentTimestamp);
  lLever.Monitor(currentTimestamp);
  lickCircuit.Monitor(currentTimestamp);

  scheduler.Update(currentTimestamp);
  microscope.HandleFrameSignal();
  microscope.TickTrigger(currentTimestamp);  // Fix: FW-001
  slm.HandleTimestampSignal();
  if (sessionEndPending && (int32_t)(millis() - SESSION_END_TIMESTAMP) >= SCOPE_DRAIN_MS) {
    armToggleDevices(devices, false);
    slm.ArmToggle(false);
    sessionEndPending = false;
  }
  ParseCommands();
}

void ReconfigureChain() {
  Trigger* t = scheduler.GetTrigger(0);
  uint8_t currentRatio = t ? t->threshold : 1;
  DeviceType timeoutTarget = (activeLever == &rLever) ? DeviceType::LEVER_RH : DeviceType::LEVER_LH;
  configureFixedRatio(scheduler, cue, *activePump, laser, currentRatio, timeoutTarget, TRACE_INTERVAL, activePumpTarget, CUE_SOURCE_FILTER, PUMP_SOURCE_FILTER, PUMP2_SOURCE_FILTER);
  {
    Chain* c0 = scheduler.GetChain(0);
    if (c0 && c0->numSteps >= 3) {
      uint32_t pd = (activePump == &pump2) ? PUMP2_ONSET_DELAY : PUMP_ONSET_DELAY;
      c0->steps[0].offsetMs += CUE_ONSET_DELAY;
      c0->steps[1].offsetMs += CUE_ONSET_DELAY + pd;
      c0->steps[2].offsetMs += CUE_ONSET_DELAY;
    }
  }
  if (LASER_RH_ONLY_MODE) {
    Chain* c = scheduler.GetChain(0);
    if (c && c->numSteps >= 3) c->steps[2].type = ActionType::NONE;
    Trigger* t1 = scheduler.GetTrigger(1);
    if (t1) {
      t1->type = TriggerType::PRESS_COUNT;
      t1->chainIndex = 1;
      t1->enabled = true;
      t1->threshold = 1;
      t1->initialThreshold = 1;
      t1->pressCount = 0;
      t1->prStep = 0;
      t1->sourceFilter = DeviceType::LEVER_RH;
      t1->probability = 100;
    }
    Chain* c1 = scheduler.GetChain(1);
    if (c1) {
      c1->numSteps = 1;
      c1->steps[0].type = ActionType::ACTIVATE_DEVICE;
      c1->steps[0].target = DeviceType::LASER;
      c1->steps[0].sourceFilter = DeviceType::NONE;
      c1->steps[0].offsetMs = laser.OnsetDelay();
      c1->steps[0].param = laser.Duration();
    }
  } else {
    Trigger* t1 = scheduler.GetTrigger(1);
    if (t1) t1->enabled = false;
  }
}

void StartSession() {
  SESSION_START_TIMESTAMP = millis();
  microscope.Trigger();
  scheduler.StartSession(SESSION_START_TIMESTAMP);

  Serial.println(F("{\"level\":\"007\",\"device\":\"CONTROLLER\",\"event\":\"START\",\"timestamp\":0}"));

  Serial.print(F("{\"level\":\"000\",\"device\":\"CONTROLLER\",\"paradigm\":\"FIXED_RATIO\",\"timeout\":"));
  Serial.print(TIMEOUT_INTERVAL);
  Serial.print(F(",\"active_lever\":\""));
  Serial.print((activeLever == &rLever) ? F("RH") : F("LH"));
  Serial.print(F("\",\"trace_interval\":"));
  Serial.print(TRACE_INTERVAL);
  Serial.println('}');

  reportDeviceConfig(F("CUE"), cue.Armed(), CUE_FREQUENCY, CUE_DURATION);
  reportDeviceConfig(F("CUE2"), cue2.Armed(), cue2.Frequency(), cue2.Duration());
  reportDeviceConfig(F("PUMP"), pump.Armed(), PUMP_DURATION);
  reportDeviceConfig(F("PUMP2"), pump2.Armed(), pump2.Duration());
  reportDeviceConfig(F("LASER"), laser.Armed(), LASER_FREQUENCY, LASER_DURATION);
  reportDeviceConfig(F("LICK"), lickCircuit.Armed());
  reportDeviceConfig(F("MICROSCOPE"), microscope.Armed());
  reportDeviceConfig(F("SLM"), slm.Armed());
  reportDeviceLever(F("LEVER_RH"), rLever.Armed(), rLever.IsReinforced());
  reportDeviceLever(F("LEVER_LH"), lLever.Armed(), lLever.IsReinforced());
}

void EndSession() {
  SESSION_END_TIMESTAMP = millis();
  microscope.Pause(SESSION_END_TIMESTAMP);
  scheduler.EndSession(SESSION_END_TIMESTAMP);

  Serial.print(F("{\"level\":\"007\",\"device\":\"CONTROLLER\",\"event\":\"END\",\"timestamp\":"));
  Serial.print(SESSION_END_TIMESTAMP - SESSION_START_TIMESTAMP);
  Serial.println('}');
}

void ParseCommands() {
  if (Serial.available() > 0) {
    JsonDocument inputJson;
    char buf[128];
    int len = Serial.readBytesUntil('\n', buf, sizeof(buf) - 1);
    if (len == (int)(sizeof(buf) - 1)) {
      while (Serial.available() && Serial.read() != '\n') {}
    }
    buf[len] = '\0';

    if (len >= 5 && memcmp(buf, "*IDN?", 5) == 0) {
      SendIdentification();
      return;
    }

    DeserializationError error = deserializeJson(inputJson, buf);

    if (error) {
      Serial.print(F("{\"level\":\"006\",\"desc\":\""));
      Serial.print(error.f_str());
      Serial.println(F("\"}"));
      while (Serial.available() > 0) Serial.read();
      return;
    }

    if (!inputJson["cmd"].isNull()) {
      int command = inputJson["cmd"];

      if (handleCommonDeviceCommand(devices, command, inputJson)) {
        // Update local shadow variables and reconfigure chain for duration changes
        switch (command) {
          case Cmd::CUE_SET_FREQUENCY:   CUE_FREQUENCY = inputJson["frequency"]; break;
          case Cmd::CUE_SET_DURATION:    CUE_DURATION = inputJson["duration"]; ReconfigureChain(); break;
          case Cmd::PUMP_SET_DURATION:   PUMP_DURATION = inputJson["duration"]; ReconfigureChain(); break;
          case Cmd::PUMP2_SET_DURATION:  ReconfigureChain(); break;
          case Cmd::LASER_SET_FREQUENCY: LASER_FREQUENCY = inputJson["frequency"]; break;
          case Cmd::LASER_SET_DURATION:  LASER_DURATION = inputJson["duration"]; ReconfigureChain(); break;
          case Cmd::LASER_MODE_CONTINGENT: LASER_RH_ONLY_MODE = false; ReconfigureChain(); break;
        }
      } else {
        switch (command) {
          case Cmd::LASER_SET_ONSET_DELAY:
          case Cmd::CUE_SET_ONSET_DELAY:
          case Cmd::CUE2_SET_ONSET_DELAY:
          case Cmd::PUMP_SET_ONSET_DELAY:
          case Cmd::PUMP2_SET_ONSET_DELAY: {
            uint32_t d = (uint32_t)inputJson["delay"]; if (d > 60000) d = 60000;
            if      (command == Cmd::LASER_SET_ONSET_DELAY) { laser.SetOnsetDelay(d); if (LASER_RH_ONLY_MODE) ReconfigureChain(); }
            else if (command == Cmd::CUE_SET_ONSET_DELAY)   { CUE_ONSET_DELAY = d;   ReconfigureChain(); }
            else if (command == Cmd::CUE2_SET_ONSET_DELAY)  { /* cue2 not in chain */ }
            else if (command == Cmd::PUMP_SET_ONSET_DELAY)  { PUMP_ONSET_DELAY = d;  ReconfigureChain(); }
            else                                             { PUMP2_ONSET_DELAY = d; ReconfigureChain(); }
            break;
          }
          case Cmd::LASER_TRIGGER_RH_ONLY: LASER_RH_ONLY_MODE = true; ReconfigureChain(); break;
          // RH lever commands
          case Cmd::LEVER_RH_ARM:          rLever.ArmToggle(true); break;
          case Cmd::LEVER_RH_DISARM:       rLever.ArmToggle(false); break;
          case Cmd::LEVER_RH_SET_TIMEOUT:
            TIMEOUT_INTERVAL = inputJson["timeout"]; scheduler.SetTimeoutInterval(TIMEOUT_INTERVAL);
            logParamChange(F("LEVER_RH"), F("timeout"), TIMEOUT_INTERVAL); break;
          case Cmd::LEVER_RH_SET_RATIO:
            scheduler.SetRatio(inputJson["ratio"]);
            logParamChange(F("LEVER_RH"), F("ratio"), (uint32_t)inputJson["ratio"]); break;
          case Cmd::LEVER_RH_SET_ACTIVE:
            rLever.SetActiveLever(true); activeLever = &rLever;
            logParamChange(F("LEVER_RH"), F("reinforced"), true); break;
          case Cmd::LEVER_RH_SET_INACTIVE:
            rLever.SetActiveLever(false);
            logParamChange(F("LEVER_RH"), F("reinforced"), false); break;

          // LH lever commands
          case Cmd::LEVER_LH_ARM:          lLever.ArmToggle(true); break;
          case Cmd::LEVER_LH_DISARM:       lLever.ArmToggle(false); break;
          case Cmd::LEVER_LH_SET_TIMEOUT:
            TIMEOUT_INTERVAL = inputJson["timeout"]; scheduler.SetTimeoutInterval(TIMEOUT_INTERVAL);
            logParamChange(F("LEVER_LH"), F("timeout"), TIMEOUT_INTERVAL); break;
          case Cmd::LEVER_LH_SET_RATIO:
            scheduler.SetRatio(inputJson["ratio"]);
            logParamChange(F("LEVER_LH"), F("ratio"), (uint32_t)inputJson["ratio"]); break;
          case Cmd::LEVER_LH_SET_ACTIVE:
            lLever.SetActiveLever(true); activeLever = &lLever;
            logParamChange(F("LEVER_LH"), F("reinforced"), true); break;
          case Cmd::LEVER_LH_SET_INACTIVE:
            lLever.SetActiveLever(false);
            logParamChange(F("LEVER_LH"), F("reinforced"), false); break;

          // Session setup commands
          case Cmd::SET_RATIO:
            scheduler.SetRatio(inputJson["ratio"]);
            logParamChange(F("CONTROLLER"), F("ratio"), (uint32_t)inputJson["ratio"]); break;
          case Cmd::SET_TRACE_INTERVAL:
            TRACE_INTERVAL = inputJson["interval"];
            ReconfigureChain();
            logParamChange(F("CONTROLLER"), F("trace_interval"), TRACE_INTERVAL); break;
          case Cmd::SET_ACTIVE_PUMP: {
            bool usePump2 = inputJson["pump2"] | false;
            activePump = usePump2 ? &pump2 : &pump;
            activePumpTarget = usePump2 ? DeviceType::PUMP_2 : DeviceType::PUMP;
            ReconfigureChain();
            logParamChange(F("CONTROLLER"), F("active_pump"), usePump2 ? F("PUMP2") : F("PUMP"));
            break;
          }

          // Controller commands
          case Cmd::SESSION_START:
            StartSession(); setDeviceTimestampOffset(devices, SESSION_START_TIMESTAMP); slm.SetOffset(SESSION_START_TIMESTAMP); break;
          case Cmd::SESSION_END:
            if (!sessionEndPending) { EndSession(); sessionEndPending = true; }
            break;
          case Cmd::IDENTIFY:
            if (!scheduler.IsSessionActive()) {
              armToggleDevices(devices, false);
              slm.ArmToggle(false);
            }
            SendIdentification(); break;
          case Cmd::TEST_CHAIN:
            scheduler.TestChain(millis());
            logParamChange(F("CONTROLLER"), F("test_chain"), F("FIRED")); break;
          case Cmd::TEST_MODE: {
            bool enable = inputJson["enable"] | false;
            scheduler.SetTestMode(enable, millis());
            if (!enable) ReconfigureChain();
            logParamChange(F("CONTROLLER"), F("test_mode"), scheduler.IsTestMode());
            break;
          }
          case Cmd::SESSION_PAUSE: {
            bool paused = inputJson["paused"] | false;
            uint32_t now = millis();
            scheduler.SetPaused(paused, now);
            if (paused) microscope.Pause(now);
            else        microscope.Resume(now);
            logParamChange(F("CONTROLLER"), F("session_paused"), paused);
            break;
          }

          case Cmd::CUE_SET_LEVER_FILTER:
          case Cmd::CUE2_SET_LEVER_FILTER:
          case Cmd::PUMP_SET_LEVER_FILTER:
          case Cmd::PUMP2_SET_LEVER_FILTER: {
            int val = inputJson["filter"] | 0;
            DeviceType srcFilter = DeviceType::NONE;
            if (val == 1) srcFilter = DeviceType::LEVER_RH;
            else if (val == 2) srcFilter = DeviceType::LEVER_LH;
            if (command == Cmd::CUE_SET_LEVER_FILTER)        CUE_SOURCE_FILTER  = srcFilter;
            else if (command == Cmd::PUMP_SET_LEVER_FILTER)  PUMP_SOURCE_FILTER = srcFilter;
            else if (command == Cmd::PUMP2_SET_LEVER_FILTER) PUMP2_SOURCE_FILTER = srcFilter;
            ReconfigureChain();
            if (srcFilter == DeviceType::LEVER_LH)      lLever.SetActiveLever(true);
            else if (srcFilter == DeviceType::LEVER_RH) rLever.SetActiveLever(true);
            logParamChange(F("CONTROLLER"), F("lever_filter"), (uint32_t)val);
            break;
          }

          case Cmd::SLM_ARM:    slm.ArmToggle(true); break;
          case Cmd::SLM_DISARM: slm.ArmToggle(false); break;
          case Cmd::SLM_SET_PIN: {
            uint8_t p = (uint8_t)constrain((int)(inputJson["pin"] | 11), 8, 13);
            slm.SetPin((int8_t)p); break;
          }

          default:
            Serial.println(F("{\"level\":\"006\",\"desc\":\"Command not found\"}"));
            break;
        }
      }
    }
  }
}
