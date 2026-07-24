/**
 * @file omission.ino
 * @brief REACHER v2.0.0 Omission operant conditioning controller.
 *
 * Reward fires after the animal withholds lever pressing for a configurable
 * absence interval. Any press resets the timer. No timeout is applied.
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

// Configurable parameters
uint32_t CUE_DURATION       = DEFAULT_CUE_DURATION;
uint32_t CUE_FREQUENCY      = DEFAULT_CUE_FREQUENCY;
uint32_t PUMP_DURATION      = DEFAULT_PUMP_DURATION;
uint8_t  LASER_FREQUENCY    = DEFAULT_LASER_FREQUENCY;
uint32_t LASER_DURATION     = DEFAULT_LASER_DURATION;
bool     LASER_RH_ONLY_MODE = false;
uint32_t OMISSION_INTERVAL  = 20000;

// Per-device onset delay shadows (ms) — survive ReconfigureChain()
uint32_t CUE_ONSET_DELAY   = 0;
uint32_t PUMP_ONSET_DELAY  = 0;
uint32_t PUMP2_ONSET_DELAY = 0;

// Per-device lever source filter shadows — set by cmds 378/388/478/488; survive StartSession() and ReconfigureChain()
DeviceType CUE_SOURCE_FILTER   = DeviceType::NONE;
DeviceType CUE2_SOURCE_FILTER  = DeviceType::NONE;
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

ISR(PCINT0_vect) { if (Slm::instance) Slm::instance->HandlePCINT(); }

void onLeverPress(DeviceType source, uint32_t timestamp) {
  scheduler.OnInputEvent(source, timestamp);
}

void onLeverRelease(DeviceType source) {
  scheduler.OnInputRelease(source);
}

void SendIdentification() {
  Serial.println(F("{\"level\":\"000\",\"device\":\"CONTROLLER\",\"sketch\":\"omission.ino\",\"version\":\"v3.2.0\",\"baud_rate\":115200,\"schedule\":\"OMISSION\"}"));
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

  // Omission has no timeout
  scheduler.SetTimeoutInterval(0);
  configureOmission(scheduler, cue, cue2, *activePump, laser, OMISSION_INTERVAL, activePumpTarget);

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
  configureOmission(scheduler, cue, cue2, *activePump, laser, OMISSION_INTERVAL, activePumpTarget, CUE_SOURCE_FILTER, CUE2_SOURCE_FILTER, PUMP_SOURCE_FILTER, PUMP2_SOURCE_FILTER);
  {
    Chain* c0 = scheduler.GetChain(0);
    if (c0 && c0->numSteps >= 4) {
      uint32_t pd = (activePump == &pump2) ? PUMP2_ONSET_DELAY : PUMP_ONSET_DELAY;
      // Each device's offset originates from press onset via its own delay only —
      // no device's timing depends on another device's onset/duration.
      c0->steps[0].offsetMs = CUE_ONSET_DELAY;
      c0->steps[1].offsetMs = CUE_ONSET_DELAY;
      c0->steps[2].offsetMs = pd;
      c0->steps[3].offsetMs = laser.OnsetDelay();
    }
  }
  if (LASER_RH_ONLY_MODE) {
    Chain* c = scheduler.GetChain(0);
    if (c && c->numSteps >= 4) c->steps[3].type = ActionType::NONE;
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
  ReconfigureChain();

  Serial.println(F("{\"level\":\"007\",\"device\":\"CONTROLLER\",\"event\":\"START\",\"timestamp\":0}"));

  Serial.print(F("{\"level\":\"000\",\"device\":\"CONTROLLER\",\"paradigm\":\"OMISSION\",\"active_lever\":\""));
  Serial.print((activeLever == &rLever) ? F("RH") : F("LH"));
  Serial.println(F("\"}"));

  reportDeviceConfig(F("CUE"), cue.Armed(), CUE_FREQUENCY, CUE_DURATION);
  reportDeviceConfig(F("CUE2"), cue2.Armed(), cue2.Frequency(), cue2.Duration());
  reportDeviceConfig(F("PUMP"), pump.Armed(), PUMP_DURATION);
  reportDeviceConfig(F("PUMP2"), pump2.Armed(), pump2.Duration());
  reportDeviceConfig(F("LASER"), laser.Armed(), LASER_FREQUENCY, LASER_DURATION);
  reportDeviceConfig(F("LICK"), lickCircuit.Armed());
  reportDeviceConfig(F("MICROSCOPE"), microscope.Armed());
  reportDeviceConfig(F("SLM"), slm.Armed(), slm.LaserFrequency(), slm.LaserDuration());
  reportDeviceLever(F("LEVER_RH"), rLever.Armed(), rLever.IsReinforced());
  reportDeviceLever(F("LEVER_LH"), lLever.Armed(), lLever.IsReinforced());

  Serial.print(F("{\"level\":\"000\",\"omission_interval\":"));
  Serial.print(OMISSION_INTERVAL);
  Serial.println('}');
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
            uint32_t d = (uint32_t)inputJson["delay"]; if (d > 600000) d = 600000;
            if      (command == Cmd::LASER_SET_ONSET_DELAY) { laser.SetOnsetDelay(d); if (LASER_RH_ONLY_MODE) ReconfigureChain(); }
            else if (command == Cmd::CUE_SET_ONSET_DELAY)   { CUE_ONSET_DELAY = d;   ReconfigureChain(); }
            else if (command == Cmd::CUE2_SET_ONSET_DELAY)  { /* cue2 uses CUE_ONSET_DELAY; no separate CUE2_ONSET_DELAY shadow yet */ }
            else if (command == Cmd::PUMP_SET_ONSET_DELAY)  { PUMP_ONSET_DELAY = d;  ReconfigureChain(); }
            else                                             { PUMP2_ONSET_DELAY = d; ReconfigureChain(); }
            break;
          }
          case Cmd::LASER_TRIGGER_RH_ONLY: LASER_RH_ONLY_MODE = true; ReconfigureChain(); break;
          // RH lever commands
          case Cmd::LEVER_RH_ARM:          rLever.ArmToggle(true); break;
          case Cmd::LEVER_RH_DISARM:       rLever.ArmToggle(false); break;
          case Cmd::LEVER_RH_SET_ACTIVE:
            rLever.SetActiveLever(true); activeLever = &rLever;
            logParamChange(F("LEVER_RH"), F("reinforced"), true); break;
          case Cmd::LEVER_RH_SET_INACTIVE:
            rLever.SetActiveLever(false);
            logParamChange(F("LEVER_RH"), F("reinforced"), false); break;

          // LH lever commands
          case Cmd::LEVER_LH_ARM:          lLever.ArmToggle(true); break;
          case Cmd::LEVER_LH_DISARM:       lLever.ArmToggle(false); break;
          case Cmd::LEVER_LH_SET_ACTIVE:
            lLever.SetActiveLever(true); activeLever = &lLever;
            logParamChange(F("LEVER_LH"), F("reinforced"), true); break;
          case Cmd::LEVER_LH_SET_INACTIVE:
            lLever.SetActiveLever(false);
            logParamChange(F("LEVER_LH"), F("reinforced"), false); break;

          // Session setup commands
          case Cmd::SET_ACTIVE_PUMP: {
            bool usePump2 = inputJson["pump2"] | false;
            activePump = usePump2 ? &pump2 : &pump;
            activePumpTarget = usePump2 ? DeviceType::PUMP_2 : DeviceType::PUMP;
            ReconfigureChain();
            logParamChange(F("CONTROLLER"), F("active_pump"), usePump2 ? F("PUMP2") : F("PUMP"));
            break;
          }
          case Cmd::SET_OMISSION_INTERVAL:
            OMISSION_INTERVAL = inputJson["interval"]; ReconfigureChain();
            logParamChange(F("CONTROLLER"), F("omission_interval"), OMISSION_INTERVAL); break;

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
            else if (command == Cmd::CUE2_SET_LEVER_FILTER)  CUE2_SOURCE_FILTER = srcFilter;
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
            // Pass the request through unclamped so Slm::SetPin's PCINT0-group
            // guard can reject it with a level-006 error instead of silently
            // substituting a different pin. Bound only to the int8_t range for
            // representability: 0 and 127 are not PCINT0 pins, so an
            // out-of-range request can never land on a valid one.
            int req = inputJson["pin"] | 11;
            if (req < 0)   req = 0;
            if (req > 127) req = 127;
            slm.SetPin((int8_t)req); break;
          }
          case Cmd::SLM_SET_LASER_FREQUENCY:
            slm.SetLaserFrequency((uint32_t)inputJson["frequency"]);
            logParamChange(F("SLM"), F("frequency"), slm.LaserFrequency()); break;
          case Cmd::SLM_SET_LASER_DURATION:
            slm.SetLaserDuration((uint32_t)inputJson["duration"]);
            logParamChange(F("SLM"), F("duration"), slm.LaserDuration()); break;

          default:
            Serial.println(F("{\"level\":\"006\",\"desc\":\"Command not found\"}"));
            break;
        }
      }
    }
  }
}
