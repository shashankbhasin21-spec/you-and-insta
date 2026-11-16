/*!
 * limbo-sim
 *
 * What can be computed about Limbo *without* the server seed: streak maths,
 * betting-plan simulation, and a bench that tests "predictor" rules against
 * real results. Everything is built on the exact odds from limbo-core.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./limbo-core.js'));
  else root.LimboSim = factory(root.LimboCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (Core) {
  'use strict';

  /* ------------------------------------------------------------------ *
   * Small numeric helpers
   * ------------------------------------------------------------------ */

  /** Chance that a standard normal value is at least |z| away from zero. */
  function twoSidedP(z) {
    const x = Math.abs(z) / Math.SQRT2;
    // Abramowitz & Stegun 7.1.26, absolute error below 1.5e-7
    const t = 1 / (1 + 0.3275911 * x);
    const poly = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t;
    return Math.min(1, poly * Math.exp(-x * x));
  }

  /** Value at quantile q (0..1) of an ascending-sorted array. */
  function quantile(sorted, q) {
    if (sorted.length === 0) return NaN;
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  /* ------------------------------------------------------------------ *
   * Exact session outlook (binomial)
   * ------------------------------------------------------------------ */

  const LANCZOS = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6,
    1.5056327351493116e-7,
  ];
  /** Natural log of the gamma function (Lanczos approximation). */
  function lgamma(x) {
    if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
    x -= 1;
    let a = LANCZOS[0];
    const t = x + 7.5;
    for (let i = 1; i < 9; i++) a += LANCZOS[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }

  function binomialPmf(n, p, k) {
    return Math.exp(lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1) + k * Math.log(p) + (n - k) * Math.log(1 - p));
  }

  /** P(X >= kmin) for X ~ Binomial(n, p), with 0 < p < 1. */
  function binomialTail(n, p, kmin) {
    if (kmin <= 0) return 1;
    if (kmin > n) return 0;
    const odds = p / (1 - p);
    const mode = Math.min(n, Math.floor((n + 1) * p));
    let sum = 0;
    // from the larger of the mode and kmin upward: terms only shrink
    const start = Math.max(mode, kmin);
    let term = binomialPmf(n, p, start);
    for (let k = start; k <= n && term > 0; k++) {
      sum += term;
      term *= ((n - k) / (k + 1)) * odds;
    }
    // if kmin lies below the mode, add kmin..mode-1 walking down from the mode
    if (kmin < mode) {
      term = binomialPmf(n, p, mode - 1);
      for (let k = mode - 1; k >= kmin && term > 0; k--) {
        sum += term;
        term *= (k / (n - k + 1)) / odds;
      }
    }
    return Math.min(1, sum);
  }

  /** Smallest k with P(X <= k) >= prob for X ~ Binomial(n, p). */
  function binomialQuantile(n, p, prob) {
    let lo = 0;
    let hi = n;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (1 - binomialTail(n, p, mid + 1) >= prob) hi = mid;
      else lo = mid + 1;
    }
    return lo;
  }

  /**
   * What `bets` flat bets of one unit at `target` lead to. All figures are in
   * units of one stake; nothing here is simulated.
   *   ahead     chance of ending with a profit
   *   low, high the 10th and 90th percentile of the result
   */
  function sessionOutlook(target, bets) {
    const o = Core.odds(target);
    // result = wins * target - bets. Compare in hundredths to stay in whole numbers.
    const cents = Math.round(target * 100);
    const net = (wins) => (wins * cents - bets * 100) / 100;
    const need = Math.floor((bets * 100) / cents) + 1; // fewest wins that leave a profit
    return {
      target,
      bets,
      p: o.p,
      edge: o.edge,
      mean: -o.edge * bets,
      winsToProfit: need,
      ahead: binomialTail(bets, o.p, need),
      low: net(binomialQuantile(bets, o.p, 0.1)),
      high: net(binomialQuantile(bets, o.p, 0.9)),
    };
  }

  /* ------------------------------------------------------------------ *
   * Losing streaks
   * ------------------------------------------------------------------ */

  /**
   * Chance of at least one run of `length` or more consecutive losses somewhere
   * in `bets` independent bets that each win with probability p.
   */
  function runProbability(p, length, bets) {
    if (length <= 0) return 1;
    if (length > bets) return 0;
    const q = 1 - p;
    const qL = Math.pow(q, length);
    // a[n] = chance of no such run in the first n bets
    const a = new Float64Array(bets + 1);
    for (let n = 0; n < length; n++) a[n] = 1;
    a[length] = 1 - qL;
    const c = p * qL;
    for (let n = length + 1; n <= bets; n++) a[n] = a[n - 1] - c * a[n - length - 1];
    return Math.min(1, Math.max(0, 1 - a[bets]));
  }

  /**
   * The longest losing streak that still has at least `chance` probability of
   * showing up within `bets` bets.
   */
  function longestStreak(p, bets, chance) {
    let lo = 0;
    let hi = bets;
    while (lo < hi) {
      const mid = lo + Math.ceil((hi - lo) / 2);
      if (runProbability(p, mid, bets) >= chance) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  /* ------------------------------------------------------------------ *
   * Random draws
   * ------------------------------------------------------------------ */

  /** Reproducible 32-bit generator (sfc32) for tests and repeatable demos. */
  function seededRng(seed) {
    let a = 0x9e3779b9, b = 0x243f6a88, c = 0xb7e15162, d = seed >>> 0;
    const next = () => {
      a >>>= 0; b >>>= 0; c >>>= 0; d >>>= 0;
      const t = (a + b + d) | 0;
      d = (d + 1) | 0;
      a = b ^ (b >>> 9);
      b = (c + (c << 3)) | 0;
      c = (c << 21) | (c >>> 11);
      c = (c + t) | 0;
      return t >>> 0;
    };
    for (let i = 0; i < 16; i++) next();
    return next;
  }

  /** Cryptographically strong 32-bit draws, the same quality the casino's HMAC gives. */
  function cryptoRng() {
    const buf = new Uint32Array(8192);
    let i = buf.length;
    return () => {
      if (i === buf.length) { globalThis.crypto.getRandomValues(buf); i = 0; }
      return buf[i++];
    };
  }

  /* ------------------------------------------------------------------ *
   * Betting-plan simulation
   * ------------------------------------------------------------------ */

  const DEFAULT_PLAN = {
    balance: 1000,
    baseBet: 1,
    target: 2,
    onWin: { action: 'reset', percent: 0 },
    onLoss: { action: 'reset', percent: 0 },
    maxBets: 1000,
    stopProfit: 0,
    stopLoss: 0,
  };

  function normalizePlan(plan) {
    const p = Object.assign({}, DEFAULT_PLAN, plan);
    p.onWin = Object.assign({}, DEFAULT_PLAN.onWin, plan && plan.onWin);
    p.onLoss = Object.assign({}, DEFAULT_PLAN.onLoss, plan && plan.onLoss);
    const bad = (msg) => { throw new Error(msg); };
    if (!(p.balance > 0)) bad('balance must be greater than 0');
    if (!(p.baseBet > 0)) bad('base bet must be greater than 0');
    if (!(p.target >= 1.01)) bad('target must be at least 1.01');
    if (!Number.isInteger(p.maxBets) || p.maxBets < 1) bad('number of bets must be a whole number of at least 1');
    for (const side of [p.onWin, p.onLoss]) {
      if (side.action !== 'reset' && side.action !== 'increase') bad("action must be 'reset' or 'increase'");
      if (side.action === 'increase' && !(side.percent > -100)) bad('percent change must be above -100');
    }
    if (p.stopProfit < 0 || p.stopLoss < 0) bad('stop limits cannot be negative');
    return p;
  }

  /**
   * Play one session of a betting plan.
   * `draw` returns uniform 32-bit integers; a bet wins when the draw is below
   * `winDraws` (= Core.winCount(target)), which reproduces the exact win chance.
   * Ends when the bets run out, a stop limit is hit, or the next bet cannot be
   * covered ('bust').
   */
  function simulateSession(plan, draw, winDraws, recordPath) {
    let balance = plan.balance;
    let bet = plan.baseBet;
    let bets = 0, wins = 0, wagered = 0, maxBet = 0, lowest = balance;
    let lossStreak = 0, longestLossStreak = 0;
    let reason = 'bets';
    const path = recordPath ? [balance] : null;
    while (bets < plan.maxBets) {
      if (bet > balance) { reason = 'bust'; break; }
      balance -= bet;
      wagered += bet;
      bets++;
      if (bet > maxBet) maxBet = bet;
      if (draw() < winDraws) {
        balance += bet * plan.target;
        wins++;
        lossStreak = 0;
        bet = plan.onWin.action === 'reset' ? plan.baseBet : bet * (1 + plan.onWin.percent / 100);
      } else {
        lossStreak++;
        if (lossStreak > longestLossStreak) longestLossStreak = lossStreak;
        bet = plan.onLoss.action === 'reset' ? plan.baseBet : bet * (1 + plan.onLoss.percent / 100);
      }
      if (balance < lowest) lowest = balance;
      if (path) path.push(balance);
      const net = balance - plan.balance;
      if (plan.stopProfit > 0 && net >= plan.stopProfit) { reason = 'profit'; break; }
      if (plan.stopLoss > 0 && -net >= plan.stopLoss) { reason = 'loss'; break; }
    }
    return { final: balance, net: balance - plan.balance, bets, wins, wagered, maxBet, lowest, longestLossStreak, reason, path };
  }

  /** Collects session results and turns them into summary statistics. */
  function createTally(plan, samplePaths) {
    const finals = [];
    const paths = [];
    const reasons = { bets: 0, profit: 0, loss: 0, bust: 0 };
    let wagered = 0, bets = 0, net = 0, ahead = 0, behind = 0, maxBet = 0, longest = 0;
    return {
      wantsPath: () => paths.length < samplePaths,
      add(s) {
        finals.push(s.final);
        reasons[s.reason]++;
        wagered += s.wagered;
        bets += s.bets;
        net += s.net;
        if (s.net > 0) ahead++; else if (s.net < 0) behind++;
        if (s.maxBet > maxBet) maxBet = s.maxBet;
        if (s.longestLossStreak > longest) longest = s.longestLossStreak;
        if (s.path && paths.length < samplePaths) paths.push(s.path);
      },
      get count() { return finals.length; },
      summary() {
        const n = finals.length;
        const sorted = Float64Array.from(finals).sort();
        const edge = Core.odds(plan.target).edge;
        return {
          sessions: n,
          aheadRate: ahead / n,
          behindRate: behind / n,
          bustRate: reasons.bust / n,
          reasons: Object.assign({}, reasons),
          final: {
            mean: plan.balance + net / n,
            min: sorted[0],
            p5: quantile(sorted, 0.05),
            p25: quantile(sorted, 0.25),
            median: quantile(sorted, 0.5),
            p75: quantile(sorted, 0.75),
            p95: quantile(sorted, 0.95),
            max: sorted[n - 1],
          },
          meanBets: bets / n,
          meanWagered: wagered / n,
          meanNet: net / n,
          // what the house edge alone predicts for this much wagering
          expectedNet: -edge * (wagered / n),
          largestBet: maxBet,
          longestLossStreak: longest,
          finals: sorted,
          paths,
        };
      },
    };
  }

  /** Run many sessions of a plan and summarise them. */
  function monteCarlo(plan, sessions, draw, samplePaths) {
    const p = normalizePlan(plan);
    const winDraws = Core.winCount(p.target);
    const tally = createTally(p, samplePaths === undefined ? 6 : samplePaths);
    const next = draw || cryptoRng();
    for (let i = 0; i < sessions; i++) tally.add(simulateSession(p, next, winDraws, tally.wantsPath()));
    return tally.summary();
  }

  /* ------------------------------------------------------------------ *
   * Pattern bench: do "predictor" rules beat the odds on real results?
   * ------------------------------------------------------------------ */

  /**
   * Each rule looks only at results that came before a round and decides
   * whether to bet on it. If results were predictable from history, some rule
   * would pick rounds that win more often than the rounds it skips.
   *
   * Per rule the bench reports how many rounds it bet and won, plus two scores
   * in standard errors:
   *   zOdds - its win rate against the exact chance p. This moves with the luck
   *           of the whole sample, so every rule shares it.
   *   zPick - its win rate against the rounds it skipped. This is the test of
   *           prediction: it is zero on average unless the rule really selects
   *           better rounds. |zPick| below about 2 is ordinary luck.
   */
  function predictorBench(mult, target) {
    const p = Core.winProbability(target);
    const n = mult.length;
    const dueShort = Math.max(2, Math.round(1 / p));
    const dueLong = Math.max(4, Math.ceil(3 / p));
    const win10 = Math.max(10, Math.ceil(10 / p));
    const expectedInWindow = p * win10;
    const MIN_PATTERN = 50;

    const rules = [
      { id: 'always', name: 'Bet every round', note: 'The baseline. No prediction at all.' },
      { id: 'after_loss', name: 'Bet only after a loss', note: 'The idea that a win tends to follow a loss.' },
      { id: 'after_win', name: 'Bet only after a win', note: 'The idea that wins come in clusters.' },
      { id: 'due_short', name: `Bet after ${dueShort} losses in a row`, note: 'The idea that a win is "due".' },
      { id: 'due_long', name: `Bet after ${dueLong} losses in a row`, note: 'The same idea after a long dry spell.' },
      { id: 'after_big', name: 'Bet right after a 10x or higher', note: 'The idea that big results cluster.' },
      { id: 'after_lows', name: 'Bet after two results under 1.20x', note: 'The idea that a high result follows early crashes.' },
      { id: 'cold', name: `Bet when the last ${win10} rounds ran cold`, note: 'Fewer recent wins than the odds predict.' },
      { id: 'hot', name: `Bet when the last ${win10} rounds ran hot`, note: 'More recent wins than the odds predict.' },
      { id: 'markov', name: 'Self-learning pattern matcher', note: 'Learns win rates after every 4-round win/loss pattern and bets on the best ones.' },
      { id: 'odd', name: 'Bet odd-numbered rounds only', note: 'A placebo: it cannot matter.' },
    ];
    const R = rules.length;
    const bets = new Float64Array(R);
    const wins = new Float64Array(R);

    let lossStreak = 0;
    let prevWin = false;
    let prev = NaN, prev2 = NaN;
    const ring = new Uint8Array(win10);
    let winsInWindow = 0;
    let pattern = 0;
    const seen = new Float64Array(16);
    const won = new Float64Array(16);
    let totalWon = 0;

    for (let i = 0; i < n; i++) {
      const m = mult[i];
      const win = m >= target ? 1 : 0;

      // decisions: only information from rounds before i
      const take = (r) => { bets[r]++; wins[r] += win; };
      take(0);
      if (i >= 1) {
        if (!prevWin) take(1); else take(2);
        if (prev >= 10) take(5);
      }
      if (lossStreak >= dueShort) take(3);
      if (lossStreak >= dueLong) take(4);
      if (i >= 2 && prev < 1.2 && prev2 < 1.2) take(6);
      if (i >= win10) {
        if (winsInWindow < expectedInWindow) take(7);
        else if (winsInWindow > expectedInWindow) take(8);
      }
      if (i >= 4 && seen[pattern] >= MIN_PATTERN && won[pattern] / seen[pattern] > totalWon / i) take(9);
      if (i % 2 === 0) take(10);

      // update state with round i
      if (i >= 4) { seen[pattern]++; won[pattern] += win; }
      pattern = ((pattern << 1) | win) & 15;
      totalWon += win;
      const slot = i % win10;
      winsInWindow += win - ring[slot];
      ring[slot] = win;
      lossStreak = win ? 0 : lossStreak + 1;
      prevWin = win === 1;
      prev2 = prev;
      prev = m;
    }

    const allWins = wins[0];
    const overall = n > 0 ? allWins / n : NaN;
    let maxAbsZ = 0;
    const rows = rules.map((rule, r) => {
      const b = bets[r];
      const w = wins[r];
      const skipped = n - b;
      const skippedWins = allWins - w;
      const zOdds = b * p >= 5 && b * (1 - p) >= 5 ? (w - b * p) / Math.sqrt(b * p * (1 - p)) : NaN;
      // two-proportion test: the rounds this rule bet versus the rounds it skipped
      const testable = b * overall >= 5 && skipped * overall >= 5 && b * (1 - overall) >= 5 && skipped * (1 - overall) >= 5;
      const zPick = testable
        ? (w / b - skippedWins / skipped) / Math.sqrt(overall * (1 - overall) * (1 / b + 1 / skipped))
        : NaN;
      if (testable && Math.abs(zPick) > maxAbsZ) maxAbsZ = Math.abs(zPick);
      return {
        id: rule.id,
        name: rule.name,
        note: rule.note,
        bets: b,
        wins: w,
        hitRate: b > 0 ? w / b : NaN,
        // win rate minus the win rate of every round in the sample
        lift: b > 0 ? w / b - overall : NaN,
        zOdds,
        zPick,
        luck: testable ? twoSidedP(zPick) : NaN,
        // flat one-unit bets: each win pays target-1, each loss costs 1
        profit: w * (target - 1) - (b - w),
      };
    });
    return { target, p, rounds: n, overall, rules: rows, maxAbsZ };
  }

  /** Observed versus exact win rate for a list of targets. */
  function frequencyTable(mult, targets) {
    return targets.map((target) => {
      let hits = 0;
      for (let i = 0; i < mult.length; i++) if (mult[i] >= target) hits++;
      const p = Core.winProbability(target);
      const n = mult.length;
      return {
        target,
        hits,
        observed: n > 0 ? hits / n : NaN,
        expected: p,
        z: n * p >= 5 ? (hits - n * p) / Math.sqrt(n * p * (1 - p)) : NaN,
      };
    });
  }

  return {
    twoSidedP, quantile,
    lgamma, binomialTail, binomialQuantile, sessionOutlook,
    runProbability, longestStreak,
    seededRng, cryptoRng,
    DEFAULT_PLAN, normalizePlan, simulateSession, createTally, monteCarlo,
    predictorBench, frequencyTable,
  };
});
