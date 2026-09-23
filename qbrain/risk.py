"""Risk management: the agent with veto power over the brain.

Two layers:
* scale_exposure (vectorised, causal): volatility targeting and the exposure cap.
* RiskGuard (stateful, bar by bar): trailing stop, daily loss limit and the
  max-drawdown kill switch. Once the kill switch fires the bot stays in cash until a
  human deletes the state file; losing money slowly is better than losing it all.
"""

from __future__ import annotations

import math

import pandas as pd

from . import indicators as ind
from .config import RiskConfig


def scale_exposure(df: pd.DataFrame, exposure: pd.Series, cfg: RiskConfig, periods_per_year: float) -> pd.Series:
    vol = ind.realized_vol(df["close"], cfg.vol_lookback, periods_per_year)
    scale = (cfg.target_vol / vol).clip(upper=1.0).fillna(0.0)
    return (exposure * scale).clip(0.0, cfg.max_exposure)


class RiskGuard:
    def __init__(self, cfg: RiskConfig, equity: float):
        self.cfg = cfg
        self.peak = equity
        self.halted = False
        self.day: str | None = None
        self.day_start_equity = equity
        self.trail_high: float | None = None
        self.cooldown = 0

    def decide(self, time, price: float, atr: float, equity: float, target: float, current_exposure: float) -> tuple[float, str]:
        cfg = self.cfg
        self.peak = max(self.peak, equity)
        if equity <= self.peak * (1 - cfg.max_drawdown):
            self.halted = True
        if self.halted:
            return 0.0, "kill-switch"

        day = pd.Timestamp(time).strftime("%Y-%m-%d")
        if day != self.day:
            self.day, self.day_start_equity = day, equity
        if equity <= self.day_start_equity * (1 - cfg.daily_loss_limit):
            return 0.0, "daily-loss-limit"

        if current_exposure > 0.01:
            self.trail_high = price if self.trail_high is None else max(self.trail_high, price)
            if math.isfinite(atr) and price < self.trail_high - cfg.atr_stop_mult * atr:
                self.trail_high = None
                self.cooldown = cfg.stop_cooldown_bars
                return 0.0, "trailing-stop"
        else:
            self.trail_high = None

        if self.cooldown > 0:
            self.cooldown -= 1
            return 0.0, "cooldown"
        return min(max(target, 0.0), cfg.max_exposure), "signal"

    def to_dict(self) -> dict:
        return {
            "peak": self.peak,
            "halted": self.halted,
            "day": self.day,
            "day_start_equity": self.day_start_equity,
            "trail_high": self.trail_high,
            "cooldown": self.cooldown,
        }

    @classmethod
    def from_dict(cls, cfg: RiskConfig, data: dict) -> "RiskGuard":
        guard = cls(cfg, data.get("peak", 0.0))
        for key, value in data.items():
            setattr(guard, key, value)
        return guard
