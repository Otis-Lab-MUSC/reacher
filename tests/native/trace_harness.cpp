// Runs a real sketch on the host against tests/native/arduino_stub, driven by a
// session script, and prints everything the sketch wrote to serial.
//
// Built by tests/test_golden_traces.py with -DSKETCH='"<path>/<sketch>.ino"'.
//
// Usage: trace_harness <script-file>
//
// Script lines (times are ms of session time, from 1; 0 is the loop() that ran
// SESSION_START):
//   pre <json>              sent before the session starts, one per tick
//   cmd <t> <json>          sent at session time t
//   press <RH|LH> <t> <ms>  the lever closes at t and opens ms later
//   lick <t> <ms>           the lick circuit closes at t and opens ms later
//   stop <t>                SESSION_END at t; outputs are reported 5 ms and
//                           3000 ms later
//   stop_probe <t>          report the outputs at t, as "before_stop"
//
// Output: the sketch's serial bytes, then one "#check <label> cue=.. cue2=..
// pump=.. pump2=.. laser=.. aux=.." line per report, in time order.

#include "Arduino.h"
#include SKETCH

#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Input { int pin; uint32_t from, to; };
struct Timed { uint32_t at; std::string json; };

std::vector<std::string> pre;
std::vector<Timed> commands;
std::vector<Input> inputs;
std::vector<uint32_t> stops;
std::vector<uint32_t> probes;
std::string checks;

void send(const std::string& line) {
  Serial.rx += line;
  Serial.rx += "\n";
}

void tick() {
  loop();
  g_millis++;
}

int level(int pin) { return pin >= 0 && pin < NUM_PINS && g_outLevel[pin] == HIGH ? 1 : 0; }

void report(const char* label, uint32_t t) {
#if defined(HAS_AUX)  // the Mega fr sketch is the only one with an Aux
  int auxOn = level(aux.Pin());
#else
  int auxOn = 0;
#endif
  char buf[160];
  snprintf(buf, sizeof buf, "#check %s@%lu cue=%d cue2=%d pump=%d pump2=%d laser=%d aux=%d\n", label,
           (unsigned long)t, g_toneFreq[PIN_CUE] > 0 ? 1 : 0, g_toneFreq[PIN_CUE_2] > 0 ? 1 : 0,
           level(PIN_PUMP), level(PIN_PUMP_2), level(PIN_LASER), auxOn);
  checks += buf;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    fprintf(stderr, "usage: %s <script>\n", argv[0]);
    return 2;
  }
  std::ifstream in(argv[1]);
  std::string line;
  uint32_t last = 0;
  while (std::getline(in, line)) {
    std::istringstream ss(line);
    std::string kind;
    ss >> kind;
    if (kind == "pre") {
      std::string json;
      std::getline(ss >> std::ws, json);
      pre.push_back(json);
    } else if (kind == "cmd") {
      Timed c;
      ss >> c.at;
      std::getline(ss >> std::ws, c.json);
      commands.push_back(c);
    } else if (kind == "press" || kind == "lick") {
      std::string side;
      if (kind == "press") ss >> side;
      uint32_t at, ms;
      ss >> at >> ms;
      int pin = kind == "lick" ? PIN_LICK_CIRCUIT : side == "LH" ? PIN_LEVER_LH : PIN_LEVER_RH;
      inputs.push_back({pin, at, at + ms});
      if (at + ms > last) last = at + ms;
    } else if (kind == "stop_probe") {
      uint32_t at;
      ss >> at;
      probes.push_back(at);
    } else if (kind == "stop") {
      uint32_t at;
      ss >> at;
      stops.push_back(at);
      if (at > last) last = at;
    }
  }

  setup();
  for (const std::string& c : pre) {
    send(c);
    tick();
  }
  // SESSION_START runs on this loop(); its millis() is session time 0.
  send("{\"cmd\":101}");
  const uint32_t t0 = g_millis;
  loop();

  for (uint32_t t = 1; t <= last + 3000; t++) {
    g_millis = t0 + t;
    for (const Timed& c : commands)
      if (c.at == t) send(c.json);
    for (uint32_t s : stops)
      if (s == t) send("{\"cmd\":100}");
    for (const Input& i : inputs) {
      if (t == i.from) g_inputPressed[i.pin] = 1;
      if (t == i.to) g_inputPressed[i.pin] = 0;
    }
    loop();
    for (uint32_t p : probes)
      if (t == p) report("before_stop", t);
    for (uint32_t s : stops) {
      if (t == s + 5) report("after_5ms", t);
      if (t == s + 3000) report("after_3s", t);
    }
  }

  fputs(Serial.tx.c_str(), stdout);
  fputs(checks.c_str(), stdout);
  return 0;
}
