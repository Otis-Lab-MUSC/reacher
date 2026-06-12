/**
 * @file Pump.h
 * @brief Syringe pump relay controller.
 * @ingroup output_devices
 */

#ifndef PUMP_H
#define PUMP_H

#include <Arduino.h>
#include "Device.h"

/// @brief Syringe pump relay controller. Drives a relay HIGH during infusion window.
class Pump : public Device {
public:
  Pump(int8_t pin, uint32_t duration);

  /// @brief Schedule pump activation for a time window.
  /// @param startTs Start timestamp (millis)
  /// @param dur Duration in ms
  void Activate(uint32_t startTs, uint32_t dur);

  /// @brief Drive relay based on current time vs active window. Called every loop tick.
  void Await(uint32_t currentTimestamp);

  /// @brief Fire a test infusion for configured duration (bypasses armed check).
  void Test(uint32_t currentTimestamp);

  void SetDuration(uint32_t duration);
  uint32_t Duration() const;

private:
  uint32_t duration;        ///< Default infusion duration in ms
  uint32_t startTimestamp;  ///< Active window start
  uint32_t endTimestamp;    ///< Active window end
  bool     isTesting;       ///< Bypass armed check for pre-session test

  void On();
  void Off();
};

#endif // PUMP_H
