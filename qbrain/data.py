"""Market data: exchange candles via ccxt, plus a synthetic generator for offline runs."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import timeframe_seconds

log = logging.getLogger("qbrain")
COLUMNS = ["open", "high", "low", "close", "volume"]


def make_exchange(exchange_id: str, with_keys: bool = False):
    import ccxt

    options = {"enableRateLimit": True, "timeout": 30_000}
    # ccxt ignores proxy environment variables unless told about them.
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        options["httpsProxy"] = proxy
    if with_keys:
        options["apiKey"] = os.environ["QBRAIN_API_KEY"]
        options["secret"] = os.environ["QBRAIN_API_SECRET"]
        if os.environ.get("QBRAIN_API_PASSWORD"):
            options["password"] = os.environ["QBRAIN_API_PASSWORD"]
    if not hasattr(ccxt, exchange_id):
        raise ValueError(f"ccxt does not know exchange {exchange_id!r}")
    return getattr(ccxt, exchange_id)(options)


def to_frame(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["time", *COLUMNS])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.drop_duplicates("time").set_index("time").sort_index()
    return df.astype(float)


def closed_only(df: pd.DataFrame, timeframe: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop the candle that is still forming; decisions use closed bars only."""
    now = now or pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(seconds=timeframe_seconds(timeframe)) <= now]


def fetch_history(exchange, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    step_ms = timeframe_seconds(timeframe) * 1000
    since = exchange.milliseconds() - (bars + 1) * step_ms  # +1: the forming candle is dropped
    rows: list[list[float]] = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        next_since = batch[-1][0] + step_ms
        if next_since <= since or next_since > exchange.milliseconds():
            break
        since = next_since
    if not rows:
        raise RuntimeError(f"{exchange.id} returned no candles for {symbol} {timeframe}")
    df = closed_only(to_frame(rows), timeframe)
    log.info("%s %s %s: %d candles %s -> %s", exchange.id, symbol, timeframe, len(df), df.index[0], df.index[-1])
    return df.iloc[-bars:]


def cached_history(exchange_id: str, symbol: str, timeframe: str, bars: int, cache_dir: str | Path) -> pd.DataFrame:
    """fetch_history with a CSV cache that is refreshed once the next candle has closed."""
    path = Path(cache_dir) / f"{exchange_id}_{symbol.replace('/', '-')}_{timeframe}.csv"
    if path.exists():
        df = pd.read_csv(path, index_col="time", parse_dates=["time"])
        fresh = df.index[-1] + pd.Timedelta(seconds=2 * timeframe_seconds(timeframe)) > pd.Timestamp.now(tz="UTC")
        if fresh and len(df) >= bars:
            return df.iloc[-bars:]
    df = fetch_history(make_exchange(exchange_id), symbol, timeframe, bars)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="time")
    return df


def synthetic_ohlcv(bars: int = 3000, timeframe: str = "4h", seed: int = 1, start_price: float = 30_000.0) -> pd.DataFrame:
    """Regime-switching random walk (bull, bear and choppy phases) for offline tests.

    It is NOT a forecast of anything; it only gives the agents different kinds of
    markets to be tested against without touching the network.
    """
    rng = np.random.default_rng(seed)
    regimes = {"bull": (0.0012, 0.015), "bear": (-0.0010, 0.020), "chop": (0.0, 0.012)}
    names = list(regimes)
    log_price = np.empty(bars)
    level = np.log(start_price)
    anchor = level
    regime = "chop"
    for i in range(bars):
        if rng.random() < 1 / 150:
            regime = names[rng.integers(len(names))]
            anchor = level
        mu, sigma = regimes[regime]
        pull = -0.05 * (level - anchor) if regime == "chop" else 0.0
        level += mu + pull + sigma * rng.standard_normal()
        log_price[i] = level
    close = np.exp(log_price)
    open_ = np.concatenate([[start_price], close[:-1]])
    wiggle = np.abs(rng.standard_normal((2, bars))) * 0.006
    high = np.maximum(open_, close) * (1 + wiggle[0])
    low = np.minimum(open_, close) * (1 - wiggle[1])
    volume = rng.lognormal(3, 0.5, bars)
    step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    index = pd.date_range(end=pd.Timestamp("2026-01-01", tz="UTC"), periods=bars, freq=step, name="time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index)


def sleep_until_next_bar(timeframe: str, grace_seconds: int = 20) -> None:
    period = timeframe_seconds(timeframe)
    now = time.time()
    time.sleep(period - now % period + grace_seconds)
