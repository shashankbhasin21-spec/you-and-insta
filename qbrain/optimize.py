"""Training the brain, and walk-forward validation.

Optimising on the same data you report results on produces beautiful, meaningless
numbers. So walk_forward() tunes the brain on the older part of the history only, then
grades it on the newer part it has never seen, against buy-and-hold and an untuned
brain. Only the out-of-sample column says anything about the future, and even that
is just one sample.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .backtest import BacktestResult, max_drawdown, run_backtest
from .brain import QuantumBrain
from .config import Config, bars_per_year
from .quantum import OptimizeResult, QuantumInspiredOptimizer
from .risk import scale_exposure

log = logging.getLogger("qbrain")


def apply_band(target: pd.Series, band: float) -> pd.Series:
    """Mirror the broker's rebalance band: ignore moves smaller than `band`, always allow exits."""
    values = target.to_numpy()
    out = np.empty_like(values)
    held = 0.0
    for i, t in enumerate(values):
        if (t <= 0 < held) or abs(t - held) >= band:
            held = t
        out[i] = held
    return pd.Series(out, index=target.index)


def fast_metrics(df: pd.DataFrame, exposure: pd.Series, fee_rate: float, periods_per_year: float) -> dict:
    """Quick equity curve: hold exposure[t] through bar t+1, pay fees on every change."""
    ret = df["close"].pct_change().fillna(0.0)
    held = exposure.shift(1).fillna(0.0)
    turnover = exposure.diff().abs().fillna(exposure.abs())
    strat = held * ret - fee_rate * turnover
    equity = (1 + strat).cumprod()
    std = strat.std()
    return {
        "total_return": float(equity.iloc[-1] - 1),
        "sharpe": float(strat.mean() / std * np.sqrt(periods_per_year)) if std > 0 else 0.0,
        "max_drawdown": max_drawdown(equity),
        "entries": int(((exposure > 0.01) & (held <= 0.01)).sum()),
    }


def make_fitness(df: pd.DataFrame, cfg: Config) -> Callable[[dict[str, float]], float]:
    ppy = bars_per_year(cfg.timeframe)

    def fitness(flat: dict[str, float]) -> float:
        brain = QuantumBrain.from_flat(flat)
        exposure = apply_band(scale_exposure(df, brain.exposure(df), cfg.risk, ppy), cfg.risk.rebalance_band)
        m = fast_metrics(df, exposure, cfg.fee_rate, ppy)
        sharpe = m["sharpe"]
        if sharpe > 0:  # a handful of lucky trades proves nothing: discount thin evidence
            sharpe *= min(1.0, m["entries"] / cfg.optimizer.min_trades)
        return sharpe - 2.0 * m["max_drawdown"]

    return fitness


def optimize_brain(df: pd.DataFrame, cfg: Config) -> tuple[QuantumBrain, OptimizeResult]:
    o = cfg.optimizer
    optimizer = QuantumInspiredOptimizer(
        QuantumBrain.search_space(),
        make_fitness(df, cfg),
        population=o.population,
        generations=o.generations,
        bits_per_param=o.bits_per_param,
        seed=o.seed,
    )
    result = optimizer.run()
    log.info("optimised on %d bars: fitness %.3f after %d evaluations", len(df), result.best_fitness, result.evaluations)
    return QuantumBrain.from_flat(result.best_params), result


@dataclass
class WalkForwardReport:
    brain: QuantumBrain
    optimization: OptimizeResult
    train: BacktestResult
    test: BacktestResult
    test_untuned: BacktestResult
    split_time: pd.Timestamp


def walk_forward(df: pd.DataFrame, cfg: Config, capital: float, symbol: str = "SYMBOL") -> WalkForwardReport:
    split = int(len(df) * cfg.optimizer.train_fraction)
    if split < 300 or len(df) - split < 100:
        raise ValueError(f"need more history for walk-forward (have {len(df)} bars)")
    brain, opt = optimize_brain(df.iloc[:split], cfg)
    return WalkForwardReport(
        brain=brain,
        optimization=opt,
        train=run_backtest(df.iloc[:split], brain, cfg, capital, start=200, symbol=symbol),
        test=run_backtest(df, brain, cfg, capital, start=split, symbol=symbol),
        test_untuned=run_backtest(df, QuantumBrain(), cfg, capital, start=split, symbol=symbol),
        split_time=df.index[split],
    )
