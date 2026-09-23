"""Configuration: plain dataclasses loaded from an optional JSON file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class RiskConfig:
    max_exposure: float = 0.95        # never put more than 95% of a sleeve in the coin
    rebalance_band: float = 0.15      # skip trades that move exposure by less than this
    min_order_usd: float = 5.0        # most exchanges reject orders below ~$5-10
    target_vol: float = 0.60          # annualised; exposure shrinks when the market is wilder
    vol_lookback: int = 60
    atr_period: int = 14
    atr_stop_mult: float = 3.0        # trailing stop distance, in ATRs below the high
    stop_cooldown_bars: int = 6       # bars to stay flat after a stop fires
    max_drawdown: float = 0.25        # kill switch: halt after a 25% fall from the peak
    daily_loss_limit: float = 0.06    # go flat for the rest of the UTC day after -6%


@dataclass
class OptimizerConfig:
    population: int = 16
    generations: int = 30
    bits_per_param: int = 6
    seed: int = 7
    train_fraction: float = 0.7       # walk-forward: optimise on the first 70%, test on the rest
    min_trades: int = 6


@dataclass
class Config:
    exchange: str = "kraken"          # where orders go (and live candles come from)
    data_exchange: str = "bitstamp"   # where long backtest history comes from
    symbols: list[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    timeframe: str = "4h"
    starting_capital: float = 50.0
    fee_rate: float = 0.004           # Kraken taker fee at low volume; ~0.001 on Binance/OKX
    slippage_bps: float = 5.0
    history_bars: int = 3000
    live_window_bars: int = 500
    state_dir: str = "state"
    reoptimize_hours: float = 24.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if path is None or not Path(path).exists():
            return cls()
        raw = json.loads(Path(path).read_text())
        risk = RiskConfig(**_known(RiskConfig, raw.pop("risk", {})))
        opt = OptimizerConfig(**_known(OptimizerConfig, raw.pop("optimizer", {})))
        return cls(**_known(cls, raw), risk=risk, optimizer=opt)

    def to_dict(self) -> dict:
        return asdict(self)


def _known(klass, data: dict) -> dict:
    """Drop unknown keys so an old config file never crashes a newer bot."""
    names = {f.name for f in fields(klass)}
    return {k: v for k, v in data.items() if k in names}


TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
}


def timeframe_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError:
        raise ValueError(f"unsupported timeframe {timeframe!r}; use one of {list(TIMEFRAME_SECONDS)}")


def bars_per_year(timeframe: str) -> float:
    return 365 * 86400 / timeframe_seconds(timeframe)
