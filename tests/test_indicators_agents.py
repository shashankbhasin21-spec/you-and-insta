import numpy as np
import pandas as pd
import pytest

from qbrain import indicators as ind
from qbrain.agents import default_agents
from qbrain.brain import QuantumBrain
from qbrain.config import RiskConfig, bars_per_year
from qbrain.risk import scale_exposure


def test_rsi_bounds_and_extremes(candles):
    r = ind.rsi(candles["close"], 14).dropna()
    assert r.between(0, 100).all()
    rising = pd.Series(np.arange(1.0, 60.0))
    assert ind.rsi(rising, 14).iloc[-1] == 100.0


def test_atr_positive_and_ema_of_constant(candles):
    assert (ind.atr(candles, 14).dropna() > 0).all()
    flat = pd.Series([5.0] * 50)
    assert np.allclose(ind.ema(flat, 10).dropna(), 5.0)


@pytest.mark.parametrize("agent", default_agents(), ids=lambda a: a.name)
def test_agent_signals_are_bounded_exposures(agent, candles):
    s = agent.signal(candles, agent.defaults)
    assert len(s) == len(candles)
    assert not s.isna().any()
    assert s.between(0, 1).all()


def _random_brain(seed: int) -> QuantumBrain:
    rng = np.random.default_rng(seed)
    space = QuantumBrain.search_space()
    return QuantumBrain.from_flat({p.name: p.clip(rng.uniform(p.low, p.high)) for p in space})


@pytest.mark.parametrize("brain", [QuantumBrain(), _random_brain(1), _random_brain(2)])
def test_no_look_ahead(brain, candles):
    """The decision at bar t must not change when future bars are appended."""
    ppy = bars_per_year("4h")
    full = scale_exposure(candles, brain.exposure(candles), RiskConfig(), ppy)
    for cut in (300, 650, 1000):
        part = candles.iloc[:cut]
        partial = scale_exposure(part, brain.exposure(part), RiskConfig(), ppy)
        pd.testing.assert_series_equal(partial, full.iloc[:cut], check_names=False)


def test_born_rule_prior():
    brain = QuantumBrain(amplitudes={"trend": 1.0, "mean_reversion": 1.0, "breakout": 0.0, "momentum": 0.0})
    prior = brain.prior()
    assert prior.sum() == pytest.approx(1.0)
    assert prior["trend"] == pytest.approx(0.5)
    assert prior["breakout"] == 0.0
    zero = QuantumBrain(amplitudes={a.name: 0.0 for a in default_agents()}).prior()
    assert np.allclose(zero, 0.25)


def test_weights_are_distributions(candles):
    brain = QuantumBrain(learning_rate=20)
    w = brain.weights(candles, brain.agent_signals(candles))
    assert np.allclose(w.sum(axis=1), 1.0)
    assert (w >= 0).all().all()


def test_brain_serialization_round_trip(candles):
    brain = _random_brain(5)
    clone = QuantumBrain.from_dict(brain.to_dict())
    pd.testing.assert_series_equal(brain.exposure(candles), clone.exposure(candles))
