"""Quantum-inspired evolutionary optimizer (QIEA).

This is classical code that borrows ideas from quantum computing; it does not need, or
benefit from, a quantum computer. Based on Han & Kim, "Quantum-inspired evolutionary
algorithm for a class of combinatorial optimization" (IEEE TEVC, 2002).

* Each candidate is a register of Q-bits. A Q-bit is an angle theta, and "measuring" it
  gives 1 with probability sin^2(theta), so a register describes a probability
  distribution over every bit string at once (the "superposition").
* Each generation every register is measured, the bit strings are decoded (Gray code)
  into parameters and scored.
* A rotation gate turns each Q-bit a small step toward the bits of the best solution
  found so far, so the population's probability mass drifts toward good regions
  while still sampling around them.
* A NOT-gate mutation swaps a Q-bit's 0/1 probabilities now and then, and a stalled
  search resets the weaker half of the population to equal superposition (a
  "Hadamard reset"), which keeps it from collapsing onto one local optimum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .agents import Param

HALF_PI = np.pi / 2


@dataclass
class OptimizeResult:
    best_params: dict[str, float]
    best_fitness: float
    history: list[float] = field(default_factory=list)
    evaluations: int = 0


def gray_to_int(bits: np.ndarray) -> int:
    value = 0
    acc = 0
    for bit in bits:
        acc ^= int(bit)
        value = (value << 1) | acc
    return value


class QuantumInspiredOptimizer:
    def __init__(
        self,
        space: list[Param],
        fitness: Callable[[dict[str, float]], float],
        population: int = 16,
        generations: int = 30,
        bits_per_param: int = 6,
        rotation: float = 0.015 * np.pi,
        mutation_rate: float = 0.01,
        floor: float = 0.04 * np.pi,
        stall_generations: int = 6,
        seed: int = 0,
    ):
        if not space:
            raise ValueError("empty search space")
        self.space = space
        self.fitness = fitness
        self.population = population
        self.generations = generations
        self.bits = bits_per_param
        self.rotation = rotation
        self.mutation_rate = mutation_rate
        self.floor = floor
        self.stall_generations = stall_generations
        self.rng = np.random.default_rng(seed)
        self._cache: dict[bytes, float] = {}

    def decode(self, bits: np.ndarray) -> dict[str, float]:
        top = 2**self.bits - 1
        out = {}
        for i, prm in enumerate(self.space):
            k = gray_to_int(bits[i * self.bits:(i + 1) * self.bits])
            out[prm.name] = prm.clip(prm.low + (prm.high - prm.low) * k / top)
        return out

    def _score(self, bits: np.ndarray) -> float:
        key = np.packbits(bits).tobytes()
        if key not in self._cache:
            value = float(self.fitness(self.decode(bits)))
            self._cache[key] = value if np.isfinite(value) else -np.inf
        return self._cache[key]

    def run(self) -> OptimizeResult:
        n_bits = len(self.space) * self.bits
        theta = np.full((self.population, n_bits), np.pi / 4)   # equal superposition
        local_best = np.zeros((self.population, n_bits), dtype=bool)
        local_fit = np.full(self.population, -np.inf)
        best_bits, best_fit = None, -np.inf
        history, stall = [], 0

        for _ in range(self.generations):
            measured = self.rng.random(theta.shape) < np.sin(theta) ** 2
            fits = np.array([self._score(row) for row in measured])

            improved = fits > local_fit
            local_best[improved] = measured[improved]
            local_fit[improved] = fits[improved]

            gen_best = int(np.argmax(fits))
            if best_bits is None or fits[gen_best] > best_fit:
                best_fit, best_bits, stall = fits[gen_best], measured[gen_best].copy(), 0
            else:
                stall += 1

            # Rotation gate: move each Q-bit toward its attractor's bit, pulling half the
            # population toward the global best and half toward their own best.
            attractor = local_best.copy()
            attractor[: self.population // 2] = best_bits
            differs = measured != attractor
            direction = np.where(attractor, 1.0, -1.0)
            theta += self.rotation * direction * differs

            # NOT-gate mutation: swap the 0/1 amplitudes of a few random Q-bits.
            flip = self.rng.random(theta.shape) < self.mutation_rate
            theta[flip] = HALF_PI - theta[flip]

            if stall >= self.stall_generations:
                weak = np.argsort(local_fit)[: self.population // 2]
                theta[weak] = np.pi / 4
                local_fit[weak] = -np.inf
                stall = 0

            theta = np.clip(theta, self.floor, HALF_PI - self.floor)
            history.append(best_fit)

        return OptimizeResult(self.decode(best_bits), best_fit, history, len(self._cache))
