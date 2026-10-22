/**
 * @file AuxDraw.h
 * @brief Seeded per-reward draw for the AUX output (independent and block modes).
 * @ingroup output_devices
 *
 * Plain C++ with no Arduino dependency so a host compiler can check it against
 * the golden vectors the Python simulator also reproduces. The simulator's
 * AuxDraw must stay bit-identical to this file.
 */

#ifndef AUX_DRAW_H
#define AUX_DRAW_H

#include <stdint.h>

// The Arduino core builds with gnu++11, where a constexpr member function
// cannot mutate state. Under C++14 the draw is constexpr, so the golden
// vectors can be checked with static_assert by a syntax-only cross compile.
#if __cplusplus >= 201402L
#define AUX_CONSTEXPR14 constexpr
#else
#define AUX_CONSTEXPR14 inline
#endif

/// @brief xorshift32 PRNG plus the block-schedule bookkeeping.
struct AuxDraw {
  uint32_t state;
  uint8_t  blockK;
  uint8_t  blockN;
  uint8_t  remainingK;
  uint8_t  remainingN;

  AUX_CONSTEXPR14 AuxDraw() : state(1), blockK(0), blockN(1), remainingK(0), remainingN(1) {}

  /// @brief Reset the PRNG to @p seed (must be non-zero) and restart the block at (k, n).
  AUX_CONSTEXPR14 void Reset(uint32_t seed, uint8_t k, uint8_t n) {
    state = seed ? seed : 1;
    SetBlock(k, n);
  }

  /// @brief Set the block size and restart the block. Caller clamps k <= n and n >= 1.
  AUX_CONSTEXPR14 void SetBlock(uint8_t k, uint8_t n) {
    blockK = k;
    blockN = n;
    remainingK = k;
    remainingN = n;
  }

  AUX_CONSTEXPR14 uint32_t Next() {
    uint32_t x = state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    state = x;
    return x;
  }

  /// @brief Independent mode: shock iff next() % 100 < probability.
  AUX_CONSTEXPR14 bool DrawIndependent(uint8_t probability) {
    return (Next() % 100) < probability;
  }

  /// @brief Block mode: exactly blockK shocks in each block of blockN draws.
  /// Draws even when no shocks remain, so the stream length is one per reward.
  AUX_CONSTEXPR14 bool DrawBlock() {
    if (blockN == 0) {
      Next();
      return false;
    }
    if (remainingN == 0) {
      remainingK = blockK;
      remainingN = blockN;
    }
    uint32_t r = Next() % remainingN;
    bool shock = r < remainingK;
    if (shock) remainingK--;
    remainingN--;
    if (remainingN == 0) {
      remainingK = blockK;
      remainingN = blockN;
    }
    return shock;
  }
};

#endif // AUX_DRAW_H
