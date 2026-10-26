// Runs a real sketch on the host against tests/native/arduino_stub and reports
// whether the cue, pump or laser is still driven after the host acts.
//
// Built by tests/test_end_session_outputs.py with -DSKETCH='"<path>/<sketch>.ino"'.
//
// Usage: harness <press_at_ms> <act_after_ms> <stop|disarm>
//   press_at_ms   session time before the RH lever goes down and stays down
//   act_after_ms  time after the press before the host acts
//   stop          SESSION_END (100), as stop_program() sends
//   disarm        CUE_DISARM (300) + PUMP_DISARM (400) mid-session
//
// Prints one line per checkpoint, "<label> cue=<0|1> pump=<0|1> laser=<0|1>",
// then "controller_end=<0|1>".

#include "Arduino.h"
#include SKETCH

#include <cstdio>
#include <cstring>

static void send(const char* line) {
  Serial.rx += line;
  Serial.rx += "\n";
}

static void run(uint32_t ms) {
  for (uint32_t i = 0; i < ms; i++) {
    loop();
    g_millis++;
  }
}

static void report(const char* label) {
  printf("%s cue=%d pump=%d laser=%d\n", label, g_toneFreq[PIN_CUE] > 0 ? 1 : 0,
         g_outLevel[PIN_PUMP] == HIGH ? 1 : 0, g_outLevel[PIN_LASER] == HIGH ? 1 : 0);
}

int main(int argc, char** argv) {
  if (argc != 4) {
    fprintf(stderr, "usage: %s <press_at_ms> <act_after_ms> <stop|disarm>\n", argv[0]);
    return 2;
  }
  uint32_t pressAt = (uint32_t)atoi(argv[1]);
  uint32_t actAfter = (uint32_t)atoi(argv[2]);
  bool disarm = strcmp(argv[3], "disarm") == 0;

  setup();
  // 1 s cue, pump and laser, every output armed, RH lever armed; then start.
  // One command per tick: ParseCommands reads one line per loop().
  const char* configure[] = {
      "{\"cmd\":371,\"frequency\":8000}", "{\"cmd\":372,\"duration\":1000}",
      "{\"cmd\":472,\"duration\":1000}",  "{\"cmd\":671,\"frequency\":1}",
      "{\"cmd\":672,\"duration\":1000}",  "{\"cmd\":1001}",
      "{\"cmd\":301}",                    "{\"cmd\":401}",
      "{\"cmd\":601}",                    "{\"cmd\":101}",
  };
  for (const char* c : configure) {
    send(c);
    run(1);
  }

  run(pressAt);
  g_inputPressed[PIN_LEVER_RH] = 1;  // the heavy presser: down, and held
  run(actAfter);
  report("before");

  if (disarm) {
    send("{\"cmd\":300}");
    send("{\"cmd\":400}");
  } else {
    send("{\"cmd\":100}");
  }
  run(5);
  report("after_5ms");
  run(3000);  // past the 1 s output window and the 200 ms SCOPE_DRAIN_MS
  report("after_3s");

  bool sawEnd = Serial.tx.find("\"event\":\"END\"") != std::string::npos;
  printf("controller_end=%d\n", sawEnd ? 1 : 0);
  return 0;
}
