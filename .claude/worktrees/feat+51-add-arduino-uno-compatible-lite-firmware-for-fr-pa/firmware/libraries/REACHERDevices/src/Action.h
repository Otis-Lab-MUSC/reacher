/**
 * @file Action.h
 * @brief Action, Chain, and PendingAction data structures for the scheduling engine.
 * @ingroup scheduling
 */

#ifndef ACTION_H
#define ACTION_H

#include <Arduino.h>
#include "Device.h"

/// @defgroup scheduling Scheduling Engine
/// @{

/// Maximum number of steps in a single reward chain.
static constexpr uint8_t MAX_CHAIN_STEPS = 6;

/// @brief Type of action executed by the scheduler.
enum class ActionType : uint8_t {
  ACTIVATE_DEVICE,  ///< Call device->Activate(startTs, param)
  SET_TIMEOUT,      ///< Set lever timeout end
  RESET_TRIGGER,    ///< Reset trigger press count
  NONE              ///< No-op placeholder
};

/// @brief Single step in a reward chain.
struct Action {
  ActionType type;          ///< What kind of action to execute
  DeviceType target;        ///< Which device to act on
  DeviceType sourceFilter;  ///< Lever that must have caused the press (NONE = any)
  uint32_t   offsetMs;      ///< Delay from trigger fire time (0 = immediate)
  uint32_t   param;         ///< Duration for ACTIVATE, timeout length for SET_TIMEOUT
};

/// @brief Ordered sequence of actions fired by a trigger.
struct Chain {
  Action   steps[MAX_CHAIN_STEPS];  ///< Action steps executed in order
  uint8_t  numSteps;                ///< Number of active steps in this chain
};

/// @brief Deferred action in the scheduler queue.
struct PendingAction {
  Action   action;     ///< The action to execute
  uint32_t executeAt;  ///< Absolute millis() timestamp to execute
  bool     active;     ///< True if this slot is occupied
};

/// @}

#endif // ACTION_H
