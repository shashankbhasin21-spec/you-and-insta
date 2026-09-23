"""Event-driven backtester.

It runs the same brain -> risk guard -> broker path as the live bot, one closed candle
at a time: decide on bar t's close, fill at bar t+1's open, pay fees and slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind
from .brain import QuantumBrain
from .broker import PaperBroker, Trade
from .config import Config, bars_per_year
from .risk import RiskGuard, scale_exposure


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    trades: list[Trade] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def run_backtest(df: pd.DataFrame, brain: QuantumBrain, cfg: Config, capital: float, start: int = 0,
                 symbol: str = "SYMBOL") -> BacktestResult:
    """Trade df[start:]; bars before `start` only warm up the indicators."""
    ppy = bars_per_year(cfg.timeframe)
    target = scale_exposure(df, brain.exposure(df), cfg.risk, ppy).to_numpy()
    atr = ind.atr(df, cfg.risk.atr_period).to_numpy()
    opens, closes, index = df["open"].to_numpy(), df["close"].to_numpy(), df.index

    broker = PaperBroker(symbol, capital, cfg.fee_rate, cfg.slippage_bps)
    guard = RiskGuard(cfg.risk, capital)
    equity, exposure_time = [], 0
    for i in range(start, len(df)):
        price = closes[i]
        eq = broker.equity(price)
        equity.append(eq)
        if broker.qty > 0:
            exposure_time += 1
        if i == len(df) - 1:
            break
        desired, reason = guard.decide(index[i], price, atr[i], eq, target[i], broker.exposure(price))
        broker.rebalance(desired, opens[i + 1], index[i + 1], cfg.risk, reason)

    equity = pd.Series(equity, index=index[start:], name="equity")
    benchmark = capital * (1 - cfg.fee_rate) * df["close"].iloc[start:] / df["close"].iloc[start]
    result = BacktestResult(equity, benchmark.rename("buy_and_hold"), broker.trades)
    result.stats = summarize(equity, benchmark, broker, ppy, exposure_time)
    return result


def max_drawdown(equity: pd.Series) -> float:
    return float((1 - equity / equity.cummax()).max()) if len(equity) else 0.0


def sharpe(returns: pd.Series, periods_per_year: float) -> float:
    std = returns.std()
    return float(returns.mean() / std * np.sqrt(periods_per_year)) if std and std > 0 else 0.0


def summarize(equity: pd.Series, benchmark: pd.Series, broker: PaperBroker, ppy: float, exposure_time: int) -> dict:
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    wins = [p for p in broker.round_trips if p > 0]
    days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400 if len(equity) > 1 else 0
    return {
        "days": round(days, 1),
        "start_equity": round(start, 2),
        "final_equity": round(end, 2),
        "total_return_pct": round((end / start - 1) * 100, 2),
        "buy_hold_return_pct": round((float(benchmark.iloc[-1]) / start - 1) * 100, 2),
        "sharpe": round(sharpe(equity.pct_change().dropna(), ppy), 2),
        "max_drawdown_pct": round(max_drawdown(equity) * 100, 2),
        "buy_hold_max_drawdown_pct": round(max_drawdown(benchmark) * 100, 2),
        "trades": len(broker.trades),
        "round_trips": len(broker.round_trips),
        "win_rate_pct": round(100 * len(wins) / len(broker.round_trips), 1) if broker.round_trips else 0.0,
        "fees_paid": round(broker.fees_paid, 2),
        "time_in_market_pct": round(100 * exposure_time / len(equity), 1),
    }
