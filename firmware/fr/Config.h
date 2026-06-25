/**
 * @file Config.h
 * @brief Fixed Ratio trigger and chain configuration.
 */

#ifndef CONFIG_H
#define CONFIG_H

#include <Scheduler.h>
#include <Cue.h>
#include <Pump.h>
#include <Laser.h>

/// Default cue tone frequency in Hz.
static constexpr uint32_t DEFAULT_CUE_FREQUENCY      = 8000;
/// Default cue tone duration in ms.
static constexpr uint32_t DEFAULT_CUE_DURATION       = 1600;
/// Default syringe pump infusion duration in ms.
static constexpr uint32_t DEFAULT_PUMP_DURATION      = 2000;
/// Default laser oscillation frequency in Hz.
static constexpr uint8_t  DEFAULT_LASER_FREQUENCY    = 40;
/// Default laser activation duration in ms.
static constexpr uint32_t DEFAULT_LASER_DURATION     = 5000;
/// Default post-reward timeout interval in ms.
static constexpr uint32_t DEFAULT_TIMEOUT_INTERVAL   = 20000;

/// @brief Configure a Fixed Ratio schedule (single trigger, single chain).
///
/// N active lever presses -> reward chain (cue + cue2 -> pump + laser after trace).
/// @param sched Scheduler to configure
/// @param cue Primary cue device (duration used for chain timing)
/// @param cue2 Secondary cue device
/// @param pump Pump device (duration used for chain timing)
/// @param laser Laser device (duration used for chain timing)
/// @param ratio Number of presses required per reward
/// @param timeoutTarget Which lever receives the post-reward timeout
/// @param traceInterval Delay between cue offset and reward onset (ms)
/// @param cueFilter Per-action lever source filter for the CUE step (NONE = any)
/// @param cue2Filter Per-action lever source filter for the CUE_2 step (NONE = any)
/// @param pumpFilter Per-action lever source filter for the PUMP step (NONE = any)
/// @param pump2Filter Per-action lever source filter when pumpTarget is PUMP_2 (NONE = any)
inline void configureFixedRatio(Scheduler& sched, Cue& cue, Cue& cue2, Pump& pump, Laser& laser, uint8_t ratio, DeviceType timeoutTarget, uint32_t traceInterval, DeviceType pumpTarget = DeviceType::PUMP, DeviceType cueFilter = DeviceType::NONE, DeviceType cue2Filter = DeviceType::NONE, DeviceType pumpFilter = DeviceType::NONE, DeviceType pump2Filter = DeviceType::NONE) {
  Trigger* t = sched.GetTrigger(0);
  if (t) {
    t->type = TriggerType::PRESS_COUNT;
    t->chainIndex = 0;
    t->enabled = true;
    t->threshold = ratio;
    t->initialThreshold = ratio;
    t->pressCount = 0;
    t->prStep = 0;
    t->sourceFilter = DeviceType::NONE;
    t->probability = 100;
  }

  Trigger* t1 = sched.GetTrigger(1);
  if (t1) t1->enabled = false;

  Chain* c = sched.GetChain(0);
  if (c) {
    c->numSteps = 5;

    c->steps[0].type = ActionType::ACTIVATE_DEVICE;
    c->steps[0].target = DeviceType::CUE;
    c->steps[0].sourceFilter = cueFilter;
    c->steps[0].offsetMs = 0;
    c->steps[0].param = cue.Duration();

    c->steps[1].type = ActionType::ACTIVATE_DEVICE;
    c->steps[1].target = DeviceType::CUE_2;
    c->steps[1].sourceFilter = cue2Filter;
    c->steps[1].offsetMs = 0;
    c->steps[1].param = cue2.Duration();

    c->steps[2].type = ActionType::ACTIVATE_DEVICE;
    c->steps[2].target = pumpTarget;
    c->steps[2].sourceFilter = (pumpTarget == DeviceType::PUMP_2) ? pump2Filter : pumpFilter;
    c->steps[2].offsetMs = cue.Duration() + traceInterval;
    c->steps[2].param = pump.Duration();

    c->steps[3].type = ActionType::ACTIVATE_DEVICE;
    c->steps[3].target = DeviceType::LASER;
    c->steps[3].sourceFilter = DeviceType::NONE;
    c->steps[3].offsetMs = cue.Duration() + traceInterval;
    c->steps[3].param = laser.Duration();

    c->steps[4].type = ActionType::SET_TIMEOUT;
    c->steps[4].target = timeoutTarget;
    c->steps[4].sourceFilter = DeviceType::NONE;
    c->steps[4].offsetMs = 0;
    c->steps[4].param = sched.TimeoutInterval();
  }
}

#endif // CONFIG_H
