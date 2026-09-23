"""Bitcoin mining calculator: what a given rig really earns.

This is a calculator, not a miner. Every miner in the world shares ~450 BTC a day
(144 blocks x 3.125 BTC subsidy, plus fees) in proportion to hashrate, so the maths is
simple and unforgiving:

    expected blocks/day = hashrate * 86400 / (difficulty * 2^32)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("qbrain")

SECONDS_PER_DAY = 86_400
BLOCK_SUBSIDY_BTC = 3.125          # until the next halving (block 1,050,000, ~2028)
AVG_FEES_PER_BLOCK_BTC = 0.02      # assumption; fees have been low in 2025-2026

# Snapshot from mempool.space on 2026-09-23, used only when offline.
FALLBACK_DIFFICULTY = 132.757e12
FALLBACK_PRICE_USD = 84_450.0

# Approximate manufacturer specs. Hardware prices move a lot; pass your own.
RIGS = {
    "s21-xp": {"th": 270.0, "watts": 3645.0, "label": "Antminer S21 XP (ASIC)"},
    "s21": {"th": 200.0, "watts": 3500.0, "label": "Antminer S21 (ASIC)"},
    "gpu": {"th": 0.003, "watts": 300.0, "label": "high-end gaming GPU"},
    "cpu": {"th": 0.00005, "watts": 65.0, "label": "laptop/desktop CPU"},
}


@dataclass
class Network:
    difficulty: float
    price_usd: float
    live: bool


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_network(offline: bool = False) -> Network:
    if not offline:
        try:
            difficulty = float(_get_json("https://mempool.space/api/v1/mining/hashrate/3d")["currentDifficulty"])
            price = float(_get_json("https://mempool.space/api/v1/prices")["USD"])
            return Network(difficulty, price, live=True)
        except Exception as exc:
            log.warning("could not reach mempool.space (%s); using the September 2026 snapshot", exc)
    return Network(FALLBACK_DIFFICULTY, FALLBACK_PRICE_USD, live=False)


def network_hashrate(difficulty: float) -> float:
    """Hashes per second implied by the difficulty (one block every 600 s)."""
    return difficulty * 2**32 / 600


def btc_per_day(hashrate_hs: float, difficulty: float, reward_btc: float = BLOCK_SUBSIDY_BTC + AVG_FEES_PER_BLOCK_BTC) -> float:
    return hashrate_hs * SECONDS_PER_DAY / (difficulty * 2**32) * reward_btc


def hashrate_for(btc_daily: float, difficulty: float, reward_btc: float = BLOCK_SUBSIDY_BTC + AVG_FEES_PER_BLOCK_BTC) -> float:
    return btc_daily * difficulty * 2**32 / (SECONDS_PER_DAY * reward_btc)


def rig_report(th: float, watts: float, electricity_usd_kwh: float, net: Network, pool_fee: float = 0.02) -> dict:
    hs = th * 1e12
    btc = btc_per_day(hs, net.difficulty) * (1 - pool_fee)
    revenue = btc * net.price_usd
    power_cost = watts / 1000 * 24 * electricity_usd_kwh
    return {
        "btc_per_day": btc,
        "usd_revenue_per_day": revenue,
        "usd_power_cost_per_day": power_cost,
        "usd_profit_per_day": revenue - power_cost,
        "days_to_one_btc": 1 / btc if btc > 0 else float("inf"),
        "share_of_network": hs / network_hashrate(net.difficulty),
    }


def goal_report(goal_btc: float, goal_days: float, rig: dict, electricity_usd_kwh: float,
                unit_price_usd: float, net: Network) -> dict:
    needed_hs = hashrate_for(goal_btc / goal_days, net.difficulty)
    machines = needed_hs / (rig["th"] * 1e12)
    megawatts = machines * rig["watts"] / 1e6
    return {
        "hashrate_needed_ph": needed_hs / 1e15,
        "share_of_network": needed_hs / network_hashrate(net.difficulty),
        "machines": machines,
        "megawatts": megawatts,
        "hardware_usd": machines * unit_price_usd,
        "electricity_usd": megawatts * 1000 * 24 * goal_days * electricity_usd_kwh,
        "reward_value_usd": goal_btc * net.price_usd,
    }
