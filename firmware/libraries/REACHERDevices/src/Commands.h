#ifndef COMMANDS_H
#define COMMANDS_H

// REACHER Serial Command Codes
//
// Encoding: [Device Prefix][Action Suffix]
//   Prefix:  1xx=Controller  2xx=Session  3xx=Cue  4xx=Pump
//            5xx=Lick  6xx=Laser  9xx=Microscope
//            10xx=RH Lever  11xx=SLM  13xx=LH Lever
//   Suffix:  x00=disarm  x01=arm  x03=test
//            x71=set frequency  x72=set duration
//            x74=set timeout  x75=set ratio  x76=set pin
//            x80=set inactive/mode B  x81=set active/mode A  x82=set mode B
//
// Output JSON levels (firmware -> host):
//   "000" — Configuration / settings dump
//   "001" — Arm / disarm state changes
//   "006" — Error messages
//   "007" — Behavioral events
//   "008" — Microscope frame timestamps
//   "009" — SLM timestamps

namespace Cmd {
  // --- Controller (1xx) ---
  constexpr int SESSION_END          = 100;
  constexpr int SESSION_START        = 101;
  constexpr int IDENTIFY             = 102;
  constexpr int TEST_CHAIN           = 103;
  constexpr int TEST_MODE            = 104;
  constexpr int SESSION_PAUSE        = 105;  // Pause/resume active session

  // --- Session Setup (2xx) ---
  constexpr int SET_RATIO            = 201;
  constexpr int SET_PARADIGM         = 202;
  constexpr int SET_OMISSION_INTERVAL = 203;
  constexpr int SET_VI_INTERVAL      = 204;
  constexpr int SET_PR_STEP          = 205;
  constexpr int SET_TRACE_INTERVAL   = 220;
  constexpr int SET_ACTIVE_PUMP      = 221;

  // Pavlovian parameters (206-219)
  constexpr int PAV_CS_PLUS_PROB     = 206;
  constexpr int PAV_CS_MINUS_PROB    = 207;
  constexpr int PAV_CS_PLUS_COUNT    = 208;
  constexpr int PAV_CS_MINUS_COUNT   = 209;
  constexpr int PAV_CS_PLUS_FREQ     = 210;
  constexpr int PAV_CS_MINUS_FREQ    = 211;
  constexpr int PAV_COUNTERBALANCE   = 212;
  constexpr int PAV_CUE_DURATION     = 213;
  constexpr int PAV_TRACE_INTERVAL   = 214;
  constexpr int PAV_CONSUMPTION      = 215;
  constexpr int PAV_ITI_MEAN         = 216;
  constexpr int PAV_ITI_MIN          = 217;
  constexpr int PAV_ITI_MAX          = 218;
  constexpr int PAV_PULSE_CONFIG     = 219;

  // --- Cue (3xx) ---
  constexpr int CUE_DISARM           = 300;
  constexpr int CUE_ARM              = 301;
  constexpr int CUE2_DISARM          = 310;
  constexpr int CUE_TEST             = 303;
  constexpr int CUE2_ARM             = 311;
  constexpr int CUE2_TEST            = 313;
  constexpr int CUE_SET_FREQUENCY    = 371;
  constexpr int CUE_SET_DURATION     = 372;
  constexpr int CUE_SET_TRACE        = 373;  // deprecated
  constexpr int CUE_SET_PIN          = 376;
  constexpr int CUE_SET_ONSET_DELAY  = 377;  // Cue onset delay from trigger (ms); operant paradigms
  constexpr int CUE_SET_LEVER_FILTER  = 378;  // Per-device lever routing (0=any, 1=RH_only, 2=LH_only)
  constexpr int CUE2_SET_FREQUENCY   = 381;
  constexpr int CUE2_SET_DURATION    = 382;
  constexpr int CUE2_SET_ONSET_DELAY  = 387;  // Cue2 onset delay (stored; cue2 absent from operant chains)
  constexpr int CUE2_SET_LEVER_FILTER = 388;
  constexpr int CUE2_SET_PIN         = 386;

  // --- Pump (4xx) ---
  constexpr int PUMP_DISARM          = 400;
  constexpr int PUMP_ARM             = 401;
  constexpr int PUMP2_DISARM         = 410;
  constexpr int PUMP_TEST            = 403;
  constexpr int PUMP2_ARM            = 411;
  constexpr int PUMP2_TEST           = 413;
  constexpr int PUMP_SET_DURATION    = 472;
  constexpr int PUMP_SET_TRACE       = 473;  // deprecated
  constexpr int PUMP_SET_PIN         = 476;
  constexpr int PUMP_SET_ONSET_DELAY  = 477;  // Pump onset delay from reward-window start (ms)
  constexpr int PUMP_SET_LEVER_FILTER  = 478;  // Per-device lever routing (0=any, 1=RH_only, 2=LH_only)
  constexpr int PUMP2_SET_DURATION   = 482;
  constexpr int PUMP2_SET_PIN        = 486;
  constexpr int PUMP2_SET_ONSET_DELAY  = 487;  // Pump2 onset delay
  constexpr int PUMP2_SET_LEVER_FILTER = 488;

  // --- Lick Circuit (5xx) ---
  constexpr int LICK_DISARM          = 500;
  constexpr int LICK_ARM             = 501;
  constexpr int LICK_SET_PIN         = 576;

  // --- Laser (6xx) ---
  constexpr int LASER_DISARM         = 600;
  constexpr int LASER_ARM            = 601;
  constexpr int LASER_TEST           = 603;
  constexpr int LASER_SET_FREQUENCY  = 671;
  constexpr int LASER_SET_DURATION   = 672;
  constexpr int LASER_SET_ONSET_DELAY = 673;  // Delay (ms) from RH press to laser onset in RH-only mode
  constexpr int LASER_SET_PIN        = 676;
  constexpr int LASER_MODE_CONTINGENT  = 681;
  constexpr int LASER_MODE_INDEPENDENT = 682;
  constexpr int LASER_TRIGGER_RH_ONLY  = 684;  // RH lever press fires laser only (no cue, no pump)

  // Pavlovian laser trial assignment
  constexpr int PAV_LASER_CS_PLUS      = 691;  // Fire on CS+ trials only
  constexpr int PAV_LASER_CS_MINUS     = 692;  // Fire on CS- trials only
  constexpr int PAV_LASER_CS_BOTH      = 693;  // Fire on both trial types
  // Pavlovian laser phase selection
  constexpr int PAV_LASER_PHASE_REWARD = 694;  // Fire during REWARD phase (default)
  constexpr int PAV_LASER_PHASE_CUE    = 695;  // Fire during CUE_ON phase

  // --- Microscope (9xx) ---
  constexpr int MICROSCOPE_DISARM    = 900;
  constexpr int MICROSCOPE_ARM       = 901;
  constexpr int MICROSCOPE_TEST      = 903;
  // Trigger pin only — timestamp pin (INT0) is fixed for cross-board portability.
  constexpr int MICROSCOPE_SET_TRIG_PIN = 976;

  // --- SLM (11xx) ---
  constexpr int SLM_DISARM           = 1100;
  constexpr int SLM_ARM              = 1101;
  // Timestamp pin is configurable within PCINT0 group (Arduino pins 8–13).
  constexpr int SLM_SET_PIN          = 1176;

  // --- RH Lever (10xx) ---
  constexpr int LEVER_RH_DISARM      = 1000;
  constexpr int LEVER_RH_ARM         = 1001;
  constexpr int LEVER_RH_SET_TIMEOUT = 1074;
  constexpr int LEVER_RH_SET_RATIO   = 1075;
  constexpr int LEVER_RH_SET_PIN     = 1076;
  constexpr int LEVER_RH_SET_INACTIVE = 1080;
  constexpr int LEVER_RH_SET_ACTIVE  = 1081;

  // --- LH Lever (13xx) ---
  constexpr int LEVER_LH_DISARM      = 1300;
  constexpr int LEVER_LH_ARM         = 1301;
  constexpr int LEVER_LH_SET_TIMEOUT = 1374;
  constexpr int LEVER_LH_SET_RATIO   = 1375;
  constexpr int LEVER_LH_SET_PIN     = 1376;
  constexpr int LEVER_LH_SET_INACTIVE = 1380;
  constexpr int LEVER_LH_SET_ACTIVE  = 1381;
}

#endif // COMMANDS_H
