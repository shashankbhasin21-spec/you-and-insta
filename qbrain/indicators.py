"""Technical indicators. Every function is causal: bar t only uses data up to bar t."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI in [0, 100]."""
    delta = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average true range (Wilder smoothing)."""
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def zscore(series: pd.Series, n: int) -> pd.Series:
    mean = series.rolling(n, min_periods=n).mean()
    std = series.rolling(n, min_periods=n).std()
    return (series - mean) / std.replace(0, np.nan)


def prior_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the n bars *before* the current one (Donchian upper band)."""
    return high.rolling(n, min_periods=n).max().shift(1)


def prior_low(low: pd.Series, n: int) -> pd.Series:
    return low.rolling(n, min_periods=n).min().shift(1)


def realized_vol(close: pd.Series, n: int, periods_per_year: float) -> pd.Series:
    """Annualised volatility of log returns over the last n bars."""
    log_ret = np.log(close).diff()
    return log_ret.rolling(n, min_periods=max(5, n // 2)).std() * np.sqrt(periods_per_year)
