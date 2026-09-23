import json
from pathlib import Path

import pandas as pd
import pytest

from qbrain import live, mining
from qbrain.backtest import run_backtest
from qbrain.brain import QuantumBrain
from qbrain.config import Config, RiskConfig
from qbrain.data import synthetic_ohlcv
from qbrain.optimize import apply_band, walk_forward


class AlwaysIn:
    def exposure(self, df):
        return pd.Series(1.0, index=df.index)


def test_frictionless_always_in_equals_buy_and_hold(candles):
    cfg = Config(fee_rate=0.0, slippage_bps=0.0, risk=RiskConfig(
        max_exposure=1.0, rebalance_band=0.0, min_order_usd=0.0, target_vol=1e9,
        atr_stop_mult=1e9, max_drawdown=1.0, daily_loss_limit=1.0))
    start = 100
    result = run_backtest(candles, AlwaysIn(), cfg, 1000.0, start=start)
    entry = candles["open"].iloc[start + 1]
    assert result.equity.iloc[-1] == pytest.approx(1000.0 * candles["close"].iloc[-1] / entry)
    assert result.stats["trades"] == 1


def test_backtest_stats_and_fees(candles, cfg):
    result = run_backtest(candles, QuantumBrain(), cfg, 50.0, start=200)
    assert len(result.equity) == len(candles) - 200
    assert result.stats["fees_paid"] == pytest.approx(sum(t.fee for t in result.trades), abs=0.01)
    assert (result.equity > 0).all()


def test_apply_band():
    target = pd.Series([0.0, 0.3, 0.35, 0.5, 0.05, 0.0, 0.2])
    assert apply_band(target, 0.15).tolist() == [0.0, 0.3, 0.3, 0.5, 0.05, 0.0, 0.2]


def test_walk_forward_runs_offline(cfg):
    df = synthetic_ohlcv(900, "4h", seed=4)
    report = walk_forward(df, cfg, 25.0, "BTC/USD")
    assert report.test.equity.index[0] == report.split_time
    assert "total_return_pct" in report.test.stats


def test_mining_maths_is_consistent():
    d = mining.FALLBACK_DIFFICULTY
    assert mining.network_hashrate(d) / 1e18 == pytest.approx(950, rel=0.02)
    needed = mining.hashrate_for(0.1, d)
    assert mining.btc_per_day(needed, d) == pytest.approx(0.1)
    assert 190e15 < needed < 230e15            # ~210 PH/s for 1 BTC in 10 days
    net = mining.Network(d, 84_000, live=False)
    cpu = mining.rig_report(**{k: mining.RIGS["cpu"][k] for k in ("th", "watts")}, electricity_usd_kwh=0.1, net=net)
    assert cpu["usd_profit_per_day"] < 0
    assert cpu["days_to_one_btc"] > 365 * 1e6


def test_real_money_requires_explicit_opt_in(monkeypatch, cfg):
    for key in ("QBRAIN_API_KEY", "QBRAIN_API_SECRET", live.RISK_FLAG):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="QBRAIN_API_KEY"):
        live.LiveRunner(cfg, real_money=True)
    monkeypatch.setenv("QBRAIN_API_KEY", "k")
    monkeypatch.setenv("QBRAIN_API_SECRET", "s")
    with pytest.raises(SystemExit, match=live.RISK_FLAG):
        live.LiveRunner(cfg, real_money=True)


def test_paper_runner_trades_and_persists_state(monkeypatch, cfg, fake_exchange_cls):
    df = synthetic_ohlcv(600, "4h", seed=8)
    now = pd.Timestamp.now(tz="UTC").floor("4h")
    df.index = pd.date_range(end=now - pd.Timedelta(hours=4), periods=len(df), freq="4h", name="time")
    monkeypatch.setattr(live, "make_exchange", lambda *a, **k: fake_exchange_cls(df))
    cfg.symbols = ["BTC/USD"]
    path = live.model_path(cfg, "BTC/USD")
    path.parent.mkdir(parents=True)
    brain = QuantumBrain(amplitudes={"trend": 1, "mean_reversion": 0, "breakout": 1, "momentum": 1}, conviction=0.0)
    path.write_text(json.dumps({"trained_at": 1e18, "brain": brain.to_dict()}))

    runner = live.LiveRunner(cfg)
    runner.run(once=True)
    state = json.loads(Path(runner.state_path).read_text())
    assert state["mode"] == "paper"
    assert state["sleeves"]["BTC/USD"]["last_bar"] == str(df.index[-1])

    # Restarting resumes from the saved ledger and does not act twice on the same bar.
    again = live.LiveRunner(cfg)
    assert again.sleeves[0].broker.to_dict() == runner.sleeves[0].broker.to_dict()
    again.run(once=True)
    assert len(again.sleeves[0].broker.trades) == len(runner.sleeves[0].broker.trades)
