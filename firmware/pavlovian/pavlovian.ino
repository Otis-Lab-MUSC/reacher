/**
 * @file pavlovian.ino
 * @brief Main sketch for REACHER v2.0.0 Pavlovian (classical conditioning) controller.
 *
 * @mainpage REACHER v2.0.0 Pavlovian Firmware
 *
 * Classical conditioning controller firmware for Arduino Uno (ATmega328P, 2KB RAM, 32KB Flash).
 *
 * **Baud rate:** 115200
 *
 * **Paradigm:** Pavlovian (timer-driven CS+/CS- trials with ITI, cue, trace, and reward phases).
 *
 * **Architecture:** The PavlovianScheduler drives a trial-based state machine
 * (ITI -> CUE -> TRACE -> REWARD). Lever presses are logged but do not affect
 * trial progression. Two cues (CS+/CS-), two pumps, and an optional laser are used.
 * Laser can be assigned to specific trial types (CS+, CS-, or both) and firing phases
 * (CUE or REWARD).
 *
 * **JSON protocol levels:**
 * - 000 — Settings / configuration dump
 * - 001 — Arm / disarm state changes
 * - 006 — Error messages
 * - 007 — Behavioral events (presses, licks, device activations, trial events)
 * - 008 — Microscope frame timestamps
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
#ifdef __AVR_ATmega2560__
#include <Slm.h>
#endif
#include <ReacherHelpers.h>
#include "PavlovianScheduler.h"

// Default parameter values
static constexpr uint32_t DEFAULT_CUE_FREQUENCY = 8000;
static constexpr uint32_t DEFAULT_CUE_DURATION  = 2000;
static constexpr uint32_t DEFAULT_PUMP_DURATION  = 2000;

// Pavlovian paradigm parameters
uint8_t  PAV_CS_PLUS_COUNT   = 50;
uint8_t  PAV_CS_MINUS_COUNT  = 50;
uint32_t PAV_CS_PLUS_FREQ    = 12000;
uint32_t PAV_CS_MINUS_FREQ   = 3000;
uint8_t  PAV_CS_PLUS_PROB    = 100;
uint8_t  PAV_CS_MINUS_PROB   = 0;
bool     PAV_COUNTERBALANCE  = false;
uint16_t PAV_CUE_DURATION    = 2000;
uint16_t PAV_TRACE_INTERVAL  = 1000;
uint16_t PAV_CONSUMPTION_MS  = 3000;
uint32_t PAV_ITI_MEAN        = 30000;
uint32_t PAV_ITI_MIN         = 10000;
uint32_t PAV_ITI_MAX         = 90000;

// Device instances
SwitchLever rLever(PIN_LEVER_RH, "RH", DeviceType::LEVER_RH);
SwitchLever lLever(PIN_LEVER_LH, "LH", DeviceType::LEVER_LH);

Cue         cue(PIN_CUE, DEFAULT_CUE_FREQUENCY, DEFAULT_CUE_DURATION);
Cue         cue2(PIN_CUE_2, DEFAULT_CUE_FREQUENCY, DEFAULT_CUE_DURATION);
Pump        pump(PIN_PUMP, DEFAULT_PUMP_DURATION);
Pump        pump2(PIN_PUMP_2, DEFAULT_PUMP_DURATION);
LickCircuit lickCircuit(PIN_LICK_CIRCUIT);
Laser       laser(PIN_LASER, 40, 5000);
Microscope  microscope(PIN_MICROSCOPE_TRIG, PIN_MICROSCOPE_TS);
#ifdef __AVR_ATmega2560__
Slm         slm(PIN_SLM_TS);
#endif

// Laser shadow variables
uint8_t  LASER_FREQUENCY = 40;
uint32_t LASER_DURATION  = 5000;

// Cue pulse shadow variables (CS+ = cue, CS- = cue2). pulse_on == 0 means
// continuous (pulsing disabled). 374/375 set CS+, 384/385 set CS-; each
// command carries only its own key, so we retain the companion value here.
uint16_t CUE_PULSE_ON_MS   = 0;    // CS+ continuous by default
uint16_t CUE_PULSE_OFF_MS  = 0;
uint16_t CUE2_PULSE_ON_MS  = 200;  // CS- pulsed 200/200 by default (matches PAV_PULSE_CONFIG)
uint16_t CUE2_PULSE_OFF_MS = 200;

/// Central scheduler instance.
PavlovianScheduler scheduler;

#ifdef __AVR_ATmega2560__
DeviceSet devices = { &rLever, &lLever, &cue, &cue2, &pump, &pump2, &lickCircuit, &laser, &microscope, &slm };
#else
DeviceSet devices = { &rLever, &lLever, &cue, &cue2, &pump, &pump2, &lickCircuit, &laser, &microscope, nullptr };
#endif

// Session timestamps
uint32_t SESSION_START_TIMESTAMP;
uint32_t SESSION_END_TIMESTAMP;


// Forward declarations
void ParseCommands();
void StartSession();
void EndSession();
void ReconfigureScheduler();
void SendIdentification();

#ifdef __AVR_ATmega2560__
ISR(PCINT0_vect) { Slm::instance->HandlePCINT(); }
#endif

/// @brief Callback: forward lever press to scheduler.
void onLeverPress(DeviceType source, uint32_t timestamp) {
  scheduler.OnInputEvent(source, timestamp);
}

/// @brief Callback: forward lever release to scheduler.
void onLeverRelease(DeviceType source) {
  scheduler.OnInputRelease(source);
}

/// @brief Send SCPI-style identification JSON (reusable for boot, *IDN?, and Cmd::IDENTIFY).
void SendIdentification() {
  Serial.println(F("{\"level\":\"000\",\"device\":\"CONTROLLER\",\"sketch\":\"pavlovian.ino\",\"version\":\"v3.0.1\",\"baud_rate\":115200,\"schedule\":\"PAVLOVIAN\"}"));
}

/// @brief Arduino setup — initialize serial, register devices, configure Pavlovian defaults.
void setup() {
  delay(100);
  Serial.begin(115200);
  Serial.setTimeout(100);
  delay(100);
  while (Serial.available()) Serial.read();  // Drain bootloader residue

  cue.Jingle();

  // Register devices with scheduler
  scheduler.RegisterLever(&rLever, DeviceType::LEVER_RH);
  scheduler.RegisterLever(&lLever, DeviceType::LEVER_LH);
  scheduler.RegisterLickCircuit(&lickCircuit);
  scheduler.RegisterCue(&cue);
  scheduler.RegisterCue2(&cue2);
  scheduler.RegisterPump(&pump);
  scheduler.RegisterPump2(&pump2);
  scheduler.RegisterLaser(&laser);
  scheduler.RegisterMicroscope(&microscope);

  // Set input callbacks
  rLever.SetCallback(onLeverPress);
  rLever.SetReleaseCallback(onLeverRelease);
  lLever.SetCallback(onLeverPress);
  lLever.SetReleaseCallback(onLeverRelease);

  // Both levers are reinforced (presses logged as ACTIVE)
  rLever.SetActiveLever(true);
  lLever.SetActiveLever(true);

  // Configure default Pavlovian parameters
  ReconfigureScheduler();

  // Send setup JSON
  SendIdentification();
  wdt_enable(WDTO_8S);
}

/// @brief Arduino main loop — poll inputs, tick scheduler, handle serial commands.
void loop() {
  wdt_reset();
  uint32_t currentTimestamp = millis();

  // Poll inputs
  rLever.Monitor(currentTimestamp);
  lLever.Monitor(currentTimestamp);
  lickCircuit.Monitor(currentTimestamp);

  // Scheduler: tick trial state machine and output devices
  scheduler.Update(currentTimestamp);

  // Auto-end when all Pavlovian trials are complete
  if (scheduler.IsComplete() && scheduler.IsSessionActive()) {
    EndSession();
    armToggleDevices(devices, false);
#ifdef __AVR_ATmega2560__
    slm.ArmToggle(false);
#endif
  }

  // Microscope frame handling
  microscope.HandleFrameSignal();
  microscope.TickTrigger(currentTimestamp);  // Fix: FW-001
#ifdef __AVR_ATmega2560__
  slm.HandleTimestampSignal();
#endif

  // Process serial commands
  ParseCommands();
}

/// @brief Deserialize JSON serial input and dispatch to device/session handlers.
void ParseCommands() {
  if (Serial.available() > 0) {
    JsonDocument inputJson;
    char buf[128];
    int len = Serial.readBytesUntil('\n', buf, sizeof(buf) - 1);
    if (len == (int)(sizeof(buf) - 1)) {
      while (Serial.available() && Serial.read() != '\n') {}
    }
    buf[len] = '\0';

    // SCPI-style identification query (not valid JSON, must check before deserialization)
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
        // Update Pavlovian shadow variables and reconfigure scheduler for cue changes
        switch (command) {
          case Cmd::CUE_SET_FREQUENCY:  PAV_CS_PLUS_FREQ = inputJson["frequency"]; ReconfigureScheduler(); break;
          case Cmd::CUE_SET_DURATION:   PAV_CUE_DURATION = inputJson["duration"]; ReconfigureScheduler(); break;
          case Cmd::CUE2_SET_FREQUENCY: PAV_CS_MINUS_FREQ = inputJson["frequency"]; ReconfigureScheduler(); break;
          case Cmd::CUE2_SET_DURATION:  ReconfigureScheduler(); break;
          case Cmd::LASER_SET_FREQUENCY: LASER_FREQUENCY = inputJson["frequency"]; break;
          case Cmd::LASER_SET_DURATION:  LASER_DURATION = inputJson["duration"]; break;
        }
      } else {
        switch (command) {
          // RH lever commands
          case Cmd::LEVER_RH_ARM:    rLever.ArmToggle(true); break;
          case Cmd::LEVER_RH_DISARM: rLever.ArmToggle(false); break;

          // LH lever commands
          case Cmd::LEVER_LH_ARM:    lLever.ArmToggle(true); break;
          case Cmd::LEVER_LH_DISARM: lLever.ArmToggle(false); break;

          // Pavlovian paradigm commands
          case Cmd::PAV_CS_PLUS_PROB:
            PAV_CS_PLUS_PROB = inputJson["probability"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("cs_plus_prob"), (uint32_t)PAV_CS_PLUS_PROB); break;
          case Cmd::PAV_CS_MINUS_PROB:
            PAV_CS_MINUS_PROB = inputJson["probability"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("cs_minus_prob"), (uint32_t)PAV_CS_MINUS_PROB); break;
          case Cmd::PAV_CS_PLUS_COUNT:
            if (scheduler.IsSessionActive()) {
              Serial.println(F("{\"level\":\"006\",\"device\":\"CONTROLLER\",\"desc\":\"Cannot change CS+ count during active session\"}"));
            } else {
              PAV_CS_PLUS_COUNT = inputJson["count"]; ReconfigureScheduler();
              logParamChange(F("CONTROLLER"), F("cs_plus_count"), (uint32_t)PAV_CS_PLUS_COUNT);
            }
            break;
          case Cmd::PAV_CS_MINUS_COUNT:
            if (scheduler.IsSessionActive()) {
              Serial.println(F("{\"level\":\"006\",\"device\":\"CONTROLLER\",\"desc\":\"Cannot change CS- count during active session\"}"));
            } else {
              PAV_CS_MINUS_COUNT = inputJson["count"]; ReconfigureScheduler();
              logParamChange(F("CONTROLLER"), F("cs_minus_count"), (uint32_t)PAV_CS_MINUS_COUNT);
            }
            break;
          case Cmd::PAV_CS_PLUS_FREQ:
            PAV_CS_PLUS_FREQ = inputJson["frequency"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("cs_plus_freq"), PAV_CS_PLUS_FREQ); break;
          case Cmd::PAV_CS_MINUS_FREQ:
            PAV_CS_MINUS_FREQ = inputJson["frequency"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("cs_minus_freq"), PAV_CS_MINUS_FREQ); break;
          case Cmd::PAV_COUNTERBALANCE:
            if (scheduler.IsSessionActive()) {
              Serial.println(F("{\"level\":\"006\",\"device\":\"CONTROLLER\",\"desc\":\"Cannot change counterbalance during active session\"}"));
            } else {
              PAV_COUNTERBALANCE = inputJson["counterbalance"]; ReconfigureScheduler();
              logParamChange(F("CONTROLLER"), F("counterbalance"), PAV_COUNTERBALANCE);
            }
            break;
          case Cmd::PAV_CUE_DURATION:
            PAV_CUE_DURATION = inputJson["duration"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("cue_duration"), (uint32_t)PAV_CUE_DURATION); break;
          case Cmd::PAV_TRACE_INTERVAL:
            PAV_TRACE_INTERVAL = inputJson["interval"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("trace_interval"), (uint32_t)PAV_TRACE_INTERVAL); break;
          case Cmd::PAV_CONSUMPTION:
            PAV_CONSUMPTION_MS = inputJson["duration"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("consumption"), (uint32_t)PAV_CONSUMPTION_MS); break;
          case Cmd::PAV_ITI_MEAN:
            PAV_ITI_MEAN = inputJson["iti_mean"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("iti_mean"), PAV_ITI_MEAN); break;
          case Cmd::PAV_ITI_MIN:
            PAV_ITI_MIN = inputJson["iti_min"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("iti_min"), PAV_ITI_MIN); break;
          case Cmd::PAV_ITI_MAX:
            PAV_ITI_MAX = inputJson["iti_max"]; ReconfigureScheduler();
            logParamChange(F("CONTROLLER"), F("iti_max"), PAV_ITI_MAX); break;
          case Cmd::PAV_PULSE_CONFIG: {
            uint16_t onMs = inputJson["pulse_on"] | (uint16_t)200;
            uint16_t offMs = inputJson["pulse_off"] | (uint16_t)200;
            cue2.SetPulsed(true, onMs, offMs);
            logParamChange(F("CUE2"), F("pulse_on"), (uint32_t)onMs);
            logParamChange(F("CUE2"), F("pulse_off"), (uint32_t)offMs);
            break;
          }

          // Per-cue, per-direction pulse control (supersedes the CS--only 219
          // for fine control). Each command carries one key; the companion
          // value is held in the shadow globals. pulse_on == 0 => continuous.
          case Cmd::CUE_SET_PULSE_ON: {
            CUE_PULSE_ON_MS = inputJson["pulse_on"] | (uint16_t)0;
            cue.SetPulsed(CUE_PULSE_ON_MS > 0, CUE_PULSE_ON_MS, CUE_PULSE_OFF_MS);
            logParamChange(F("CUE"), F("pulse_on"), (uint32_t)CUE_PULSE_ON_MS); break;
          }
          case Cmd::CUE_SET_PULSE_OFF: {
            CUE_PULSE_OFF_MS = inputJson["pulse_off"] | (uint16_t)0;
            cue.SetPulsed(CUE_PULSE_ON_MS > 0, CUE_PULSE_ON_MS, CUE_PULSE_OFF_MS);
            logParamChange(F("CUE"), F("pulse_off"), (uint32_t)CUE_PULSE_OFF_MS); break;
          }
          case Cmd::CUE2_SET_PULSE_ON: {
            CUE2_PULSE_ON_MS = inputJson["pulse_on"] | (uint16_t)0;
            cue2.SetPulsed(CUE2_PULSE_ON_MS > 0, CUE2_PULSE_ON_MS, CUE2_PULSE_OFF_MS);
            logParamChange(F("CUE2"), F("pulse_on"), (uint32_t)CUE2_PULSE_ON_MS); break;
          }
          case Cmd::CUE2_SET_PULSE_OFF: {
            CUE2_PULSE_OFF_MS = inputJson["pulse_off"] | (uint16_t)0;
            cue2.SetPulsed(CUE2_PULSE_ON_MS > 0, CUE2_PULSE_ON_MS, CUE2_PULSE_OFF_MS);
            logParamChange(F("CUE2"), F("pulse_off"), (uint32_t)CUE2_PULSE_OFF_MS); break;
          }

          // Pavlovian laser commands
          case Cmd::PAV_LASER_CS_PLUS:
            scheduler.SetLaserTrialFilter(LaserTrialFilter::CS_PLUS);
            logParamChange(F("LASER"), F("trial_filter"), F("CS_PLUS")); break;
          case Cmd::PAV_LASER_CS_MINUS:
            scheduler.SetLaserTrialFilter(LaserTrialFilter::CS_MINUS);
            logParamChange(F("LASER"), F("trial_filter"), F("CS_MINUS")); break;
          case Cmd::PAV_LASER_CS_BOTH:
            scheduler.SetLaserTrialFilter(LaserTrialFilter::CS_BOTH);
            logParamChange(F("LASER"), F("trial_filter"), F("CS_BOTH")); break;
          case Cmd::PAV_LASER_PHASE_REWARD:
            scheduler.SetLaserPhase(LaserPhase::REWARD);
            logParamChange(F("LASER"), F("laser_phase"), F("REWARD")); break;
          case Cmd::PAV_LASER_PHASE_CUE:
            scheduler.SetLaserPhase(LaserPhase::CUE);
            logParamChange(F("LASER"), F("laser_phase"), F("CUE")); break;
          case Cmd::LASER_SET_ONSET_DELAY: {
            uint32_t d = (uint32_t)inputJson["delay"]; if (d > 600000) d = 600000;
            laser.SetOnsetDelay(d);
            logParamChange(F("LASER"), F("onset_delay"), d); break;
          }

          // Controller commands
          case Cmd::SESSION_START:
            StartSession(); setDeviceTimestampOffset(devices, SESSION_START_TIMESTAMP);
#ifdef __AVR_ATmega2560__
            slm.SetOffset(SESSION_START_TIMESTAMP);
#endif
            break;
          case Cmd::SESSION_END:
            EndSession(); armToggleDevices(devices, false);
#ifdef __AVR_ATmega2560__
            slm.ArmToggle(false);
#endif
            break;
          case Cmd::IDENTIFY:
            if (!scheduler.IsSessionActive()) {
              armToggleDevices(devices, false);
#ifdef __AVR_ATmega2560__
              slm.ArmToggle(false);
#endif
            }
            SendIdentification(); break;
          case Cmd::SESSION_PAUSE: {
            bool paused = inputJson["paused"] | false;
            uint32_t now = millis();
            scheduler.SetPaused(paused, now);
            if (paused) microscope.Pause(now);
            else        microscope.Resume(now);
            logParamChange(F("CONTROLLER"), F("session_paused"), paused);
            break;
          }

#ifdef __AVR_ATmega2560__
          case Cmd::SLM_ARM:    slm.ArmToggle(true); break;
          case Cmd::SLM_DISARM: slm.ArmToggle(false); break;
          case Cmd::SLM_SET_PIN: {
            uint8_t p = (uint8_t)constrain((int)(inputJson["pin"] | 11), 8, 13);
            slm.SetPin((int8_t)p); break;
          }
#endif

          default:
            Serial.println(F("{\"level\":\"006\",\"desc\":\"Command not found\"}"));
            break;
        }
      }
    }
  }
}

/// @brief Push current parameter globals into the scheduler.
void ReconfigureScheduler() {
  scheduler.Configure(
    PAV_CS_PLUS_COUNT, PAV_CS_MINUS_COUNT,
    PAV_CS_PLUS_FREQ, PAV_CS_MINUS_FREQ,
    PAV_CUE_DURATION, PAV_TRACE_INTERVAL, PAV_CONSUMPTION_MS,
    PAV_ITI_MEAN, PAV_ITI_MIN, PAV_ITI_MAX,
    PAV_CS_PLUS_PROB, PAV_CS_MINUS_PROB,
    PAV_COUNTERBALANCE);
}

/// @brief Begin a session: trigger microscope, initialize scheduler, emit settings JSON.
void StartSession() {
  SESSION_START_TIMESTAMP = millis();
  microscope.Trigger();
  scheduler.StartSession(SESSION_START_TIMESTAMP);

  Serial.println(F("{\"level\":\"007\",\"device\":\"CONTROLLER\",\"event\":\"START\",\"timestamp\":0}"));

  // Send Pavlovian settings
  Serial.print(F("{\"level\":\"000\",\"device\":\"CONTROLLER\",\"paradigm\":\"PAVLOVIAN\",\"cs_plus_count\":"));
  Serial.print(PAV_CS_PLUS_COUNT);
  Serial.print(F(",\"cs_minus_count\":"));
  Serial.print(PAV_CS_MINUS_COUNT);
  Serial.print(F(",\"cs_plus_prob\":"));
  Serial.print(PAV_CS_PLUS_PROB);
  Serial.print(F(",\"cs_minus_prob\":"));
  Serial.print(PAV_CS_MINUS_PROB);
  Serial.print(F(",\"counterbalance\":"));
  Serial.print(PAV_COUNTERBALANCE ? F("true") : F("false"));
  Serial.println('}');

  Serial.print(F("{\"level\":\"000\",\"cs_plus_freq\":"));
  Serial.print(PAV_CS_PLUS_FREQ);
  Serial.print(F(",\"cs_minus_freq\":"));
  Serial.print(PAV_CS_MINUS_FREQ);
  Serial.print(F(",\"cue_duration\":"));
  Serial.print(PAV_CUE_DURATION);
  Serial.print(F(",\"trace_interval\":"));
  Serial.print(PAV_TRACE_INTERVAL);
  Serial.print(F(",\"consumption_ms\":"));
  Serial.print(PAV_CONSUMPTION_MS);
  Serial.print(F(",\"iti_mean\":"));
  Serial.print(PAV_ITI_MEAN);
  Serial.print(F(",\"iti_min\":"));
  Serial.print(PAV_ITI_MIN);
  Serial.print(F(",\"iti_max\":"));
  Serial.print(PAV_ITI_MAX);
  Serial.println('}');

  // Per-device config with armed status
  reportDeviceConfig(F("CUE"), cue.Armed(), PAV_CS_PLUS_FREQ, (uint32_t)PAV_CUE_DURATION);
  reportDeviceConfig(F("CUE2"), cue2.Armed(), PAV_CS_MINUS_FREQ, cue2.Duration());
  reportDeviceConfig(F("PUMP"), pump.Armed(), pump.Duration());
  reportDeviceConfig(F("PUMP2"), pump2.Armed(), pump2.Duration());
  reportDeviceConfig(F("LASER"), laser.Armed(), LASER_FREQUENCY, LASER_DURATION);
  reportDeviceConfig(F("LICK"), lickCircuit.Armed());
  reportDeviceConfig(F("MICROSCOPE"), microscope.Armed());
#ifdef __AVR_ATmega2560__
  reportDeviceConfig(F("SLM"), slm.Armed());
#endif
  reportDeviceLever(F("LEVER_RH"), rLever.Armed(), rLever.IsReinforced());
  reportDeviceLever(F("LEVER_LH"), lLever.Armed(), lLever.IsReinforced());
}

/// @brief End a session: trigger microscope, shut down scheduler, emit end event.
void EndSession() {
  if (!scheduler.IsSessionActive()) return;  // Already ended
  SESSION_END_TIMESTAMP = millis();
  microscope.Pause(SESSION_END_TIMESTAMP);
  scheduler.EndSession(SESSION_END_TIMESTAMP);
  digitalWrite(PIN_LASER, LOW);

  Serial.print(F("{\"level\":\"007\",\"device\":\"CONTROLLER\",\"event\":\"END\",\"timestamp\":"));
  Serial.print(SESSION_END_TIMESTAMP - SESSION_START_TIMESTAMP);
  Serial.println('}');
}
