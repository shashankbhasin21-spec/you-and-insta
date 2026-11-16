#!/usr/bin/env node
/*
 * Can the next Limbo result be predicted from earlier results?
 *
 * Replays millions of consecutive rounds per seed pair with the exact
 * algorithm and runs a battery of tests for any structure a predictor could
 * use: uneven draws, wrong frequencies, memory between rounds, "due" wins,
 * and betting rules that only look at the past.
 *
 * Three safeguards against fooling ourselves:
 *   - several independent seed pairs, pooled: a real pattern adds up across
 *     pairs, luck cancels out;
 *   - a control: the identical battery on AES-256 keystream noise, which shows
 *     what the scores of genuinely patternless data look like (the tests
 *     overlap, so this is the honest yardstick rather than a textbook table);
 *   - replication: the most extreme scores from seed pair 1 are looked up on
 *     the other pairs.
 *
 * Usage: node research/predictability.js [rounds per run] [seed pairs]
 * Seeds are fixed, so the report is reproducible.
 */
'use strict';
const crypto = require('node:crypto');
const Core = require('../src/limbo-core.js');
const Sim = require('../src/limbo-sim.js');

const N = Number(process.argv[2] || 10000000);
const RUNS = Number(process.argv[3] || 6);

const fmt = (x, d = 4) => (Number.isFinite(x) ? x.toFixed(d) : 'n/a');
const pad = (s, w) => String(s).padEnd(w);
const padL = (s, w) => String(s).padStart(w);
const zTag = (z) => (!Number.isFinite(z) ? 'too few rounds' : Math.abs(z) < 2 ? 'noise' : Math.abs(z) < 3 ? 'within luck' : Math.abs(z) < 4 ? 'unusual' : 'SIGNAL');
const count = (n) => n.toLocaleString('en-US');
const out = (s = '') => process.stdout.write(s + '\n');

/** z-score of one group's win rate against the win rate of every round outside the group. */
function pickZ(wins, rounds, allWins, total) {
  const rest = total - rounds;
  const pooled = allWins / total;
  if (rounds * pooled < 5 || rest * pooled < 5 || rounds * (1 - pooled) < 5 || rest * (1 - pooled) < 5) return NaN;
  return (wins / rounds - (allWins - wins) / rest) / Math.sqrt(pooled * (1 - pooled) * (1 / rounds + 1 / rest));
}

/** Limbo draws for one seed pair: the real algorithm, nonces 1..N. */
function limboDraws(i) {
  const server = Core.sha256Hex(`limbo-lab research server seed #${i}`);
  const client = `limbo-lab-research-${i}`;
  return { server, client, draws: Core.replayUint32(server, client, 1, N) };
}

/** Control draws: AES-256-CTR keystream, a different primitive with no Limbo in it. */
function controlDraws(i) {
  const key = crypto.createHash('sha256').update(`limbo-lab control key #${i}`).digest();
  const cipher = crypto.createCipheriv('aes-256-ctr', key, Buffer.alloc(16));
  const draws = new Uint32Array(N);
  const CHUNK = 1 << 20;
  const zeros = Buffer.alloc(CHUNK * 4);
  for (let off = 0; off < N; off += CHUNK) {
    const n = Math.min(CHUNK, N - off);
    const ks = cipher.update(n === CHUNK ? zeros : zeros.subarray(0, n * 4));
    for (let j = 0; j < n; j++) draws[off + j] = ks.readUInt32BE(j * 4);
  }
  return { draws };
}

/**
 * Run every test on one sequence of draws. Returns [{ label, z }] so the same
 * tests can be compared across runs. Prints the full tables if verbose.
 */
function battery(draws, verbose) {
  const line = (s = '') => { if (verbose) out(s); };
  const tests = [];
  const record = (label, z) => { if (Number.isFinite(z)) tests.push({ label, z }); return z; };

  const mult = new Float64Array(N);
  for (let i = 0; i < N; i++) mult[i] = Core.multiplierFromUint32(draws[i]);
  if (verbose) {
    const first = Math.min(N, 1000000);
    line(`checksum      draws ${crypto.createHash('sha256').update(Buffer.from(draws.buffer, 0, first * 4)).digest('hex').slice(0, 16)}` +
      `  results ${crypto.createHash('sha256').update(Buffer.from(mult.buffer, 0, first * 8)).digest('hex').slice(0, 16)}` +
      `  (first ${count(first)} rounds; research/ml_attack.py prints the same)`);
  }

  /* 1. Is the raw 32-bit draw uniform? */
  line('\n1. RAW DRAW UNIFORMITY');
  {
    const chi = (label, bins, index) => {
      const counts = new Float64Array(bins);
      for (let i = 0; i < N; i++) counts[index(draws[i])]++;
      const e = N / bins;
      let x = 0;
      for (let b = 0; b < bins; b++) x += (counts[b] - e) ** 2 / e;
      const df = bins - 1;
      const z = record(`uniformity: ${label}`, (x - df) / Math.sqrt(2 * df));
      line(`   ${pad(label, 26)}chi2 = ${padL(fmt(x, 1), 7)}  (expect ${df} +- ${fmt(Math.sqrt(2 * df), 1)})  z = ${padL(fmt(z, 2), 6)}  ${zTag(z)}`);
    };
    chi('first byte, 256 bins', 256, (k) => k >>> 24);
    chi('fourth byte, 256 bins', 256, (k) => k & 255);
    chi('float, 1000 equal bins', 1000, (k) => Math.floor(k / 4294967296 * 1000));
    let worst = 0;
    for (let bit = 0; bit < 32; bit++) {
      let ones = 0;
      for (let i = 0; i < N; i++) ones += (draws[i] >>> bit) & 1;
      const z = record(`uniformity: bit ${bit} of the draw`, (ones - N / 2) / Math.sqrt(N / 4));
      if (Math.abs(z) > worst) worst = Math.abs(z);
    }
    line(`   ${pad('each of the 32 bits', 26)}largest |z| = ${fmt(worst, 2)}  (32 fair bits typically give 1.8-2.7)`);
  }

  /* 2. Does each band of results come up as often as the exact odds say? Bands do not overlap. */
  line('\n2. HOW OFTEN EACH BAND OF RESULTS COMES UP, VERSUS THE EXACT ODDS');
  line(`   ${pad('result', 20)}${padL('rounds', 11)}${padL('observed %', 13)}${padL('exact %', 12)}${padL('z', 8)}`);
  {
    const edges = [1.01, 1.5, 2, 3, 5, 10, 100, 1000];
    const counts = new Float64Array(edges.length + 1);
    for (let i = 0; i < N; i++) {
      let b = 0;
      while (b < edges.length && mult[i] >= edges[b]) b++;
      counts[b]++;
    }
    for (let b = 0; b <= edges.length; b++) {
      const atLeast = b === 0 ? 1 : Core.winProbability(edges[b - 1]);
      const above = b === edges.length ? 0 : Core.winProbability(edges[b]);
      const p = atLeast - above;
      const name = b === 0 ? '1.00x exactly' : b === edges.length ? `${edges[b - 1]}x or more` : `${edges[b - 1]}x to under ${edges[b]}x`;
      const z = record(`frequency of results: ${name}`, (counts[b] - N * p) / Math.sqrt(N * p * (1 - p)));
      line(`   ${pad(name, 20)}${padL(counts[b], 11)}${padL(fmt(counts[b] / N * 100, 4), 13)}${padL(fmt(p * 100, 4), 12)}${padL(fmt(z, 2), 8)}  ${zTag(z)}`);
    }
  }

  /* 3. Does a round remember earlier rounds? */
  line('\n3. MEMORY BETWEEN ROUNDS (win/loss at 2.00x correlated with the round k steps earlier)');
  {
    const w = new Uint8Array(N);
    let total = 0;
    for (let i = 0; i < N; i++) { w[i] = mult[i] >= 2 ? 1 : 0; total += w[i]; }
    const mean = total / N; // sample mean, so this measures dependence and not the overall rate
    let worst = 0;
    const parts = [];
    for (let lag = 1; lag <= 20; lag++) {
      let both = 0;
      for (let i = lag; i < N; i++) both += w[i] & w[i - lag];
      const n = N - lag;
      const r = (both / n - mean * mean) / (mean * (1 - mean));
      const z = record(`memory: win/loss at 2x vs ${lag} round(s) earlier`, r * Math.sqrt(n));
      if (Math.abs(z) > worst) worst = Math.abs(z);
      parts.push(`${lag}:${fmt(z, 2)}`);
    }
    line(`   z by lag   ${parts.slice(0, 10).join('  ')}`);
    line(`              ${parts.slice(10).join('  ')}`);
    line(`   largest |z| over 20 lags = ${fmt(worst, 2)}  (20 independent lags typically give 1.6-2.6)`);
  }

  /* 4. Is a win "due" after a losing streak? Each bucket is compared with all the other rounds. */
  for (const [target, width, buckets] of [[2, 1, 13], [10, 10, 7]]) {
    const p = Core.winProbability(target);
    line(`\n4. IS A WIN "DUE"?  Win rate at ${target.toFixed(2)}x by the number of losses in a row just before (exact chance ${fmt(p * 100, 4)}%)`);
    line(`   ${pad('losses before', 16)}${padL('rounds', 11)}${padL('win %', 11)}${padL('z', 8)}   (z: this row against all other rounds)`);
    const seen = new Float64Array(buckets);
    const won = new Float64Array(buckets);
    let streak = 0;
    let allWins = 0;
    for (let i = 0; i < N; i++) {
      const win = mult[i] >= target ? 1 : 0;
      const b = Math.min(Math.floor(streak / width), buckets - 1);
      seen[b]++;
      won[b] += win;
      allWins += win;
      streak = win ? 0 : streak + 1;
    }
    for (let b = 0; b < buckets; b++) {
      const lo = b * width;
      const name = b === buckets - 1 ? `${lo} or more` : width === 1 ? `${lo}` : `${lo}-${lo + width - 1}`;
      const z = record(`"due" check: win rate at ${target}x after ${name} losses`, pickZ(won[b], seen[b], allWins, N));
      line(`   ${pad(name, 16)}${padL(seen[b], 11)}${padL(fmt(won[b] / seen[b] * 100, 4), 11)}${padL(fmt(z, 2), 8)}  ${zTag(z)}`);
    }
  }

  /* 5. Betting rules that only look at the past: do they pick better rounds than the ones they skip? */
  for (const target of [1.5, 2, 10, 100]) {
    const b = Sim.predictorBench(mult, target);
    line(`\n5. PREDICTOR RULES at ${target.toFixed(2)}x  (exact win chance ${fmt(b.p * 100, 4)}%; all rounds in this sample won ${fmt(b.overall * 100, 4)}%)`);
    line(`   ${pad('rule', 44)}${padL('bets', 10)}${padL('win %', 10)}${padL('vs all', 9)}${padL('z', 8)}${padL('profit/bet', 12)}`);
    for (const r of b.rules) {
      const z = r.id === 'always' ? NaN : record(`rule at ${target}x: ${r.name}`, r.zPick);
      line(`   ${pad(r.name, 44)}${padL(r.bets, 10)}${padL(fmt(r.hitRate * 100, 4), 10)}${padL(r.id === 'always' ? '' : (r.lift >= 0 ? '+' : '') + fmt(r.lift * 100, 4), 9)}` +
        `${padL(r.id === 'always' ? '' : fmt(z, 2), 8)}${padL(fmt(r.profit / r.bets, 4), 12)}  ${r.id === 'always' ? 'baseline' : zTag(z)}`);
    }
  }
  return tests;
}

const largest = (tests) => tests.reduce((m, t) => Math.max(m, Math.abs(t.z)), 0);
const beyond = (tests, limit) => tests.filter((t) => Math.abs(t.z) >= limit).length;
/** Combine one test's z across runs (Stouffer): still a standard normal if there is no pattern. */
function pool(runs) {
  return runs[0].map((t, i) => ({ label: t.label, z: runs.reduce((s, r) => s + r[i].z, 0) / Math.sqrt(runs.length) }));
}

out('LIMBO PREDICTABILITY STUDY');
out('==========================');
out(`${RUNS} seed pairs and ${RUNS} control runs, ${count(N)} rounds each.\n`);

const limbo = [];
for (let i = 1; i <= RUNS; i++) {
  const src = limboDraws(i);
  if (i === 1) {
    out('SEED PAIR 1 IN FULL');
    out(`rounds        ${count(N)}  (nonces 1..${N})`);
    out(`server seed   ${src.server}`);
    out(`  its SHA-256 ${Core.sha256Hex(src.server)}   <- all a player sees while the seed is live`);
    out(`client seed   ${src.client}`);
  }
  limbo.push(battery(src.draws, i === 1));
}
const control = [];
for (let i = 1; i <= RUNS; i++) control.push(battery(controlDraws(i).draws, false));

const T = limbo[0].length;
out('\nSCOREBOARD');
out(`   ${T} tests per run. z measures how far a result sits from "no pattern", in standard errors.`);
out(`   ${pad('run', 26)}${padL('largest |z|', 13)}${padL('|z| >= 2', 10)}${padL('|z| >= 3', 10)}`);
limbo.forEach((r, i) => out(`   ${pad(`Limbo seed pair ${i + 1}`, 26)}${padL(fmt(largest(r), 2), 13)}${padL(beyond(r, 2), 10)}${padL(beyond(r, 3), 10)}`));
control.forEach((r, i) => out(`   ${pad(`control (AES noise) ${i + 1}`, 26)}${padL(fmt(largest(r), 2), 13)}${padL(beyond(r, 2), 10)}${padL(beyond(r, 3), 10)}`));
const avg = (runs, f) => runs.reduce((s, r) => s + f(r), 0) / runs.length;
out(`   ${pad('Limbo average', 26)}${padL(fmt(avg(limbo, largest), 2), 13)}${padL(fmt(avg(limbo, (r) => beyond(r, 2)), 1), 10)}${padL(fmt(avg(limbo, (r) => beyond(r, 3)), 1), 10)}`);
out(`   ${pad('control average', 26)}${padL(fmt(avg(control, largest), 2), 13)}${padL(fmt(avg(control, (r) => beyond(r, 2)), 1), 10)}${padL(fmt(avg(control, (r) => beyond(r, 3)), 1), 10)}`);
out(`   ${pad('theory, no pattern', 26)}${padL('', 13)}${padL(fmt(T * 0.0455, 1), 10)}${padL(fmt(T * 0.0027, 1), 10)}`);

const pooledLimbo = pool(limbo);
const pooledControl = pool(control);
out(`\nPOOLED OVER ALL ${RUNS} RUNS (${count(N * RUNS)} rounds): a real pattern would grow here, luck averages out`);
out(`   ${pad('', 26)}${padL('largest |z|', 13)}${padL('|z| >= 2', 10)}${padL('|z| >= 3', 10)}`);
out(`   ${pad('Limbo', 26)}${padL(fmt(largest(pooledLimbo), 2), 13)}${padL(beyond(pooledLimbo, 2), 10)}${padL(beyond(pooledLimbo, 3), 10)}`);
out(`   ${pad('control (AES noise)', 26)}${padL(fmt(largest(pooledControl), 2), 13)}${padL(beyond(pooledControl, 2), 10)}${padL(beyond(pooledControl, 3), 10)}`);
for (const [name, pooled] of [['Limbo', pooledLimbo], ['control', pooledControl]]) {
  out(`   Most extreme pooled ${name} tests:`);
  for (const t of pooled.slice().sort((x, y) => Math.abs(y.z) - Math.abs(x.z)).slice(0, 5)) {
    out(`     ${pad(t.label, 62)}${padL(fmt(t.z, 2), 8)}`);
  }
}

out(`\nREPLICATION: the most extreme scores on seed pair 1, looked up on the other ${RUNS - 1} seed pairs`);
out(`   ${pad('test', 62)}${padL('pair 1', 8)}  others`);
const others = limbo.slice(1);
const top = limbo[0].map((t, i) => ({ t, i })).sort((x, y) => Math.abs(y.t.z) - Math.abs(x.t.z)).slice(0, 8);
let repeats = 0;
for (const { t, i } of top) {
  const zs = others.map((r) => r[i].z);
  const same = zs.filter((z) => Math.sign(z) === Math.sign(t.z) && Math.abs(z) >= 2).length;
  repeats += same;
  out(`   ${pad(t.label, 62)}${padL(fmt(t.z, 2), 8)}  ${zs.map((z) => padL(fmt(z, 2), 6)).join(' ')}`);
}
out(`   ${repeats} of ${top.length * others.length} follow-up scores land beyond 2 in the same direction (luck alone gives about ${fmt(top.length * others.length * 0.0228, 1)}).`);
out(`\n   For scale: a prediction edge of 1 percentage point at 2.00x would score z = ${fmt(0.01 * Math.sqrt(N) / Math.sqrt(0.495 * 0.505), 0)} on one seed pair` +
  ` and z = ${fmt(0.01 * Math.sqrt(N * RUNS) / Math.sqrt(0.495 * 0.505), 0)} pooled.`);
