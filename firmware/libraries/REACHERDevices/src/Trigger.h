/**
 * @file Trigger.h
 * @brief Trigger struct — configurable conditions that map input events to chain firing.
 * @ingroup scheduling
 */

#ifndef TRIGGER_H
#define TRIGGER_H

#include <Arduino.h>
#include "Device.h"

/// @brief Type of condition a trigger evaluates.
enum class TriggerType : uint8_t {
  PRESS_COUNT,          ///< FR/PR: N active presses -> fire chain
  ABSENCE_TIMER,        ///< Omission: no press for N ms -> fire chain
  AVAILABILITY_WINDOW,  ///< VI: random window, press during it -> fire chain
  MANUAL                ///< Serial command test trigger
};

/// @brief Classification of a lever press event.
enum class PressClass : uint8_t {
  ACTIVE,    ///< Press on reinforced lever outside timeout
  INACTIVE,  ///< Press on non-reinforced lever
  TIMEOUT    ///< Press during timeout period
};

/// @brief Configurable condition that maps input events to chain firing.
///
/// Supports press counting (FR/PR), absence timing (Omission), and
/// availability windows (VI — simplified uniform random, not exponential).
/// @note PR uses arithmetic progression (threshold += step), not Richardson & Roberts exponential.
/// @note VI uses a uniform random availability window within a fixed interval,
///       not the Fleshler-Hoffman exponential distribution.
/// @see Scheduler::OnInputEvent, Chain
struct Trigger {
  TriggerType type       = TriggerType::PRESS_COUNT;  ///< Trigger evaluation mode
  uint8_t     chainIndex = 0;                         ///< Index of chain to fire when condition is met
  bool        enabled    = false;                     ///< Trigger only evaluates when enabled

  // PRESS_COUNT fields
  uint8_t     threshold        = 1;   ///< Required press count to fire (FR value)
  uint8_t     initialThreshold = 1;   ///< Saved threshold for PR reset
  uint8_t     pressCount       = 0;   ///< Current accumulated active presses
  uint8_t     prStep           = 0;   ///< PR step increment (0 = fixed ratio)

  // ABSENCE_TIMER fields
  uint32_t    absenceMs    = 0;  ///< Required absence duration (ms) to fire
  uint32_t    absenceStart = 0;  ///< Timestamp of last press (timer restarts here)

  // AVAILABILITY_WINDOW fields
  uint32_t    windowStart  = 0;      ///< Start of current availability window
  uint32_t    windowEnd    = 0;      ///< End of current availability window / interval
  uint32_t    intervalMin  = 0;      ///< Total interval length (ms) for window cycling
  bool        firedInWindow = false;  ///< True if already fired in current window

  // Source filtering (NONE = accept any lever, LEVER_RH/LH = specific lever only)
  DeviceType  sourceFilter = DeviceType::NONE;  ///< Lever filter (NONE accepts any)

  // Probability of firing when condition is met (0-100, default 100 = always)
  uint8_t     probability  = 100;  ///< Percent chance to fire when threshold met

  /// @brief Evaluate an input event against this trigger's condition.
  /// @param source DeviceType of the lever that was pressed
  /// @param timestamp millis() at press time
  /// @return true if trigger should fire its chain
  bool OnInputEvent(DeviceType source, uint32_t timestamp) {
    if (!enabled) return false;
    if (sourceFilter != DeviceType::NONE && sourceFilter != source) return false;

    switch (type) {
      case TriggerType::PRESS_COUNT:
        pressCount++;
        if (pressCount >= threshold) {
          if (probability < 100 && (uint8_t)random(100) >= probability) {
            pressCount = 0;
            return false;
          }
          pressCount = 0;
          if (prStep > 0) {
            if (threshold <= 255 - prStep) {
              threshold += prStep;
            } else {
              // Fix: FW-005 — Emit info event when PR threshold is capped at max
              Serial.print(F("{\"level\":\"001\",\"device\":\"CONTROLLER\",\"desc\":\"PR threshold capped at "));
              Serial.print(threshold);
              Serial.println(F("\"}"));
            }
          }
          return true;
        }
        return false;

      case TriggerType::ABSENCE_TIMER:
        absenceStart = timestamp;
        return false;

      case TriggerType::AVAILABILITY_WINDOW:
        if (!firedInWindow && (int32_t)(timestamp - windowStart) >= 0 && (int32_t)(windowEnd - timestamp) > 0) {
          if (probability < 100 && (uint8_t)random(100) >= probability) return false;
          firedInWindow = true;
          return true;
        }
        return false;

      default:
        return false;
    }
  }

  /// @brief Tick-based evaluation (called every loop iteration).
  /// @param now Current millis() timestamp
  /// @return true if trigger should fire its chain
  bool OnTick(uint32_t now) {
    if (!enabled) return false;

    switch (type) {
      case TriggerType::ABSENCE_TIMER:
        if (absenceStart > 0 && (now - absenceStart) >= absenceMs) {
          absenceStart = now;  // restart timer immediately for next omission cycle
          if (probability < 100 && (uint8_t)random(100) >= probability) return false;
          return true;
        }
        return false;

      case TriggerType::AVAILABILITY_WINDOW:
        // Overflow-safe: see Scheduler.cpp:232 for rationale (Bug 2.2)
        if (windowEnd > 0 && (int32_t)(now - windowEnd) >= 0) {
          // Interval expired, start new interval with new random window
          windowStart = now + random(0, intervalMin);
          windowEnd = now + intervalMin;
          firedInWindow = false;
        }
        return false;

      default:
        return false;
    }
  }

  /// @brief Reset all runtime state (press count, timers, PR threshold).
  void Reset() {
    pressCount = 0;
    absenceStart = 0;
    windowStart = 0;
    windowEnd = 0;
    firedInWindow = false;
    if (prStep > 0) {
      threshold = initialThreshold;
    }
  }
};

#endif // TRIGGER_H
