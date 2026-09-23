# Research: 10 open-source projects, and what QuantumBrain took from each

I surveyed the open-source trading-bot landscape (GitHub plus 2026 comparison reviews)
and shortlisted ten projects. They were picked for being actively maintained,
open-source and relevant to autonomous or multi-agent trading, with serious
backtesting. QuantumBrain does not copy their code; it borrows one idea from each.

| # | Project | What it is | Idea borrowed for QuantumBrain |
|---|---------|------------|--------------------------------|
| 1 | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | The most popular open-source crypto bot (Python, GPL-3). Dry-run mode, Hyperopt, FreqAI. | **Paper trading by default** and tuning parameters against a backtest before any money moves. |
| 2 | [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | Market-making and arbitrage framework with connectors for 50+ CEX/DEX venues (Apache-2). | A **kill switch** that stops everything at a loss threshold, and a separate budget per strategy (our per-symbol "sleeves"). |
| 3 | [ccxt/ccxt](https://github.com/ccxt/ccxt) | Unified API for 100+ exchanges (MIT). | Used directly as the exchange layer, so the bot works on Kraken, Binance, OKX, KuCoin and others without code changes. |
| 4 | [jesse-ai/jesse](https://github.com/jesse-ai/jesse) | Research-first framework focused on accurate backtests (MIT). | **No look-ahead**: decide on a closed candle, fill on the next one. `tests/test_indicators_agents.py::test_no_look_ahead` enforces it. |
| 5 | [Drakkar-Software/OctoBot](https://github.com/Drakkar-Software/OctoBot) | Modular bot where many "evaluators" feed a strategy (GPL-3). | **Several independent agents** vote, and a strategy layer fuses their votes. |
| 6 | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | LLM multi-agent "trading firm": analysts, bull/bear researchers, trader, risk team. | **Role separation**: signal agents propose, the brain decides, and a risk manager with **veto power** has the last word. |
| 7 | [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | Production-grade event-driven engine (Rust/Python). | **Backtest/live parity**: the backtester and the live bot run the same brain -> risk guard -> broker path. |
| 8 | [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Vectorised backtesting for huge parameter sweeps. | **Vectorised scoring** inside the optimizer loop, so hundreds of candidate brains can be scored in seconds. |
| 9 | [microsoft/qlib](https://github.com/microsoft/qlib) | AI-oriented quant research platform. | **Strict walk-forward evaluation**: tune on the past, report only on data the model never saw. |
| 10 | [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Deep reinforcement learning for trading; its ensemble strategy picks the agent with the best recent Sharpe. | **Adaptive agent weighting** by recent risk-adjusted performance. We blend agents (Hedge algorithm) rather than picking just one. |

Also looked at but not adopted: Superalgos (visual, heavy), Passivbot (grid trading
with leverage on futures, too dangerous for a $50 account), and hosted bots
(3Commas, Pionex, Cryptohopper) because they are closed source.

## What the research says about profits

None of these projects promises profits, and the most popular ones tell users to start
in dry-run/paper mode and expect losses while learning. That matches what QuantumBrain's own
walk-forward tests show (see the README). Fees, slippage and changing market regimes
eat most simple edges, and small accounts pay proportionally the most.

## What "quantum" can and cannot do here

* Real quantum hardware in 2026 gives no demonstrated advantage for retail trading. Research
  on quantum annealing / QAOA for portfolio optimisation exists, but not at a scale or
  cost that beats a laptop for this problem.
* **Quantum-inspired** algorithms are real, classical and useful. QuantumBrain's optimizer
  is a Quantum-Inspired Evolutionary Algorithm (Han & Kim, *IEEE Transactions on
  Evolutionary Computation*, 2002). It uses Q-bit probability amplitudes, rotation gates,
  NOT-gate mutation and superposition resets to search the ~18-dimensional space of agent
  and brain parameters.
* The brain's Born-rule weighting (`w = a^2 / sum a^2`) is a quantum-flavoured way of
  writing a normalised trust vector. It is a nice notation, not a physical effect.

## Sources

* [Gainium: best open-source crypto trading bots 2026](https://gainium.io/best/open-source)
* [Freqtrade alternatives compared, 2026](https://alexbobes.com/crypto/best-freqtrade-alternatives/)
* [CoinCodeCap: open-source trading bots on GitHub](https://coincodecap.com/open-source-trading-bots-on-github)
* [autotradelab: 20+ Python quant frameworks reviewed](https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)
* [TradingAgents paper (arXiv 2412.20138)](https://arxiv.org/pdf/2412.20138)
* [mempool.space mining API](https://mempool.space/docs/api) (live difficulty and price used by `qbrain mine-calc`)
