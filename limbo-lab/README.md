# limbo-lab

A study of the provably fair **Limbo** algorithm used by Stake-style casinos:
the exact result formula reverse-engineered from the client-side verification
code, the exact odds, and a hard look at whether results can be predicted.

**Short answer to "can it be predicted?": no.** Each result is
`HMAC-SHA256(server seed, "client:nonce:0")`. While a server seed is live you
only ever see its hash, so the output is cryptographically indistinguishable
from random. A 60-million-round study here finds no pattern any predictor could
use; the 1% house edge holds on every target. What the algorithm *is* good for
is **verification** — checking after a seed is revealed that you weren't cheated.

## The algorithm

```
digest = HMAC-SHA256(key = serverSeed, message = `${clientSeed}:${nonce}:0`)
float  = first 4 bytes of digest, read as b0/256 + b1/256² + b2/256³ + b3/256⁴
raw    = 1e8 / (floor(float × 1e8) + 1) × 0.99        # 0.99 = return to player
result = max(floor(raw × 100) / 100, 1.00)
```

The `floor(float × 1e8) + 1` term is the detail the public documentation gets
wrong (it prints `1e8 / (float × 1e8) × 0.99`). Only the version above
reproduces the published verification vectors — see the tiny-float case in
`test/core.test.js`, where the two formulas disagree by a wide margin.

## Layout

| Path | What it is |
|------|------------|
| `src/limbo-core.js` | Exact algorithm: SHA-256, HMAC, the Limbo formula, exact odds, seed-commitment check. No dependencies; runs in Node and the browser. |
| `src/limbo-sim.js` | Streak maths, exact session outlook (binomial), betting-plan Monte Carlo, and the predictor-rule bench. |
| `bin/limbo.js` | Command-line tool: `verify`, `hash`, `odds`, `patterns`, `simulate`. |
| `app/` | Single-file web app (`index.html`, built from `index.template.html` + the two `src/` files). |
| `test/` | 40 tests: vectors, cross-checks against `node:crypto`, odds vs an exhaustive count, simulation and bench checks. |
| `research/` | The predictability study (`predictability.js`), the ML attack (`ml_attack.py`), the exhaustive 2³² enumeration (`exact_enum.c`), and saved outputs in `research/results/`. |

## Use it

```bash
# Verify the published test vector, step by step
node bin/limbo.js verify --server "server seed" --client "client seed" --nonce 1 --target 2

# Exact odds, house edge and losing-streak maths for a target
node bin/limbo.js odds --target 2

# Test "predictor" rules against the real results of a seed pair
node bin/limbo.js patterns --server "server seed" --client "client seed" --count 200000

# Play a betting plan thousands of times and see where it lands
node bin/limbo.js simulate --on-loss 100 --target 2 --sessions 5000

node --test "test/*.test.js"   # run the tests
node build.js                  # rebuild app/index.html
```

## What the research shows

- **`research/exact_enum.c`** evaluates the formula for all 4,294,967,296 draws:
  the result never rises as the draw grows (so exact odds are a binary search),
  every ordinary target returns exactly 99%, and the documented formula differs
  from the real one on about 0.13% of draws.
- **`research/predictability.js`** replays 6 seed pairs and 6 AES-noise control
  runs (10M rounds each) through 124 statistical tests — uniformity, frequencies,
  memory between rounds, "due"-after-a-streak, and a bench of betting rules
  including a self-learning pattern matcher. Limbo scores no higher than the
  known-random control, and the most extreme results never repeat on a fresh
  seed pair. A planted fake pattern *is* caught, so the tests work.
- **`research/ml_attack.py`** trains logistic-regression and neural-network
  models on 3M past results and scores them on 1M unseen ones. On a planted
  pattern they succeed (AUC z ≈ 16); on the real results they score z ≈ 1 —
  no edge. Its own HMAC implementation (Python `hashlib`) cross-checks the
  JavaScript, and both print the same checksum line.

## Responsible-use note

The 1% house edge is mathematical. No seed, streak, system, or model changes it;
over time, flat betting loses 1% of everything wagered and the martingale in
`simulate` busts a large share of sessions. This project is for understanding
and verification, not a betting strategy.
