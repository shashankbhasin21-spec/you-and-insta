"""Brokers. Each traded symbol gets its own sleeve: a virtual ledger of cash and coin.

PaperBroker simulates fills with fees and slippage (the default). CcxtBroker sends real
market orders through ccxt, then books the exchange's actual fill into the same ledger,
so several symbols can share one exchange account without stepping on each other.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass

from .config import RiskConfig

log = logging.getLogger("qbrain")


@dataclass
class Trade:
    time: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    reason: str


class Ledger:
    def __init__(self, symbol: str, cash: float, qty: float = 0.0, fee_rate: float = 0.004):
        self.symbol = symbol
        self.cash = float(cash)
        self.qty = float(qty)
        self.fee_rate = fee_rate
        self.fees_paid = 0.0
        self.trades: list[Trade] = []
        self.round_trips: list[float] = []
        self._round_cost = 0.0
        self._round_proceeds = 0.0

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def exposure(self, price: float) -> float:
        eq = self.equity(price)
        return self.qty * price / eq if eq > 0 else 0.0

    def plan(self, target: float, price: float, risk: RiskConfig) -> tuple[str, float] | None:
        """Decide the side and quote-currency size needed to move toward `target`."""
        eq = self.equity(price)
        if eq <= 0 or price <= 0:
            return None
        current = self.exposure(price)
        closing = target <= 0 and self.qty > 0
        if not closing and abs(target - current) < risk.rebalance_band:
            return None
        value = (target - current) * eq
        if value > 0:
            value = min(value, self.cash / (1 + self.fee_rate))
            return ("buy", value) if value > 0 and value >= risk.min_order_usd else None
        value = -value
        if value <= 0 or self.qty <= 0:
            return None
        if closing or self.qty * price - value < risk.min_order_usd:
            return "sell", self.qty * price  # close fully rather than leave unsellable dust
        return ("sell", value) if value >= risk.min_order_usd else None

    def apply_fill(self, time, side: str, qty: float, price: float, fee: float, reason: str) -> Trade:
        notional = qty * price
        if side == "buy":
            self.cash -= notional + fee
            self.qty += qty
            self._round_cost += notional + fee
        else:
            qty = min(qty, self.qty)
            notional = qty * price
            self.cash += notional - fee
            self.qty -= qty
            self._round_proceeds += notional - fee
            if self.qty <= 1e-12:
                self.qty = 0.0
                self.round_trips.append(self._round_proceeds - self._round_cost)
                self._round_cost = self._round_proceeds = 0.0
        if abs(self.cash) < 1e-9:
            self.cash = 0.0
        self.fees_paid += fee
        trade = Trade(str(time), self.symbol, side, qty, price, fee, reason)
        self.trades.append(trade)
        return trade

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "cash": self.cash,
            "qty": self.qty,
            "fees_paid": self.fees_paid,
            "round_trips": self.round_trips,
            "round_cost": self._round_cost,
            "round_proceeds": self._round_proceeds,
            "trades": [asdict(t) for t in self.trades],
        }

    def load_state(self, data: dict) -> None:
        self.cash = data["cash"]
        self.qty = data["qty"]
        self.fees_paid = data.get("fees_paid", 0.0)
        self.round_trips = data.get("round_trips", [])
        self._round_cost = data.get("round_cost", 0.0)
        self._round_proceeds = data.get("round_proceeds", 0.0)
        self.trades = [Trade(**t) for t in data.get("trades", [])]


class PaperBroker(Ledger):
    def __init__(self, symbol: str, cash: float, fee_rate: float = 0.004, slippage_bps: float = 5.0):
        super().__init__(symbol, cash, fee_rate=fee_rate)
        self.slippage = slippage_bps / 10_000

    def rebalance(self, target: float, price: float, time, risk: RiskConfig, reason: str = "signal") -> Trade | None:
        order = self.plan(target, price, risk)
        if order is None:
            return None
        side, value = order
        if side == "buy":
            fill = price * (1 + self.slippage)
            qty = value / fill
        else:
            fill = price * (1 - self.slippage)
            qty = min(self.qty, value / price)
        return self.apply_fill(time, side, qty, fill, qty * fill * self.fee_rate, reason)


class CcxtBroker(Ledger):
    """Real orders. Only built by the live runner after explicit opt-in."""

    def __init__(self, exchange, symbol: str, cash: float, fee_rate: float = 0.004):
        super().__init__(symbol, cash, fee_rate=fee_rate)
        self.exchange = exchange

    def rebalance(self, target: float, price: float, time, risk: RiskConfig, reason: str = "signal") -> Trade | None:
        order = self.plan(target, price, risk)
        if order is None:
            return None
        side, value = order
        market = self.exchange.market(self.symbol)
        base, quote = market["base"], market["quote"]
        free = self.exchange.fetch_balance().get("free") or {}
        if side == "buy":
            qty = value / price
            if value * (1 + self.fee_rate) > (free.get(quote) or 0.0):
                log.warning("%s: need %.2f %s but only %.2f is free, skipping", self.symbol, value, quote, free.get(quote) or 0.0)
                return None
        else:
            qty = min(self.qty, value / price, free.get(base) or 0.0)
        amount = float(self.exchange.amount_to_precision(self.symbol, qty))
        limits = market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min") or 0.0
        min_cost = (limits.get("cost") or {}).get("min") or 0.0
        if amount <= 0 or amount < min_amount or amount * price < min_cost:
            log.info("%s: %s %.8f is below the exchange minimum, skipping", self.symbol, side, amount)
            return None

        placed = self.exchange.create_order(self.symbol, "market", side, amount)
        if placed.get("filled") is None or placed.get("average") is None:
            try:
                placed = self.exchange.fetch_order(placed["id"], self.symbol)
            except Exception as exc:  # some exchanges cannot look up fresh market orders
                log.warning("%s: could not fetch order %s: %s", self.symbol, placed.get("id"), exc)
        filled = float(placed.get("filled") if placed.get("filled") is not None else amount)
        if filled <= 0:
            log.warning("%s: %s order %s did not fill", self.symbol, side, placed.get("id"))
            return None
        avg = float(placed.get("average") or placed.get("price") or price)

        fee_info = placed.get("fee") or {}
        fee_cost = fee_info.get("cost")
        fee_cost = float(fee_cost) if fee_cost is not None and math.isfinite(float(fee_cost)) else None
        if fee_cost is not None and fee_info.get("currency") == quote:
            fee = fee_cost
        elif fee_cost is not None and fee_info.get("currency") == base and side == "buy":
            filled -= fee_cost          # the fee came out of the coins we received
            fee = fee_cost * avg
        else:
            fee = filled * avg * self.fee_rate
        log.info("%s: %s %.8f @ %.2f (fee %.4f %s) [%s]", self.symbol, side, filled, avg, fee, quote, reason)
        return self.apply_fill(time, side, filled, avg, fee, reason)
