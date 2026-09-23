import pandas as pd
import pytest

from qbrain.config import Config, OptimizerConfig
from qbrain.data import synthetic_ohlcv


@pytest.fixture(scope="session")
def candles() -> pd.DataFrame:
    return synthetic_ohlcv(1200, "4h", seed=3)


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        state_dir=str(tmp_path / "state"),
        optimizer=OptimizerConfig(population=6, generations=3, seed=1, min_trades=2),
    )


class FakeExchange:
    """Just enough of the ccxt interface for the brokers and the live runner."""

    id = "fake"

    def __init__(self, df: pd.DataFrame | None = None, symbol: str = "BTC/USD"):
        self.df = df
        self.markets = {
            symbol: {
                "symbol": symbol, "base": symbol.split("/")[0], "quote": symbol.split("/")[1],
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5.0}},
            }
        }
        self.orders = []
        self.free = {"USD": 1000.0, "BTC": 10.0}
        self.fee_currency = "USD"
        self.fill_ratio = 1.0

    def load_markets(self):
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def milliseconds(self):
        return int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)

    def amount_to_precision(self, symbol, amount):
        return f"{int(amount * 1e5) / 1e5:.5f}"

    def fetch_balance(self):
        return {"free": self.free}

    def fetch_ticker(self, symbol):
        return {"last": float(self.df["close"].iloc[-1]) if self.df is not None else 100.0}

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        rows = [[int(t.timestamp() * 1000), *r] for t, r in zip(self.df.index, self.df.to_numpy().tolist())]
        return [r for r in rows if since is None or r[0] >= since][:limit]

    def create_order(self, symbol, type_, side, amount):
        price = self.fetch_ticker(symbol)["last"]
        amount = float(amount)
        filled = amount * self.fill_ratio
        fee = filled * 0.001 if self.fee_currency == "BTC" else filled * price * 0.001
        order = {"id": str(len(self.orders)), "filled": filled, "average": price,
                 "fee": {"cost": fee, "currency": self.fee_currency}}
        self.orders.append((side, amount))
        return order


@pytest.fixture
def fake_exchange_cls():
    return FakeExchange
