"""Trading agents.

Each agent turns candles into a target exposure in [0, 1] (0 = all cash, 1 = fully in
the coin). The bot trades spot, long-only: with $50 there is no margin to lose, and a
short on spot is impossible anyway.

Signals are computed on bar t's close and executed on bar t+1, so no agent ever sees
the future (the look-ahead test in tests/test_brain.py enforces this).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class Param:
    name: str
    low: float
    high: float
    integer: bool = False

    def clip(self, value: float) -> float:
        value = min(max(float(value), self.low), self.high)
        return float(round(value)) if self.integer else value


class Agent:
    name: str = "agent"
    description: str = ""
    params: tuple[Param, ...] = ()
    defaults: dict[str, float] = {}

    def signal(self, df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
        raise NotImplementedError

    def resolve(self, p: dict[str, float] | None) -> dict[str, float]:
        merged = {**self.defaults, **(p or {})}
        return {prm.name: prm.clip(merged[prm.name]) for prm in self.params}


def _latch(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """Hold 1 from an entry bar until an exit bar, 0 otherwise (causal state machine)."""
    state = pd.Series(np.nan, index=entry.index)
    state[exit_.fillna(False).astype(bool)] = 0.0
    state[entry.fillna(False).astype(bool)] = 1.0
    return state.ffill().fillna(0.0)


class TrendAgent(Agent):
    name = "trend"
    description = "EMA crossover; exposure grows with the fast/slow gap measured in ATRs"
    params = (
        Param("fast", 5, 50, integer=True),
        Param("slow", 20, 200, integer=True),
        Param("scale", 0.25, 3.0),
    )
    defaults = {"fast": 20, "slow": 80, "scale": 1.0}

    def signal(self, df, p):
        p = self.resolve(p)
        fast = int(p["fast"])
        slow = max(int(p["slow"]), fast + 5)
        gap = (ind.ema(df["close"], fast) - ind.ema(df["close"], slow)) / ind.atr(df, 14)
        return (gap / p["scale"]).clip(0, 1).fillna(0.0)


class MeanReversionAgent(Agent):
    name = "mean_reversion"
    description = "Buys oversold dips (Bollinger z-score + RSI), sells when price returns to the mean"
    params = (
        Param("window", 10, 60, integer=True),
        Param("entry_z", 0.5, 3.0),
        Param("rsi_period", 5, 30, integer=True),
        Param("rsi_buy", 15, 45),
    )
    defaults = {"window": 20, "entry_z": 2.0, "rsi_period": 14, "rsi_buy": 30}

    def signal(self, df, p):
        p = self.resolve(p)
        z = ind.zscore(df["close"], int(p["window"]))
        r = ind.rsi(df["close"], int(p["rsi_period"]))
        entry = (z < -p["entry_z"]) & (r < p["rsi_buy"])
        return _latch(entry, z >= 0)


class BreakoutAgent(Agent):
    name = "breakout"
    description = "Donchian channel breakout (turtle style): buy new highs, exit on new lows"
    params = (
        Param("entry", 10, 120, integer=True),
        Param("exit", 5, 60, integer=True),
    )
    defaults = {"entry": 55, "exit": 20}

    def signal(self, df, p):
        p = self.resolve(p)
        entry = df["close"] > ind.prior_high(df["high"], int(p["entry"]))
        exit_ = df["close"] < ind.prior_low(df["low"], int(p["exit"]))
        return _latch(entry, exit_)


class MomentumAgent(Agent):
    name = "momentum"
    description = "Volatility-scaled time-series momentum over a lookback window"
    params = (
        Param("lookback", 10, 180, integer=True),
        Param("threshold", 0.0, 1.5),
    )
    defaults = {"lookback": 60, "threshold": 0.25}

    def signal(self, df, p):
        p = self.resolve(p)
        n = int(p["lookback"])
        log_close = np.log(df["close"])
        move = log_close - log_close.shift(n)
        noise = log_close.diff().rolling(n, min_periods=n).std() * np.sqrt(n)
        score = move / noise.replace(0, np.nan)
        return (score - p["threshold"]).clip(0, 1).fillna(0.0)


def default_agents() -> list[Agent]:
    return [TrendAgent(), MeanReversionAgent(), BreakoutAgent(), MomentumAgent()]
