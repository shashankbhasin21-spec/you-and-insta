import pandas as pd
import pytest

from qbrain.broker import CcxtBroker, Ledger, PaperBroker
from qbrain.config import RiskConfig
from qbrain.risk import RiskGuard, scale_exposure

RISK = RiskConfig(rebalance_band=0.15, min_order_usd=5.0)
T = pd.Timestamp("2026-01-01", tz="UTC")


def test_paper_round_trip_costs_exactly_fees_and_slippage():
    b = PaperBroker("BTC/USD", 100.0, fee_rate=0.004, slippage_bps=10)
    b.rebalance(0.9, 100.0, T, RISK)
    assert b.cash >= 0
    coin_value = 90 / 100.1 * 100
    assert b.exposure(100.0) == pytest.approx(coin_value / (100 - 90 * 1.004 + coin_value))
    b.rebalance(0.0, 100.0, T, RISK)
    assert b.qty == 0
    buy_value = 90.0
    expected = 100 - buy_value * 0.004 - (buy_value / 100.1) * 99.9 * 0.004 - (buy_value - buy_value / 100.1 * 99.9)
    assert b.cash == pytest.approx(expected, rel=1e-9)
    assert len(b.round_trips) == 1 and b.round_trips[0] < 0
    assert b.fees_paid == pytest.approx(sum(t.fee for t in b.trades))


def test_band_skips_small_moves_but_never_blocks_an_exit():
    b = PaperBroker("BTC/USD", 100.0, fee_rate=0.0, slippage_bps=0)
    b.rebalance(0.5, 100.0, T, RISK)
    assert b.rebalance(0.6, 100.0, T, RISK) is None      # 10% move < 15% band
    b2 = PaperBroker("BTC/USD", 100.0, fee_rate=0.0, slippage_bps=0)
    b2.rebalance(0.1 + 0.15, 100.0, T, RISK)
    assert b2.rebalance(0.0, 100.0, T, RISK) is not None  # exits always go through
    assert b2.qty == 0


def test_min_order_and_dust():
    b = PaperBroker("BTC/USD", 20.0, fee_rate=0.0, slippage_bps=0)
    assert b.rebalance(0.2, 100.0, T, RISK) is None       # $4 order is below the $5 minimum
    b.rebalance(0.9, 100.0, T, RISK)
    b.rebalance(0.2, 100.0, T, RISK)                      # would leave $4 of coin: sell it all
    assert b.qty == 0


def test_never_spends_more_cash_than_it_has():
    b = PaperBroker("BTC/USD", 50.0, fee_rate=0.004, slippage_bps=5)
    b.rebalance(1.0, 100.0, T, RISK)
    assert b.cash >= 0


def test_ledger_state_round_trip():
    b = PaperBroker("BTC/USD", 100.0)
    b.rebalance(0.8, 100.0, T, RISK)
    clone = Ledger("BTC/USD", 0.0)
    clone.load_state(b.to_dict())
    assert clone.equity(123.0) == pytest.approx(b.equity(123.0))
    assert len(clone.trades) == 1


def test_ccxt_broker_books_the_exchange_fill(fake_exchange_cls):
    ex = fake_exchange_cls()
    b = CcxtBroker(ex, "BTC/USD", 100.0, fee_rate=0.004)
    trade = b.rebalance(0.5, 100.0, T, RISK)
    assert ex.orders == [("buy", 0.5)]
    assert trade.fee == pytest.approx(0.5 * 100 * 0.001)  # fee reported by the exchange
    assert b.qty == pytest.approx(0.5)
    b.rebalance(0.0, 100.0, T, RISK)
    assert ex.orders[-1] == ("sell", 0.5) and b.qty == 0


def test_ccxt_broker_books_fees_charged_in_coin(fake_exchange_cls):
    ex = fake_exchange_cls()
    ex.fee_currency = "BTC"
    b = CcxtBroker(ex, "BTC/USD", 100.0)
    b.rebalance(0.5, 100.0, T, RISK)
    assert b.qty == pytest.approx(0.5 * 0.999)       # coins actually received
    assert b.cash == pytest.approx(100.0 - 0.5 * 100)  # cash actually paid


def test_ccxt_broker_ignores_unfilled_orders_and_caps_sells(fake_exchange_cls):
    ex = fake_exchange_cls()
    ex.fill_ratio = 0.0
    b = CcxtBroker(ex, "BTC/USD", 100.0)
    assert b.rebalance(0.5, 100.0, T, RISK) is None
    assert b.qty == 0 and not b.trades
    ex.fill_ratio = 1.0
    b.rebalance(0.5, 100.0, T, RISK)
    ex.free["BTC"] = 0.2                                # less coin on the exchange than booked
    b.rebalance(0.0, 100.0, T, RISK)
    assert ex.orders[-1] == ("sell", 0.2)


def test_ccxt_broker_respects_free_balance(fake_exchange_cls):
    ex = fake_exchange_cls()
    ex.free = {"USD": 10.0}
    b = CcxtBroker(ex, "BTC/USD", 100.0)
    assert b.rebalance(0.9, 100.0, T, RISK) is None
    assert ex.orders == []


def test_kill_switch_is_permanent():
    g = RiskGuard(RiskConfig(max_drawdown=0.25), 100.0)
    assert g.decide(T, 10, 1, 100.0, 0.8, 0.0) == (0.8, "signal")
    assert g.decide(T, 10, 1, 74.0, 0.8, 0.0)[1] == "kill-switch"
    assert g.decide(T + pd.Timedelta(days=5), 10, 1, 200.0, 0.8, 0.0)[1] == "kill-switch"


def test_daily_loss_limit_resets_next_day():
    g = RiskGuard(RiskConfig(daily_loss_limit=0.06, max_drawdown=0.9), 100.0)
    g.decide(T, 10, 1, 100.0, 0.5, 0.0)
    assert g.decide(T + pd.Timedelta(hours=8), 10, 1, 93.0, 0.5, 0.0)[1] == "daily-loss-limit"
    assert g.decide(T + pd.Timedelta(days=1), 10, 1, 93.0, 0.5, 0.0)[1] == "signal"


def test_trailing_stop_then_cooldown():
    g = RiskGuard(RiskConfig(atr_stop_mult=3, stop_cooldown_bars=2, max_drawdown=0.9, daily_loss_limit=0.9), 100.0)
    assert g.decide(T, 110, 2, 100, 1, 0.9)[1] == "signal"
    assert g.decide(T, 103, 2, 100, 1, 0.9)[1] == "trailing-stop"   # 103 < 110 - 3 * 2
    assert g.decide(T, 104, 2, 100, 1, 0.0)[1] == "cooldown"
    assert g.decide(T, 104, 2, 100, 1, 0.0)[1] == "cooldown"
    assert g.decide(T, 104, 2, 100, 1, 0.0)[1] == "signal"


def test_guard_state_round_trip():
    g = RiskGuard(RiskConfig(), 100.0)
    g.decide(T, 10, 1, 120.0, 0.5, 0.5)
    clone = RiskGuard.from_dict(RiskConfig(), g.to_dict())
    assert clone.to_dict() == g.to_dict()


def test_scale_exposure_caps(candles):
    cfg = RiskConfig(max_exposure=0.7, target_vol=100.0)
    out = scale_exposure(candles, pd.Series(1.0, index=candles.index), cfg, 2190)
    assert out.max() == pytest.approx(0.7)
    assert out.min() >= 0
