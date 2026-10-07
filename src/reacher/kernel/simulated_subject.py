"""Stochastic behaviour of the simulated animal.

The simulator's *board* (kernel.simulator.FirmwareSimulator) reproduces what
the firmware does with a press; this module only decides when presses and licks
happen. Keeping the two apart is what lets the same animal run on every
paradigm: schedule outcomes come from the configuration, never from the animal.
"""

import random
from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass(frozen=True)
class SubjectProfile:
    mean_iri_ms: int = 5000  # mean gap between operant presses (exponential)
    inactive_fraction: float = 0.15  # share of presses aimed at the non-reinforced lever
    pavlovian_mean_iri_ms: int = 20000  # non-contingent pressing during a Pavlovian run
    press_ms: Tuple[int, int] = (80, 200)
    lick_ms: Tuple[int, int] = (30, 100)
    lick_gap_ms: Tuple[int, int] = (60, 120)  # inside a bout, ~6-7 Hz
    lick_latency_ms: Tuple[int, int] = (200, 600)
    lick_tail_ms: int = 1000  # licking continues briefly after the infusion ends


class SimulatedSubject:
    def __init__(self, rng: random.Random, profile: SubjectProfile = SubjectProfile()):
        self.rng = rng
        self.profile = profile

    def press_gap_ms(self, pavlovian: bool = False) -> int:
        mean = self.profile.pavlovian_mean_iri_ms if pavlovian else self.profile.mean_iri_ms
        return max(1, int(self.rng.expovariate(1.0 / mean)))

    def press_duration_ms(self) -> int:
        return self.rng.randint(*self.profile.press_ms)

    def pick_lever(self, reinforced: Sequence[str], other: Sequence[str]) -> str:
        """Choose an orientation; the animal mostly works the reinforced lever."""
        if reinforced and other:
            pool = other if self.rng.random() < self.profile.inactive_fraction else reinforced
        else:
            pool = reinforced or other
        return self.rng.choice(list(pool))

    def lick_bout(self, start_ms: int, span_ms: int) -> List[Tuple[int, int]]:
        """(start, end) of each lick while drinking for *span_ms* from *start_ms*."""
        p = self.profile
        t = start_ms + self.rng.randint(*p.lick_latency_ms)
        stop = start_ms + span_ms + p.lick_tail_ms
        licks = []
        while t < stop:
            end = t + self.rng.randint(*p.lick_ms)
            licks.append((t, end))
            t = end + self.rng.randint(*p.lick_gap_ms)
        return licks
