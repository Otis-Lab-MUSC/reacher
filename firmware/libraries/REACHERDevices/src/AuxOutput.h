/**
 * @file AuxOutput.h
 * @brief Mega-only TTL output (tail shock) fired as the last step of reward chain 0.
 * @ingroup output_devices
 */

#ifndef AUX_H
#define AUX_H

#include <Arduino.h>

#if defined(__AVR_ATmega2560__)

#include "Device.h"
#include "AuxDraw.h"

static constexpr uint32_t AUX_MAX_DURATION    = 5000;
static constexpr uint32_t AUX_MAX_ONSET_DELAY = 600000;
static constexpr uint8_t  AUX_MAX_BLOCK_N     = 100;

static constexpr uint8_t AUX_MODE_INDEPENDENT = 0;
static constexpr uint8_t AUX_MODE_BLOCK       = 1;

/// @brief Outcome of one reward reaching the AUX step.
enum class AuxOutcome : uint8_t {
  FIRED,       ///< Pulse started (SHOCK)
  OMITTED,     ///< Draw said no (SHOCK_OMITTED)
  SUPPRESSED   ///< Test context with test-fire off; no draw (SHOCK_SUPPRESSED)
};

/// @brief TTL output driven HIGH for the pulse width. Intensity is set on the box.
///
/// Unlike Pump, Await() is not gated on armed: a pulse window always closes and
/// drives the pin LOW, and ForceOff() clears the window so nothing can re-drive it.
class Aux : public Device {
public:
  explicit Aux(int8_t pin);

  /// @brief Run the draw for one reward and start a pulse if it says shock.
  /// @param testCtx True in test mode or with no session (TEST_CHAIN)
  AuxOutcome Fire(uint32_t now, bool testCtx);

  /// @brief Drive the pin from the active pulse window. Called every loop tick.
  void Await(uint32_t now);

  /// @brief Drive LOW and cancel any pulse, including a test pulse.
  void ForceOff();

  /// @brief One pulse now for the configured duration, armed or not.
  void Test(uint32_t now);

  /// @brief Reset the PRNG (auto seed if configured 0) and restart the block.
  void ResetSession();

  /// @brief Emit the single level-000 AUX config line.
  void ReportConfig() const;

  /// @brief True iff duration is set and, in block mode, N is set.
  // Inline: an out-of-line member shifts the pavlovian hex through the shared archive.
  bool Configured() const { return duration > 0 && (mode == AUX_MODE_INDEPENDENT || blockN > 0); }

  /// @brief Drop the pin LOW and move the output to @p newPin.
  void SetPin(int8_t newPin) { ForceOff(); Device::SetPin(newPin); }

  void SetDuration(uint32_t ms);
  void SetOnsetDelay(uint32_t ms);
  void SetMode(uint32_t mode);
  void SetProbability(uint32_t p);
  void SetBlockK(uint32_t k);
  /// @brief Set n and re-clamp k to n. Returns true if k changed.
  bool SetBlockN(uint32_t n);
  void SetSeed(uint32_t seed);
  void SetTestFire(bool enable);

  uint32_t Duration() const;
  uint32_t OnsetDelay() const;
  uint8_t  Mode() const;
  uint8_t  Probability() const;
  uint8_t  BlockK() const;
  uint8_t  BlockN() const;
  uint32_t Seed() const;
  uint32_t EffectiveSeed() const;
  bool     TestFire() const;

private:
  uint32_t duration;
  uint32_t onsetDelay;
  uint8_t  mode;
  uint8_t  probability;
  uint8_t  blockK;
  uint8_t  blockN;
  uint32_t seed;           ///< Configured seed; 0 = auto at session start
  uint32_t effectiveSeed;  ///< Seed the PRNG was last reset to
  bool     testFire;

  bool     active;
  uint32_t startTimestamp;
  uint32_t endTimestamp;

  AuxDraw  draw;

  void StartPulse(uint32_t now);
};

#endif // __AVR_ATmega2560__

#endif // AUX_H
