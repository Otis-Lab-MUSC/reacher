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
/// SLM timestamp PCINT input. On the Mega 2560 target, pin 11 is PB5 / PCINT5.
/// (The Mega's SPI bus is on 50–53, not 11 — that is an UNO pinout.) Remappable
/// at runtime to any PCINT0/PORTB pin; the backend exposes 10–13 on the Mega.
/// Note 10 and 13 are the default lever pins — the backend rejects collisions.
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

/// External TTL session-start input (Mega external-interrupt pin, INPUT_PULLUP).
/// Assignable only to 18/19/20/21 (INT5/INT4/INT3/INT2) — the Mega's remaining
/// external-interrupt pins. INT0 (2) and INT1 (3) are excluded deliberately:
/// 2 is the fixed microscope timestamp input and re-attaching it would silently
/// replace Microscope::TimestampISR, killing frame logging with no error.
/// NOTE: all four assignable pins are dual-purpose on the Mega — 18/19 are
/// Serial1 TX/RX and 20/21 are I2C SDA/SCL. Nothing in this firmware uses
/// either peripheral today; revisit before adding one.
constexpr int8_t PIN_EXT_TRIGGER    = 18;

/// @}

#endif // PINS_H
