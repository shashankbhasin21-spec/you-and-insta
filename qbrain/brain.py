"""The QuantumBrain: a meta-agent that fuses the agents' signals.

* Superposition: the brain holds one amplitude per agent. Its prior trust in agent i is
  given by the Born rule, w_i = a_i^2 / sum_j a_j^2.
* Online learning: every bar the brain scores each agent on what its signal would have
  earned recently (an exponentially-weighted average, in units of market volatility)
  and re-weights them multiplicatively (the Hedge / exponential-weights algorithm):
      w_i(t)  ~  prior_i * exp(learning_rate * score_i(t))
  Agents that are working in the current market regime gain influence; ones that are
  losing fade out, without ever being switched off completely.
* Measurement: the final exposure is sum_i w_i(t) * signal_i(t), and anything below the
  conviction threshold collapses to 0 (stay in cash rather than pay fees for a
  half-hearted position).

Amplitudes, learning rate, memory and conviction are tuned by the quantum-inspired
optimizer together with every agent's own parameters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .agents import Agent, Param, _latch, default_agents

BRAIN_PARAMS = (
    Param("learning_rate", 0.0, 30.0),
    Param("memory", 5, 120, integer=True),
    Param("conviction", 0.0, 0.5),
)
AMPLITUDE = Param("amp", 0.0, 1.0)


class QuantumBrain:
    def __init__(
        self,
        agents: list[Agent] | None = None,
        agent_params: dict[str, dict[str, float]] | None = None,
        amplitudes: dict[str, float] | None = None,
        learning_rate: float = 5.0,
        memory: int = 30,
        conviction: float = 0.1,
    ):
        self.agents = agents or default_agents()
        agent_params = agent_params or {}
        amplitudes = amplitudes or {}
        self.agent_params = {a.name: a.resolve(agent_params.get(a.name)) for a in self.agents}
        self.amplitudes = {a.name: float(amplitudes.get(a.name, 0.5)) for a in self.agents}
        self.learning_rate = float(learning_rate)
        self.memory = int(memory)
        self.conviction = float(conviction)

    # --- signals ---------------------------------------------------------------------

    def agent_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {a.name: a.signal(df, self.agent_params[a.name]) for a in self.agents}, index=df.index
        )

    def prior(self) -> pd.Series:
        amps = pd.Series(self.amplitudes, dtype=float)
        power = amps**2
        if power.sum() <= 0:
            return pd.Series(1.0 / len(amps), index=amps.index)
        return power / power.sum()

    def weights(self, df: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
        ret = df["close"].pct_change().fillna(0.0)
        agent_ret = signals.shift(1).fillna(0.0).mul(ret, axis=0)
        market_vol = ret.ewm(halflife=self.memory, adjust=False).std().fillna(0.0)
        score = agent_ret.ewm(halflife=self.memory, adjust=False).mean().div(market_vol + 1e-12, axis=0)
        logits = np.log(self.prior().reindex(signals.columns).to_numpy() + 1e-12) + self.learning_rate * score
        logits = logits.sub(logits.max(axis=1), axis=0)
        w = np.exp(logits)
        return w.div(w.sum(axis=1), axis=0)

    def exposure(self, df: pd.DataFrame) -> pd.Series:
        signals = self.agent_signals(df)
        combined = (self.weights(df, signals) * signals).sum(axis=1).clip(0, 1)
        # Hysteresis: enter above the conviction threshold, but only leave once conviction
        # falls below half of it, so a signal hovering at the threshold cannot whipsaw.
        engaged = _latch(combined >= self.conviction, combined < self.conviction / 2)
        return combined.where(engaged > 0, 0.0)

    def explain(self, df: pd.DataFrame) -> dict:
        """Snapshot of the latest bar: each agent's vote and current weight."""
        signals = self.agent_signals(df)
        w = self.weights(df, signals).iloc[-1]
        s = signals.iloc[-1]
        return {name: {"signal": round(float(s[name]), 3), "weight": round(float(w[name]), 3)} for name in s.index}

    # --- optimisation plumbing -------------------------------------------------------

    @staticmethod
    def search_space(agents: list[Agent] | None = None) -> list[Param]:
        agents = agents or default_agents()
        space = []
        for a in agents:
            space += [Param(f"{a.name}.{p.name}", p.low, p.high, p.integer) for p in a.params]
            space.append(Param(f"amp.{a.name}", AMPLITUDE.low, AMPLITUDE.high))
        space += [Param(f"brain.{p.name}", p.low, p.high, p.integer) for p in BRAIN_PARAMS]
        return space

    @classmethod
    def from_flat(cls, flat: dict[str, float], agents: list[Agent] | None = None) -> "QuantumBrain":
        agents = agents or default_agents()
        agent_params: dict[str, dict[str, float]] = {a.name: {} for a in agents}
        amplitudes, brain = {}, {}
        for key, value in flat.items():
            group, name = key.split(".", 1)
            if group == "amp":
                amplitudes[name] = value
            elif group == "brain":
                brain[name] = value
            else:
                agent_params[group][name] = value
        return cls(agents, agent_params, amplitudes, **brain)

    def to_dict(self) -> dict:
        return {
            "agent_params": self.agent_params,
            "amplitudes": self.amplitudes,
            "learning_rate": self.learning_rate,
            "memory": self.memory,
            "conviction": self.conviction,
        }

    @classmethod
    def from_dict(cls, data: dict, agents: list[Agent] | None = None) -> "QuantumBrain":
        return cls(
            agents,
            data.get("agent_params"),
            data.get("amplitudes"),
            data.get("learning_rate", 5.0),
            data.get("memory", 30),
            data.get("conviction", 0.1),
        )
