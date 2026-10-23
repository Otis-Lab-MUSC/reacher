// Host stub of the Arduino core: enough of the API for REACHERDevices + the sketches.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <string>
#include <cstdio>

typedef uint8_t byte;
typedef bool boolean;
class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper*>(s))
#define PROGMEM
#define PSTR(s) (s)
inline uint8_t pgm_read_byte(const void* p) { return *(const uint8_t*)p; }
inline uint16_t pgm_read_word(const void* p) { return *(const uint16_t*)p; }
inline uint32_t pgm_read_dword(const void* p) { return *(const uint32_t*)p; }
inline float pgm_read_float(const void* p) { return *(const float*)p; }
inline const void* pgm_read_ptr(const void* p) { return *(const void* const*)p; }
#define strlen_P strlen
#define strncmp_P strncmp
#define strcmp_P strcmp
#define memcmp_P memcmp
#define memcpy_P memcpy

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define RISING 3
#define FALLING 2
#define CHANGE 1
#define A0 54
#define A1 55
#define NUM_PINS 70

// ---- simulated board state (defined in stub.cpp) ----
extern uint32_t g_millis;
extern uint8_t g_pinMode[NUM_PINS];
extern uint8_t g_outLevel[NUM_PINS];
extern uint8_t g_inputPressed[NUM_PINS];   // 1 = switch closed (reads LOW)
extern int32_t g_toneFreq[NUM_PINS];       // >0 while tone() is sounding on that pin
extern volatile uint8_t PCMSK0, PCICR;
#define PCIE0 0
extern volatile uint8_t g_portB;

inline uint32_t millis() { return g_millis; }
inline uint32_t micros() { return g_millis * 1000u; }
inline void delay(uint32_t ms) { g_millis += ms; }
inline void delayMicroseconds(uint32_t) {}
inline void pinMode(uint8_t p, uint8_t m) { if (p < NUM_PINS) g_pinMode[p] = m; }
inline void digitalWrite(uint8_t p, uint8_t v) { if (p < NUM_PINS) g_outLevel[p] = v ? 1 : 0; }
inline int digitalRead(uint8_t p) {
  if (p >= NUM_PINS) return HIGH;
  if (g_pinMode[p] == OUTPUT) return g_outLevel[p];
  return g_inputPressed[p] ? LOW : HIGH;   // pull-up idle HIGH
}
inline int analogRead(uint8_t) { return 512; }
// AVR tone(): one active tone at a time; noTone(pin) stops it.
inline void tone(uint8_t p, unsigned int f, unsigned long = 0) { for (int i = 0; i < NUM_PINS; i++) g_toneFreq[i] = 0; g_toneFreq[p] = (int32_t)f; }
inline void noTone(uint8_t p) { if (p < NUM_PINS) g_toneFreq[p] = 0; }
inline long random(long lo, long hi) { return hi > lo ? lo + (rand() % (hi - lo)) : lo; }
inline long random(long hi) { return hi > 0 ? rand() % hi : 0; }
inline void randomSeed(unsigned long s) { srand((unsigned)s); }
inline void noInterrupts() {}
inline void interrupts() {}
inline int digitalPinToInterrupt(int p) { return p; }
inline void attachInterrupt(int, void (*)(), int) {}
inline uint8_t digitalPinToPort(int) { return 2; }
inline uint8_t digitalPinToBitMask(int p) { return (uint8_t)(1u << (p & 7)); }
inline uint8_t digitalPinToPCMSKbit(int p) { return (uint8_t)(p & 7); }
inline volatile uint8_t* digitalPinToPCMSK(int p) { return (p>=10&&p<=13)||(p>=50&&p<=53) ? &PCMSK0 : nullptr; }
inline volatile uint8_t* portInputRegister(uint8_t) { return &g_portB; }
#define ISR(vec) void isr_##vec()

template <typename A, typename B> inline A min(A a, B b) { return a < (A)b ? a : (A)b; }
template <typename A, typename B> inline A max(A a, B b) { return a > (A)b ? a : (A)b; }
template <typename A, typename B, typename C> inline A constrain(A x, B lo, C hi) { return x < (A)lo ? (A)lo : (x > (A)hi ? (A)hi : x); }

class HostSerial {
 public:
  std::string rx;            // bytes waiting for the sketch to read
  std::string tx;            // everything the sketch printed
  void begin(unsigned long) {}
  void setTimeout(unsigned long) {}
  int available() { return (int)rx.size(); }
  int read() { if (rx.empty()) return -1; int c = (unsigned char)rx[0]; rx.erase(0, 1); return c; }
  size_t readBytesUntil(char term, char* buf, size_t len) {
    size_t n = 0;
    while (n < len && !rx.empty()) { char c = rx[0]; rx.erase(0, 1); if (c == term) break; buf[n++] = c; }
    return n;
  }
  void flush() {}
  size_t write(uint8_t c) { tx.push_back((char)c); return 1; }
  size_t print(const __FlashStringHelper* s) { return print(reinterpret_cast<const char*>(s)); }
  size_t print(const char* s) { tx += s; return strlen(s); }
  size_t print(const std::string& s) { tx += s; return s.size(); }
  size_t print(char c) { tx.push_back(c); return 1; }
  size_t print(unsigned char v) { return num((unsigned long)v); }
  size_t print(int v) { return snum((long)v); }
  size_t print(unsigned int v) { return num((unsigned long)v); }
  size_t print(long v) { return snum(v); }
  size_t print(unsigned long v) { return num(v); }
  size_t print(long long v) { return snum((long)v); }
  size_t print(unsigned long long v) { return num((unsigned long)v); }
  size_t print(double v, int d = 2) { char b[48]; snprintf(b, sizeof b, "%.*f", d, v); tx += b; return strlen(b); }
  template <typename T> size_t println(T v) { size_t n = print(v); tx += "\r\n"; return n + 2; }
  size_t println() { tx += "\r\n"; return 2; }
 private:
  size_t num(unsigned long v) { char b[24]; snprintf(b, sizeof b, "%lu", v); tx += b; return strlen(b); }
  size_t snum(long v) { char b[24]; snprintf(b, sizeof b, "%ld", v); tx += b; return strlen(b); }
};
extern HostSerial Serial;
