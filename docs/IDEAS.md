# Idea backlog

## 1. "Claude × Quant": Polymarket 5-minute bot with a live dashboard

*Saved 2026-10-03. Source: Instagram reel by @atsmatrix, "This trader used Claude to build a Quant Bot and …"*

### What the reel shows

An agent called "Hunter 05" trades Polymarket's 5-minute BTC/ETH/SOL up/down markets
on Polygon, using Chainlink as the price oracle (400 ms updates). The headline claims:
$563,720 all-time P&L, 86,056 trades, a 50.0% win rate, average win/loss 3.92,
Sharpe 4.21, and "#1 global, top 0.01%".

The dashboard has these panels:

| Panel | What it does |
|---|---|
| Resolution grid | Live expiries, with YES prices in cents |
| Fill survival | Chance a resting order fills before the market expires |
| Order flow "aurora" | Tape pressure / cumulative volume delta, plus a regime label (e.g. "chop") |
| Book "membrane" | Order-book depth and pressure |
| Phase vortex / coherence | Gauge of how strongly the signals agree (99%) |
| Ruin cone | Forward Monte Carlo of equity paths, giving the risk of ruin |
| Analog matcher (k-NN) | Finds the most similar past 64-bar windows and shows what happened next (match 0.97, 4 up / 2 down, median +1.7¢ over 26 bars) |
| Bayesian update | Prior P(YES) 49¢ → posterior 55¢, edge +1.98¢ |
| Footer guards | Kelly sizing capped at 12%, drawdown guard armed, cooldown |

### Reality check before building on it

* None of the numbers can be verified. The wallet address is truncated, and reels like
  this are often promotional mock-ups.
* The chart shows BTC at $65,013, but BTC was about $84.5k on 2026-09-23. So the
  footage is either older or demo data.
* The edge in 5-minute markets is thin and contested by very fast bots, and the
  spread eats most of it.
* Polymarket is restricted in some countries. Check that it is legal where you live.

### How it maps onto QuantumBrain

| Idea from the reel | QuantumBrain today | What to add |
|---|---|---|
| Analog matcher (k-NN) | — | `AnalogAgent`: z-score the last N returns, find the k nearest *past* windows, vote = share that went up. It plugs straight into the brain and the walk-forward test. |
| Bayesian P(YES) | Hedge re-weighting of agents | Turn the brain's output into a calibrated probability *p*, then compare it with the market price to get the edge. |
| Kelly sizing, capped | Volatility targeting + exposure cap | Fractional Kelly. For a binary contract bought at price *q*: f = (p − q) / (1 − q), with a hard cap. |
| Ruin cone (Monte Carlo) | Max-drawdown kill switch | Bootstrap past trade returns to estimate the chance of hitting the kill switch, and show it in `qbrain status`. |
| Order flow / book pressure | Candles only | L2 order book and trade stream via ccxt websockets. |
| Live dashboard | CLI `status` | Local web dashboard that reads the files in `state/`. |
| Polymarket 5-minute markets | Spot exchanges via ccxt | New venue using the Polymarket CLOB API, paper mode first. |

**Suggested build order:**
1. Dashboard
2. Analog agent, testable with the existing backtest
3. Kelly sizing and the ruin cone
4. Polymarket paper module
