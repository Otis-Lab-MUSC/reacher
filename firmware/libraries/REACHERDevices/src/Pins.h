/**
 * @file Pins.h
 * @brief Pin assignments for REACHER v2.0.0 hardware.
 * @ingroup hardware
 */

#ifndef PINS_H
#define PINS_H

/// @defgroup hardware Hardware Definitions
/// @{

/// Right-hand lever switch (INPUT_PULLUP)
constexpr int8_t PIN_LEVER_RH        = 10;
/// Left-hand lever switch (INPUT_PULLUP).
/// NOTE: pin 13 also drives the onboard LED via a series resistor; with
/// INPUT_PULLUP this can weakly pull the line low. Validated on current
/// hardware; revisit if reads become unstable.
constexpr int8_t PIN_LEVER_LH        = 13;
/// Lick detection circuit (INPUT_PULLUP)
constexpr int8_t PIN_LICK_CIRCUIT    = 5;
/// Microscope frame timestamp ISR input (INT0)
constexpr int8_t PIN_MICROSCOPE_TS   = 2;
/// SLM timestamp PCINT input (PCINT0 group, PB3). Note: pin 11 is also SPI MOSI;
/// avoid when SPI peripherals are in use. Remappable to any pin 8–13 at runtime.
constexpr int8_t PIN_SLM_TS          = 11;

/// Primary tone output (PWM capable)
constexpr int8_t PIN_CUE             = 3;
/// Primary syringe pump relay
constexpr int8_t PIN_PUMP            = 4;
/// Optogenetic laser PWM output
constexpr int8_t PIN_LASER           = 6;
/// Microscope trigger pulse output
constexpr int8_t PIN_MICROSCOPE_TRIG = 9;
/// Secondary tone output
constexpr int8_t PIN_CUE_2          = 7;
/// Secondary syringe pump relay
constexpr int8_t PIN_PUMP_2         = 8;

/// @}

#endif // PINS_H
