#include "Arduino.h"
uint32_t g_millis = 0;
uint8_t g_pinMode[NUM_PINS];
uint8_t g_outLevel[NUM_PINS];
uint8_t g_inputPressed[NUM_PINS];
int32_t g_toneFreq[NUM_PINS];
volatile uint8_t PCMSK0, PCICR;
volatile uint8_t g_portB;
HostSerial Serial;
