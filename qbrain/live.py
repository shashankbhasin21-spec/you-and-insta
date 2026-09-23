"""The 24/7 runner.

Every time a candle closes it asks each symbol's brain for a target exposure, lets the
risk guard veto or shrink it, rebalances through the broker and saves state. Once a day
(by default) it re-trains each brain on the latest history, so it keeps adapting.

Paper trading (real prices, simulated fills) is the default. Real orders need all of:
  * the --real-money flag,
  * QBRAIN_API_KEY and QBRAIN_API_SECRET (trade-only keys, withdrawals disabled),
  * QBRAIN_I_ACCEPT_THE_RISK=yes.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from . import indicators as ind
from .brain import QuantumBrain
from .broker import CcxtBroker, Ledger, PaperBroker, Trade
from .config import Config, bars_per_year
from .data import cached_history, fetch_history, make_exchange, sleep_until_next_bar
from .optimize import optimize_brain
from .risk import RiskGuard, scale_exposure

log = logging.getLogger("qbrain")
RISK_FLAG = "QBRAIN_I_ACCEPT_THE_RISK"


def require_real_money_opt_in() -> None:
    missing = [k for k in ("QBRAIN_API_KEY", "QBRAIN_API_SECRET") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"real-money mode needs {', '.join(missing)} in the environment")
    if os.environ.get(RISK_FLAG) != "yes":
        raise SystemExit(
            f"real-money mode can lose your whole balance. Set {RISK_FLAG}=yes once you have "
            "paper-traded for a while and accept that."
        )


def _slug(symbol: str) -> str:
    return symbol.replace("/", "-").replace(":", "-")


def model_path(cfg: Config, symbol: str) -> Path:
    return Path(cfg.state_dir) / "models" / f"{_slug(symbol)}.json"


def load_model(cfg: Config, symbol: str) -> tuple[QuantumBrain, float] | None:
    path = model_path(cfg, symbol)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return QuantumBrain.from_dict(data["brain"]), data["trained_at"]


def train_model(cfg: Config, symbol: str) -> tuple[QuantumBrain, float]:
    """Optimise a brain on the longest history available and save it."""
    cache = Path(cfg.state_dir) / "data"
    try:
        df = cached_history(cfg.data_exchange, symbol, cfg.timeframe, cfg.history_bars, cache)
    except Exception as exc:
        log.warning("%s history from %s failed (%s); using %s", symbol, cfg.data_exchange, exc, cfg.exchange)
        df = cached_history(cfg.exchange, symbol, cfg.timeframe, cfg.history_bars, cache)
    brain, result = optimize_brain(df, cfg)
    trained_at = time.time()
    path = model_path(cfg, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "symbol": symbol,
        "trained_at": trained_at,
        "bars": len(df),
        "from": str(df.index[0]),
        "to": str(df.index[-1]),
        "fitness": result.best_fitness,
        "brain": brain.to_dict(),
    }, indent=2))
    return brain, trained_at


@dataclass
class Sleeve:
    symbol: str
    broker: Ledger
    guard: RiskGuard
    brain: QuantumBrain
    trained_at: float
    last_bar: str | None = None


class LiveRunner:
    def __init__(self, cfg: Config, real_money: bool = False):
        if real_money:
            require_real_money_opt_in()
        self.cfg = cfg
        self.mode = "live" if real_money else "paper"
        self.state_path = Path(cfg.state_dir) / f"{self.mode}_state.json"
        self.trades_path = Path(cfg.state_dir) / f"{self.mode}_trades.csv"
        self.exchange = make_exchange(cfg.exchange, with_keys=real_money)
        self.exchange.load_markets()

        saved = json.loads(self.state_path.read_text()).get("sleeves", {}) if self.state_path.exists() else {}
        capital_each = cfg.starting_capital / len(cfg.symbols)
        self.sleeves: list[Sleeve] = []
        for symbol in cfg.symbols:
            if symbol not in self.exchange.markets:
                raise SystemExit(f"{cfg.exchange} has no market {symbol}")
            if real_money:
                broker: Ledger = CcxtBroker(self.exchange, symbol, capital_each, cfg.fee_rate)
            else:
                broker = PaperBroker(symbol, capital_each, cfg.fee_rate, cfg.slippage_bps)
            guard = RiskGuard(cfg.risk, capital_each)
            last_bar = None
            if symbol in saved:
                broker.load_state(saved[symbol]["ledger"])
                guard = RiskGuard.from_dict(cfg.risk, saved[symbol]["guard"])
                last_bar = saved[symbol].get("last_bar")
            model = load_model(cfg, symbol) or train_model(cfg, symbol)
            self.sleeves.append(Sleeve(symbol, broker, guard, model[0], model[1], last_bar))
        log.info("%s mode on %s, %s, %d sleeves", self.mode, cfg.exchange, cfg.timeframe, len(self.sleeves))

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "mode": self.mode,
            "exchange": self.cfg.exchange,
            "updated": pd.Timestamp.now(tz="UTC").isoformat(),
            "sleeves": {
                s.symbol: {"ledger": s.broker.to_dict(), "guard": s.guard.to_dict(), "last_bar": s.last_bar}
                for s in self.sleeves
            },
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.state_path)

    def _record(self, trade: Trade) -> None:
        new = not self.trades_path.exists()
        with self.trades_path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(asdict(trade)))
            if new:
                writer.writeheader()
            writer.writerow(asdict(trade))

    def step(self, sleeve: Sleeve) -> None:
        cfg = self.cfg
        if time.time() - sleeve.trained_at > cfg.reoptimize_hours * 3600:
            log.info("%s: re-training the brain on fresh history", sleeve.symbol)
            sleeve.brain, sleeve.trained_at = train_model(cfg, sleeve.symbol)

        df = fetch_history(self.exchange, sleeve.symbol, cfg.timeframe, cfg.live_window_bars)
        bar = str(df.index[-1])
        if bar == sleeve.last_bar:
            return
        target = float(scale_exposure(df, sleeve.brain.exposure(df), cfg.risk, bars_per_year(cfg.timeframe)).iloc[-1])
        price = float(self.exchange.fetch_ticker(sleeve.symbol).get("last") or df["close"].iloc[-1])
        atr = float(ind.atr(df, cfg.risk.atr_period).iloc[-1])
        broker = sleeve.broker
        equity = broker.equity(price)
        desired, reason = sleeve.guard.decide(df.index[-1], price, atr, equity, target, broker.exposure(price))
        trade = broker.rebalance(desired, price, pd.Timestamp.now(tz="UTC"), cfg.risk, reason)
        sleeve.last_bar = bar
        log.info(
            "%s bar %s price %.2f | brain %.2f -> risk %.2f (%s) | exposure %.2f | equity %.2f | votes %s",
            sleeve.symbol, bar, price, target, desired, reason, broker.exposure(price), equity,
            sleeve.brain.explain(df),
        )
        if trade:
            self._record(trade)

    def run(self, once: bool = False) -> None:
        while True:
            for sleeve in self.sleeves:
                try:
                    self.step(sleeve)
                except Exception:
                    log.exception("%s: step failed; will retry next bar", sleeve.symbol)
            self.save()
            if once:
                return
            sleep_until_next_bar(self.cfg.timeframe)


def status(cfg: Config, real_money: bool = False, prices: dict[str, float] | None = None) -> list[dict]:
    mode = "live" if real_money else "paper"
    path = Path(cfg.state_dir) / f"{mode}_state.json"
    if not path.exists():
        raise SystemExit(f"no {mode} state yet at {path}; start the bot with `qbrain run` first")
    sleeves = json.loads(path.read_text())["sleeves"]
    rows = []
    for symbol, data in sleeves.items():
        ledger = Ledger(symbol, 0.0)
        ledger.load_state(data["ledger"])
        price = (prices or {}).get(symbol)
        rows.append({
            "symbol": symbol,
            "cash": round(ledger.cash, 2),
            "coin": round(ledger.qty, 8),
            "equity": round(ledger.equity(price), 2) if price else None,
            "fees_paid": round(ledger.fees_paid, 2),
            "trades": len(ledger.trades),
            "closed_trades_pnl": round(sum(ledger.round_trips), 2),
            "halted": data["guard"].get("halted", False),
        })
    return rows
