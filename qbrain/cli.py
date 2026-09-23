"""Command line: qbrain backtest | train | run | status | mine-calc."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from . import mining
from .config import Config
from .data import cached_history, make_exchange, synthetic_ohlcv

log = logging.getLogger("qbrain")

DISCLAIMER = (
    "Past performance, and especially backtests, do not predict future results. "
    "Only trade money you can afford to lose."
)


def _setup_logging(cfg: Config, verbose: bool) -> None:
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(Path(cfg.state_dir) / "qbrain.log")]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def _table(rows: list[dict], columns: list[str]) -> str:
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    lines = ["  ".join(c.ljust(widths[c]) for c in columns), "  ".join("-" * widths[c] for c in columns)]
    lines += ["  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows]
    return "\n".join(lines)


def cmd_backtest(cfg: Config, args) -> None:
    from .optimize import walk_forward

    symbols = args.symbol or cfg.symbols
    capital_each = cfg.starting_capital / len(symbols)
    tests, benches = [], []
    for i, symbol in enumerate(symbols):
        if args.synthetic:
            df = synthetic_ohlcv(args.bars or cfg.history_bars, cfg.timeframe, seed=i + 1)
            source = "synthetic data"
        else:
            df = cached_history(cfg.data_exchange, symbol, cfg.timeframe, args.bars or cfg.history_bars,
                                Path(cfg.state_dir) / "data")
            source = cfg.data_exchange
        print(f"\n=== {symbol} ({source}, {cfg.timeframe}, {len(df)} bars, ${capital_each:.2f} sleeve) ===")
        report = walk_forward(df, cfg, capital_each, symbol)
        print(f"train: {df.index[0]:%Y-%m-%d} -> {report.split_time:%Y-%m-%d}   "
              f"test (never seen while training): {report.split_time:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}")
        keys = list(report.test.stats)
        rows = [
            {"metric": k, "train (in-sample)": report.train.stats[k], "TEST tuned brain": report.test.stats[k],
             "TEST untuned brain": report.test_untuned.stats[k]}
            for k in keys
        ]
        print(_table(rows, ["metric", "train (in-sample)", "TEST tuned brain", "TEST untuned brain"]))
        prior = report.brain.prior().round(3).to_dict()
        print(f"brain: prior weights {prior}, learning_rate {report.brain.learning_rate:.2f}, "
              f"memory {report.brain.memory}, conviction {report.brain.conviction:.2f}")
        tests.append(report.test.equity)
        benches.append(report.test.benchmark)

    if len(symbols) > 1:
        total = pd.concat(tests, axis=1).ffill().sum(axis=1)
        bench = pd.concat(benches, axis=1).ffill().sum(axis=1)
        print(f"\n=== portfolio, out-of-sample ===\n"
              f"start ${total.iloc[0]:.2f} -> end ${total.iloc[-1]:.2f} ({(total.iloc[-1] / total.iloc[0] - 1) * 100:+.1f}%)"
              f" | buy & hold ${bench.iloc[-1]:.2f} ({(bench.iloc[-1] / bench.iloc[0] - 1) * 100:+.1f}%)")
    print(f"\n{DISCLAIMER}")


def cmd_train(cfg: Config, args) -> None:
    from .live import train_model

    for symbol in args.symbol or cfg.symbols:
        brain, _ = train_model(cfg, symbol)
        print(f"{symbol}: saved brain, prior weights {brain.prior().round(3).to_dict()}")


def cmd_run(cfg: Config, args) -> None:
    from .live import LiveRunner

    if not args.real_money:
        print("PAPER mode: real prices, simulated orders. Add --real-money only after you trust it.")
    LiveRunner(cfg, real_money=args.real_money).run(once=args.once)


def cmd_status(cfg: Config, args) -> None:
    from .live import status

    prices = {}
    try:
        exchange = make_exchange(cfg.exchange)
        prices = {s: float(exchange.fetch_ticker(s)["last"]) for s in cfg.symbols}
    except Exception as exc:
        log.warning("could not fetch prices (%s); equity will be blank", exc)
    rows = status(cfg, args.real_money, prices)
    print(_table(rows, list(rows[0])))
    equity = sum(r["equity"] or 0 for r in rows)
    if all(r["equity"] is not None for r in rows):
        print(f"\ntotal equity ${equity:.2f} vs starting capital ${cfg.starting_capital:.2f} "
              f"({(equity / cfg.starting_capital - 1) * 100:+.2f}%)")


def cmd_mine_calc(cfg: Config, args) -> None:
    net = mining.fetch_network(offline=args.offline)
    rig = dict(mining.RIGS[args.rig])
    if args.hashrate_th:
        rig.update(th=args.hashrate_th, label="custom rig")
    if args.power_w:
        rig["watts"] = args.power_w

    src = "live from mempool.space" if net.live else "offline snapshot, Sept 2026"
    print(f"network: difficulty {net.difficulty / 1e12:.2f}T (~{mining.network_hashrate(net.difficulty) / 1e18:.0f} EH/s), "
          f"BTC ${net.price_usd:,.0f} [{src}]")

    r = mining.rig_report(rig["th"], rig["watts"], args.electricity, net)
    print(f"\nYour rig: {rig['label']}, {rig['th']:g} TH/s, {rig['watts']:g} W, electricity ${args.electricity}/kWh")
    print(f"  earns     {r['btc_per_day']:.3g} BTC/day = ${r['usd_revenue_per_day']:.3g}/day")
    print(f"  power     ${r['usd_power_cost_per_day']:.2f}/day")
    print(f"  profit    ${r['usd_profit_per_day']:.2f}/day")
    years = r["days_to_one_btc"] / 365
    print(f"  1 BTC in  {years:,.1f} years" if years >= 1 else f"  1 BTC in  {r['days_to_one_btc']:,.1f} days")

    g = mining.goal_report(args.goal_btc, args.goal_days, mining.RIGS["s21-xp"], args.electricity, args.unit_price, net)
    print(f"\nTo mine {args.goal_btc:g} BTC in {args.goal_days:g} days you would need:")
    print(f"  hashrate      {g['hashrate_needed_ph']:,.0f} PH/s ({g['share_of_network'] * 100:.3f}% of all Bitcoin mining)")
    print(f"  machines      {g['machines']:,.0f} x Antminer S21 XP")
    print(f"  power         {g['megawatts']:.1f} MW (a small power plant)")
    print(f"  hardware      ${g['hardware_usd']:,.0f} (assuming ${args.unit_price:,.0f} per machine)")
    print(f"  electricity   ${g['electricity_usd']:,.0f} for those {args.goal_days:g} days")
    print(f"  reward        ${g['reward_value_usd']:,.0f}")
    print("\nAnyone selling '1 BTC in 10 days' for a small deposit is running a scam.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qbrain", description="Multi-agent crypto bot with a quantum-inspired brain")
    parser.add_argument("--config", default="config.json", help="JSON config (defaults are used if missing)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="walk-forward test: tune on old data, grade on unseen data")
    bt.add_argument("--symbol", action="append", help="repeatable; defaults to the config symbols")
    bt.add_argument("--bars", type=int, help="history length")
    bt.add_argument("--synthetic", action="store_true", help="offline: use generated data")
    bt.set_defaults(func=cmd_backtest)

    tr = sub.add_parser("train", help="tune each symbol's brain on all history and save it")
    tr.add_argument("--symbol", action="append")
    tr.set_defaults(func=cmd_train)

    run = sub.add_parser("run", help="trade 24/7 (paper by default)")
    run.add_argument("--real-money", action="store_true", help="send real orders (needs API keys + opt-in)")
    run.add_argument("--once", action="store_true", help="process the latest candle and exit (for cron)")
    run.set_defaults(func=cmd_run)

    st = sub.add_parser("status", help="show balances, fees and P&L")
    st.add_argument("--real-money", action="store_true")
    st.set_defaults(func=cmd_status)

    mc = sub.add_parser("mine-calc", help="what a mining rig really earns")
    mc.add_argument("--rig", choices=sorted(mining.RIGS), default="s21-xp")
    mc.add_argument("--hashrate-th", type=float, help="custom rig hashrate in TH/s")
    mc.add_argument("--power-w", type=float, help="custom rig power draw in watts")
    mc.add_argument("--electricity", type=float, default=0.10, help="USD per kWh")
    mc.add_argument("--goal-btc", type=float, default=1.0)
    mc.add_argument("--goal-days", type=float, default=10.0)
    mc.add_argument("--unit-price", type=float, default=3000.0, help="assumed USD per ASIC")
    mc.add_argument("--offline", action="store_true")
    mc.set_defaults(func=cmd_mine_calc)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    _setup_logging(cfg, args.verbose)
    args.func(cfg, args)


if __name__ == "__main__":
    main()
