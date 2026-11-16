'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Core = require('../src/limbo-core.js');
const Sim = require('../src/limbo-sim.js');

test('twoSidedP matches known normal tail values', () => {
  assert.ok(Math.abs(Sim.twoSidedP(0) - 1) < 1e-6);
  assert.ok(Math.abs(Sim.twoSidedP(1.959964) - 0.05) < 1e-5);
  assert.ok(Math.abs(Sim.twoSidedP(-2.575829) - 0.01) < 1e-5);
  assert.ok(Math.abs(Sim.twoSidedP(3) - 0.0026998) < 1e-5);
});

test('lgamma matches factorials', () => {
  let f = 0; // log(k!)
  for (let k = 1; k <= 170; k++) {
    f += Math.log(k);
    assert.ok(Math.abs(Sim.lgamma(k + 1) - f) < 1e-9 * Math.max(1, f), `log(${k}!)`);
  }
  assert.ok(Math.abs(Sim.lgamma(0.5) - Math.log(Math.sqrt(Math.PI))) < 1e-12);
});

test('binomialTail agrees with a direct sum of exact terms', () => {
  const choose = (n, k) => { let c = 1; for (let i = 0; i < k; i++) c = (c * (n - i)) / (i + 1); return c; };
  for (const [n, p] of [[1, 0.3], [7, 0.495], [25, 0.099], [40, 0.9802], [60, 0.5]]) {
    for (let kmin = -1; kmin <= n + 2; kmin++) {
      let want = 0;
      for (let k = Math.max(kmin, 0); k <= n; k++) want += choose(n, k) * p ** k * (1 - p) ** (n - k);
      if (kmin <= 0) want = 1;
      assert.ok(Math.abs(Sim.binomialTail(n, p, kmin) - want) < 1e-11, `n ${n}, p ${p}, kmin ${kmin}`);
    }
  }
});

test('binomialTail stays accurate for large sessions', () => {
  // symmetric case: P(X >= n/2 + 1) + P(X <= n/2 - 1) + P(X = n/2) = 1 and the two tails are equal
  const n = 100000;
  const upper = Sim.binomialTail(n, 0.5, n / 2 + 1);
  const atLeastHalf = Sim.binomialTail(n, 0.5, n / 2);
  const middle = atLeastHalf - upper;
  assert.ok(Math.abs(2 * upper + middle - 1) < 1e-9);
  // normal approximation check far from the centre: P(Z > 3) = 0.0013499
  const k = Math.round(n / 2 + 3 * Math.sqrt(n / 4));
  assert.ok(Math.abs(Sim.binomialTail(n, 0.5, k) - 0.00135) < 0.0001);
  assert.equal(Sim.binomialTail(1000000, 0.495, 0), 1);
  assert.ok(Sim.binomialTail(1000000, 0.495, 1000000) >= 0);
});

test('binomialQuantile is the smallest k that reaches the probability', () => {
  for (const [n, p] of [[10, 0.495], [200, 0.099], [5000, 0.66], [100000, 0.0099]]) {
    for (const prob of [0.1, 0.5, 0.9]) {
      const k = Sim.binomialQuantile(n, p, prob);
      assert.ok(1 - Sim.binomialTail(n, p, k + 1) >= prob - 1e-12, `cdf(k) for n ${n}`);
      if (k > 0) assert.ok(1 - Sim.binomialTail(n, p, k) < prob, `cdf(k-1) for n ${n}`);
    }
  }
});

test('sessionOutlook: exact chance of being ahead, checked by hand and by simulation', () => {
  // 1 bet at 2x: ahead only if it wins
  const one = Sim.sessionOutlook(2, 1);
  assert.equal(one.winsToProfit, 1);
  assert.ok(Math.abs(one.ahead - Core.winProbability(2)) < 1e-12);
  // 2 bets at 2x: one win breaks even, so both must win
  const two = Sim.sessionOutlook(2, 2);
  assert.equal(two.winsToProfit, 2);
  assert.ok(Math.abs(two.ahead - Core.winProbability(2) ** 2) < 1e-12);
  // 11 bets at 1.1x: ten wins return exactly 11.00, so all eleven must win
  assert.equal(Sim.sessionOutlook(1.1, 11).winsToProfit, 11);
  // 10 bets at 100x: any single win is a profit
  const long = Sim.sessionOutlook(100, 10);
  assert.ok(Math.abs(long.ahead - (1 - (1 - Core.winProbability(100)) ** 10)) < 1e-12);

  // against simulation: 1,000 bets at 2x and at 10x
  for (const target of [2, 10]) {
    const o = Sim.sessionOutlook(target, 1000);
    const r = Sim.monteCarlo({ balance: 1e9, baseBet: 1, target, maxBets: 1000 }, 20000, Sim.seededRng(target + 40));
    assert.ok(Math.abs(r.aheadRate - o.ahead) < 0.015, `target ${target}: simulated ${r.aheadRate}, exact ${o.ahead}`);
    assert.ok(Math.abs(o.mean + 10) < 0.01);
    // the 10th-90th percentile band should hold about 80% of simulated sessions
    let inside = 0;
    for (const f of r.finals) { const net = f - 1e9; if (net >= o.low && net <= o.high) inside++; }
    assert.ok(inside / r.finals.length > 0.78 && inside / r.finals.length < 0.9, `band holds ${inside / r.finals.length}`);
  }
  // the longer the session, the smaller the chance of being ahead
  const chances = [100, 1000, 10000, 100000].map((n) => Sim.sessionOutlook(2, n).ahead);
  for (let i = 1; i < chances.length; i++) assert.ok(chances[i] < chances[i - 1]);
});

test('runProbability agrees with brute-force enumeration of every outcome', () => {
  const p = 0.37;
  for (const bets of [1, 4, 9, 14]) {
    for (let length = 1; length <= bets + 1; length++) {
      let prob = 0;
      for (let mask = 0; mask < 1 << bets; mask++) {
        let run = 0, best = 0, weight = 1;
        for (let i = 0; i < bets; i++) {
          if (mask & (1 << i)) { weight *= p; run = 0; } else { weight *= 1 - p; run++; if (run > best) best = run; }
        }
        if (best >= length) prob += weight;
      }
      assert.ok(Math.abs(Sim.runProbability(p, length, bets) - prob) < 1e-12, `bets ${bets}, length ${length}`);
    }
  }
});

test('runProbability edge cases', () => {
  assert.equal(Sim.runProbability(0.5, 0, 10), 1);
  assert.equal(Sim.runProbability(0.5, 11, 10), 0);
  assert.ok(Math.abs(Sim.runProbability(0.5, 10, 10) - 0.5 ** 10) < 1e-15);
});

test('longestStreak returns the last length that still meets the chance', () => {
  const p = Core.winProbability(2);
  for (const [bets, chance] of [[100, 0.5], [1000, 0.5], [1000, 0.01], [5000, 0.05]]) {
    const L = Sim.longestStreak(p, bets, chance);
    assert.ok(Sim.runProbability(p, L, bets) >= chance);
    assert.ok(Sim.runProbability(p, L + 1, bets) < chance);
  }
});

test('seededRng is reproducible and roughly uniform', () => {
  const a = Sim.seededRng(42);
  const b = Sim.seededRng(42);
  for (let i = 0; i < 100; i++) assert.equal(a(), b());
  const c = Sim.seededRng(7);
  let below = 0;
  const N = 400000;
  for (let i = 0; i < N; i++) if (c() < 2147483648) below++;
  assert.ok(Math.abs(below / N - 0.5) < 0.004);
});

test('cryptoRng returns 32-bit integers', () => {
  const r = Sim.cryptoRng();
  for (let i = 0; i < 20000; i++) {
    const v = r();
    assert.ok(Number.isInteger(v) && v >= 0 && v < 4294967296);
  }
});

test('normalizePlan fills defaults and rejects impossible plans', () => {
  const p = Sim.normalizePlan({ target: 3, onLoss: { action: 'increase', percent: 50 } });
  assert.equal(p.balance, 1000);
  assert.equal(p.onLoss.percent, 50);
  assert.equal(p.onWin.action, 'reset');
  assert.throws(() => Sim.normalizePlan({ balance: 0 }));
  assert.throws(() => Sim.normalizePlan({ baseBet: -1 }));
  assert.throws(() => Sim.normalizePlan({ target: 1 }));
  assert.throws(() => Sim.normalizePlan({ maxBets: 2.5 }));
  assert.throws(() => Sim.normalizePlan({ onLoss: { action: 'increase', percent: -100 } }));
  assert.throws(() => Sim.normalizePlan({ onWin: { action: 'double' } }));
});

test('a session follows the plan bet by bet', () => {
  // scripted draws: lose, lose, win, lose (draw 0 always wins, 2^32-1 always loses)
  const script = [4294967295, 4294967295, 0, 4294967295];
  let i = 0;
  const plan = Sim.normalizePlan({ balance: 100, baseBet: 1, target: 2, maxBets: 4, onLoss: { action: 'increase', percent: 100 } });
  const s = Sim.simulateSession(plan, () => script[i++], Core.winCount(2), true);
  // bets 1, 2, 4 (win, pays 8), then back to 1
  assert.deepEqual(s.path, [100, 99, 97, 101, 100]);
  assert.equal(s.wagered, 8);
  assert.equal(s.wins, 1);
  assert.equal(s.maxBet, 4);
  assert.equal(s.longestLossStreak, 2);
  assert.equal(s.reason, 'bets');
  assert.equal(s.final, 100);
});

test('a session stops when the next bet cannot be covered', () => {
  const plan = Sim.normalizePlan({ balance: 10, baseBet: 1, target: 2, maxBets: 100, onLoss: { action: 'increase', percent: 100 } });
  const s = Sim.simulateSession(plan, () => 4294967295, Core.winCount(2), false);
  // loses 1 + 2 + 4 = 7, then cannot cover a bet of 8 with 3 left
  assert.equal(s.reason, 'bust');
  assert.equal(s.bets, 3);
  assert.equal(s.final, 3);
});

test('stop limits end the session', () => {
  const win = Sim.simulateSession(Sim.normalizePlan({ balance: 100, baseBet: 5, target: 2, stopProfit: 10 }), () => 0, Core.winCount(2), false);
  assert.equal(win.reason, 'profit');
  assert.equal(win.bets, 2);
  const lose = Sim.simulateSession(Sim.normalizePlan({ balance: 100, baseBet: 5, target: 2, stopLoss: 20 }), () => 4294967295, Core.winCount(2), false);
  assert.equal(lose.reason, 'loss');
  assert.equal(lose.bets, 4);
});

test('flat betting loses the house edge on average, whatever the target', () => {
  for (const target of [1.5, 2, 10]) {
    const r = Sim.monteCarlo({ balance: 1e9, baseBet: 1, target, maxBets: 20000 }, 300, Sim.seededRng(target * 100));
    // 6,000,000 one-unit bets: mean net per bet should be -0.01 within sampling error
    const perBet = r.meanNet / r.meanBets;
    const sd = Math.sqrt(target - 1) / Math.sqrt(300 * 20000);
    assert.ok(Math.abs(perBet + 0.01) < 5 * sd, `target ${target}: ${perBet}`);
    assert.ok(Math.abs(r.expectedNet + 0.01 * r.meanWagered) < 1e-6 * r.meanWagered);
  }
});

test('martingale: most sessions end ahead, but the average still matches the house edge', () => {
  const plan = { balance: 1000, baseBet: 1, target: 2, maxBets: 500, onLoss: { action: 'increase', percent: 100 } };
  const r = Sim.monteCarlo(plan, 40000, Sim.seededRng(99));
  assert.ok(r.aheadRate > 0.5, `ahead ${r.aheadRate}`);
  assert.ok(r.bustRate > 0.1, `bust ${r.bustRate}`);
  // Mean result tracks -1% of the amount wagered (wide tolerance: heavy-tailed outcome).
  assert.ok(r.meanNet < 0);
  assert.ok(Math.abs(r.meanNet - r.expectedNet) < 6);
  assert.equal(r.sessions, 40000);
  assert.equal(r.paths.length, 6);
  assert.equal(r.reasons.bets + r.reasons.bust + r.reasons.profit + r.reasons.loss, 40000);
});

test('predictorBench counts bets and wins correctly on a hand-made sequence', () => {
  // target 2: W L L W W L  (1 = win)
  const mult = Float64Array.from([2.5, 1.0, 1.5, 3.0, 10.0, 1.1]);
  const b = Sim.predictorBench(mult, 2);
  const rule = (id) => b.rules.find((r) => r.id === id);
  assert.equal(rule('always').bets, 6);
  assert.equal(rule('always').wins, 3);
  // after a loss: rounds 2,3 (0-based) follow a loss -> outcomes L, W
  assert.equal(rule('after_loss').bets, 2);
  assert.equal(rule('after_loss').wins, 1);
  // after a win: rounds 1,4,5 -> L, W, L
  assert.equal(rule('after_win').bets, 3);
  assert.equal(rule('after_win').wins, 1);
  // after a 10x: round 5 -> L
  assert.equal(rule('after_big').bets, 1);
  assert.equal(rule('after_big').wins, 0);
  // two losses in a row precede round 3 only -> W
  assert.equal(rule('due_short').bets, 1);
  assert.equal(rule('due_short').wins, 1);
  // odd-numbered rounds (1st, 3rd, 5th) -> W, L, W
  assert.equal(rule('odd').bets, 3);
  assert.equal(rule('odd').wins, 2);
  // flat one-unit profit for the baseline: 3 wins * 1 - 3 losses
  assert.equal(rule('always').profit, 0);
  assert.equal(b.overall, 0.5);
  // lift is the rule's win rate minus the win rate of all rounds
  assert.ok(Math.abs(rule('odd').lift - (2 / 3 - 0.5)) < 1e-12);
  // six rounds are far too few to score
  assert.ok(Number.isNaN(rule('always').zOdds));
  assert.ok(Number.isNaN(rule('odd').zPick));
  assert.equal(b.maxAbsZ, 0);
});

test('predictorBench scores: zOdds against the exact chance, zPick against skipped rounds', () => {
  // 4000 rounds at target 2; odd-numbered rounds win 60%, even-numbered 40% (built by hand).
  const mult = new Float64Array(4000);
  for (let i = 0; i < mult.length; i++) {
    const slot = Math.floor(i / 2) % 10;
    const win = i % 2 === 0 ? slot < 6 : slot < 4;
    mult[i] = win ? 3 : 1.1;
  }
  const b = Sim.predictorBench(mult, 2);
  const odd = b.rules.find((r) => r.id === 'odd');
  const p = Core.winProbability(2);
  assert.equal(odd.bets, 2000);
  assert.equal(odd.wins, 1200);
  assert.ok(Math.abs(odd.zOdds - (1200 - 2000 * p) / Math.sqrt(2000 * p * (1 - p))) < 1e-9);
  // pooled rate 0.5, difference 0.6 - 0.4, standard error sqrt(0.25 * (1/2000 + 1/2000))
  assert.ok(Math.abs(odd.zPick - 0.2 / Math.sqrt(0.25 * (2 / 2000))) < 1e-9);
  assert.ok(Math.abs(odd.luck - Sim.twoSidedP(odd.zPick)) < 1e-12);
  // the baseline bets every round, so it has no skipped rounds to compare with
  assert.ok(Number.isNaN(b.rules[0].zPick));
  assert.ok(Number.isFinite(b.rules[0].zOdds));
});

test('predictorBench never uses the current round to decide (no look-ahead)', () => {
  const base = Core.replay('b'.repeat(64), 'lookahead', 1, 3000);
  // Whether a rule bets on the final round must not depend on that round's result:
  // flip the last result between a loss and a big win and compare the bet counts.
  for (let n = 1; n <= base.length; n += 7) {
    const lost = Float64Array.from(base.subarray(0, n));
    const won = Float64Array.from(lost);
    lost[n - 1] = 1;
    won[n - 1] = 50;
    const a = Sim.predictorBench(lost, 2);
    const b = Sim.predictorBench(won, 2);
    assert.deepEqual(a.rules.map((r) => r.bets), b.rules.map((r) => r.bets), `prefix of ${n} rounds`);
    // and the flip must show up in the baseline's win count
    assert.equal(b.rules[0].wins - a.rules[0].wins, 1);
  }
});

test('no rule beats the odds on real results (five seed pairs, 300k rounds each)', () => {
  let worst = 0;
  for (let s = 0; s < 5; s++) {
    const server = Core.sha256Hex('bench-server-' + s);
    const mult = Core.replay(server, 'bench-client-' + s, 1, 300000);
    for (const target of [2, 10]) {
      const b = Sim.predictorBench(mult, target);
      if (b.maxAbsZ > worst) worst = b.maxAbsZ;
    }
  }
  // 100 rule evaluations: a largest |z| above 4.5 would mean a real pattern.
  assert.ok(worst < 4.5, `largest |z| = ${worst}`);
});

test('predictorBench does detect a pattern when one really exists', () => {
  // A deliberately broken generator: a win always follows a loss.
  const mult = new Float64Array(20000);
  let last = 0;
  const rng = Sim.seededRng(5);
  for (let i = 0; i < mult.length; i++) {
    const win = last === 0 ? 1 : (rng() < 2147483648 ? 1 : 0);
    mult[i] = win ? 2.5 : 1.2;
    last = win;
  }
  const b = Sim.predictorBench(mult, 2);
  assert.ok(b.rules.find((r) => r.id === 'after_loss').zPick > 20);
  assert.ok(b.rules.find((r) => r.id === 'after_win').zPick < -20);
  assert.ok(b.maxAbsZ > 20);
});

test('frequencyTable compares observed and exact win rates', () => {
  const mult = Core.replay('c'.repeat(64), 'freq', 1, 200000);
  const rows = Sim.frequencyTable(mult, [1.5, 2, 5]);
  for (const row of rows) {
    assert.ok(Math.abs(row.observed - row.expected) < 0.006);
    assert.ok(Math.abs(row.z) < 4.5);
  }
  assert.equal(rows[1].expected, Core.winProbability(2));
});
