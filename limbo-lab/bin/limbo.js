#!/usr/bin/env node
'use strict';
const Core = require('../src/limbo-core.js');
const Sim = require('../src/limbo-sim.js');

const HELP = `limbo-lab: verify, replay and analyse provably fair Limbo results

Usage
  limbo verify   --server <seed> --client <seed> [--nonce 1] [--count 1] [--hash <hashed seed>] [--target 2] [--steps]
  limbo hash     --server <seed>
  limbo odds     --target <x> [--bets 1000]
  limbo patterns --server <seed> --client <seed> [--nonce 1] [--count 100000] [--target 2]
  limbo simulate [--balance 1000] [--bet 1] [--target 2] [--on-loss reset|<percent>] [--on-win reset|<percent>]
                 [--bets 1000] [--sessions 5000] [--stop-profit 0] [--stop-loss 0] [--seed <n>]

Commands
  verify    Recompute results from a revealed server seed. --hash also checks the seed against the
            hash that was shown while it was live. --steps prints every intermediate value.
  hash      SHA-256 of a server seed: the commitment a casino shows before the seed is revealed.
  odds      Exact win chance, house edge and losing-streak maths for a target multiplier.
  patterns  Test "predictor" betting rules against the real results of a seed pair.
  simulate  Play a betting plan thousands of times and summarise where it ends up.

A server seed is only revealed after you rotate it. While it is live you see its hash, and the
results of that seed cannot be computed by anyone outside the casino.`;

function parseArgs(argv) {
  const flags = {};
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq > 0) flags[a.slice(2, eq)] = a.slice(eq + 1);
      else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) flags[a.slice(2)] = argv[++i];
      else flags[a.slice(2)] = true;
    } else rest.push(a);
  }
  return { command: rest[0], flags };
}

const fail = (msg) => { process.stderr.write(`error: ${msg}\n\nRun "limbo help" for usage.\n`); process.exit(1); };
const out = (s = '') => process.stdout.write(s + '\n');
const pad = (s, w) => String(s).padEnd(w);
const padL = (s, w) => String(s).padStart(w);
const num = (x, d = 2) => x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x, d = 4) => (Number.isFinite(x) ? (x * 100).toFixed(d) + '%' : 'n/a');
const mult = (x) => num(x, 2) + 'x';

function need(flags, name) {
  if (flags[name] === undefined || flags[name] === true) fail(`--${name} is required`);
  return String(flags[name]);
}
function number(flags, name, fallback, { integer = false, min = -Infinity } = {}) {
  if (flags[name] === undefined) return fallback;
  const v = Number(flags[name]);
  if (!Number.isFinite(v) || (integer && !Number.isInteger(v)) || v < min) {
    fail(`--${name} must be ${integer ? 'a whole number' : 'a number'}${min > -Infinity ? ` of at least ${min}` : ''}`);
  }
  return v;
}

function cmdVerify(flags) {
  const server = need(flags, 'server');
  const client = need(flags, 'client');
  const nonce = number(flags, 'nonce', 1, { integer: true, min: 0 });
  const count = number(flags, 'count', 1, { integer: true, min: 1 });
  const target = number(flags, 'target', NaN, { min: 1.01 });

  if (flags.hash !== undefined) {
    const c = Core.checkCommitment(server, String(flags.hash));
    out(c.match
      ? `commitment   MATCH: SHA-256 of this server seed equals the hash you were shown${c.mode === 'bytes' ? ' (seed hashed as raw bytes)' : ''}.`
      : `commitment   NO MATCH: SHA-256 of this server seed is ${c.computed}`);
    out();
  }

  if (flags.steps || count === 1) {
    for (let i = 0; i < Math.min(count, 20); i++) {
      const r = Core.limboResult(server, client, nonce + i);
      out(`nonce ${nonce + i}`);
      out(`  message      ${r.message}`);
      out(`  HMAC-SHA256  ${r.hmacHex}`);
      out(`  first bytes  ${r.bytes.join(', ')}  (hex ${r.hmacHex.slice(0, 8)})`);
      out(`  float        ${r.bytes.map((b, j) => `${b}/256^${j + 1}`).join(' + ')} = ${r.float}`);
      out(`  raw          1e8 / (floor(float * 1e8) + 1) * 0.99 = 1e8 / ${r.divisor} * 0.99 = ${r.floatPoint}`);
      out(`  result       ${mult(r.multiplier)}${Number.isFinite(target) ? (r.multiplier >= target ? `   WIN at ${mult(target)}` : `   loss at ${mult(target)}`) : ''}`);
    }
    if (count > 20) out(`(--steps prints the first 20 rounds; ${count - 20} more not shown)`);
    return;
  }

  const results = Core.replay(server, client, nonce, count);
  let wins = 0;
  let highest = 0;
  const shown = Math.min(count, 200);
  out(`${pad('nonce', 12)}${padL('result', 16)}`);
  for (let i = 0; i < count; i++) {
    const m = results[i];
    const win = Number.isFinite(target) && m >= target;
    if (win) wins++;
    if (m > highest) highest = m;
    if (i < shown) out(`${pad(nonce + i, 12)}${padL(mult(m), 16)}${Number.isFinite(target) ? (win ? '   WIN' : '') : ''}`);
  }
  if (count > shown) out(`... ${count - shown} more rounds not listed`);
  out();
  out(`rounds       ${count} (nonces ${nonce}..${nonce + count - 1})`);
  out(`highest      ${mult(highest)}`);
  if (Number.isFinite(target)) {
    out(`wins at ${mult(target)}  ${wins} of ${count} = ${pct(wins / count, 2)}  (exact chance ${pct(Core.winProbability(target), 4)})`);
  }
}

function cmdHash(flags) {
  const server = need(flags, 'server');
  out(Core.sha256Hex(server.trim()));
}

function cmdOdds(flags) {
  const target = number(flags, 'target', NaN, { min: 1.01 });
  if (!Number.isFinite(target)) fail('--target is required');
  const bets = number(flags, 'bets', 1000, { integer: true, min: 1 });
  const o = Core.odds(target);
  if (o.winCount === 0) fail(`no result can reach ${mult(target)}; the largest possible result is ${mult(Core.MAX_MULTIPLIER)}`);
  out(`target            ${mult(target)}`);
  out(`win chance        ${pct(o.p, 6)}   (${num(o.winCount, 0)} of 4,294,967,296 possible draws; about 1 win in ${num(1 / o.p, 2)} rounds)`);
  out(`paid on a win     ${num(target, 2)} per 1 staked (profit ${num(target - 1, 2)})`);
  out(`return to player  ${pct(o.rtp, 6)}`);
  out(`house edge        ${pct(o.edge, 6)}   (average loss of ${num(o.edge * 1000, 2)} per 1,000 wagered, on every target)`);
  out();
  out(`losing streaks within ${num(bets, 0)} bets`);
  for (const [chance, label] of [[0.5, 'more likely than not'], [0.05, '1 session in 20'], [0.01, '1 session in 100']]) {
    out(`  ${pad(label, 22)} a run of ${Sim.longestStreak(o.p, bets, chance)} or more losses in a row`);
  }
}

function cmdPatterns(flags) {
  const server = need(flags, 'server');
  const client = need(flags, 'client');
  const nonce = number(flags, 'nonce', 1, { integer: true, min: 0 });
  const count = number(flags, 'count', 100000, { integer: true, min: 1 });
  const target = number(flags, 'target', 2, { min: 1.01 });
  const b = Sim.predictorBench(Core.replay(server, client, nonce, count), target);
  out(`${num(count, 0)} rounds at ${mult(target)}: exact win chance ${pct(b.p)}, this sample won ${pct(b.overall)}`);
  out();
  out(`${pad('rule', 44)}${padL('bets', 10)}${padL('win rate', 11)}${padL('vs all', 10)}${padL('z', 7)}  verdict`);
  for (const r of b.rules) {
    const base = r.id === 'always';
    const verdict = base ? 'baseline' : !Number.isFinite(r.zPick) ? 'too few rounds to judge' : Math.abs(r.zPick) < 2 ? 'luck' : Math.abs(r.zPick) < 3 ? 'luck (happens in 1 test of 20)' : 'unusual: re-test on another seed pair';
    out(`${pad(r.name, 44)}${padL(num(r.bets, 0), 10)}${padL(pct(r.hitRate, 2), 11)}` +
      `${padL(base || !Number.isFinite(r.lift) ? '' : (r.lift >= 0 ? '+' : '') + (r.lift * 100).toFixed(2), 10)}` +
      `${padL(base || !Number.isFinite(r.zPick) ? '' : r.zPick.toFixed(2), 7)}  ${verdict}`);
  }
  out();
  out('"vs all" is the rule\'s win rate minus the win rate of every round, in percentage points.');
  out('z compares the rounds a rule bet with the rounds it skipped; beyond about 3 would be worth a second look,');
  out('and a real pattern would have to repeat on every other seed pair.');
}

function cmdSimulate(flags) {
  const side = (name) => {
    const v = flags[name];
    if (v === undefined || v === 'reset') return { action: 'reset', percent: 0 };
    const percent = Number(v);
    if (!Number.isFinite(percent)) fail(`--${name} must be "reset" or a percent such as 100`);
    return { action: 'increase', percent };
  };
  const plan = {
    balance: number(flags, 'balance', 1000),
    baseBet: number(flags, 'bet', 1),
    target: number(flags, 'target', 2),
    onLoss: side('on-loss'),
    onWin: side('on-win'),
    maxBets: number(flags, 'bets', 1000, { integer: true, min: 1 }),
    stopProfit: number(flags, 'stop-profit', 0, { min: 0 }),
    stopLoss: number(flags, 'stop-loss', 0, { min: 0 }),
  };
  const sessions = number(flags, 'sessions', 5000, { integer: true, min: 1 });
  const rng = flags.seed !== undefined ? Sim.seededRng(number(flags, 'seed', 1, { integer: true })) : Sim.cryptoRng();
  let r;
  try { r = Sim.monteCarlo(plan, sessions, rng, 0); } catch (e) { fail(e.message); }
  const describe = (s) => (s.action === 'reset' ? 'back to the base bet' : `bet ${s.percent >= 0 ? '+' : ''}${s.percent}%`);
  out(`plan              start ${num(plan.balance)}, base bet ${num(plan.baseBet)}, target ${mult(plan.target)}, up to ${num(plan.maxBets, 0)} bets`);
  out(`                  after a loss: ${describe(plan.onLoss)}; after a win: ${describe(plan.onWin)}`);
  out(`sessions          ${num(sessions, 0)}`);
  out();
  out(`ended ahead       ${pct(r.aheadRate, 1)}`);
  out(`ended behind      ${pct(r.behindRate, 1)}`);
  out(`could not cover the next bet   ${pct(r.bustRate, 1)}`);
  out(`final balance     average ${num(r.final.mean)}, median ${num(r.final.median)}`);
  out(`                  worst 5% below ${num(r.final.p5)}, best 5% above ${num(r.final.p95)}`);
  out(`largest bet       ${num(r.largestBet)}`);
  out(`longest losing run ${r.longestLossStreak}`);
  out();
  out(`average wagered   ${num(r.meanWagered)} per session`);
  out(`average result    ${num(r.meanNet)} per session`);
  out(`house edge alone  ${num(r.expectedNet)} per session   (1% of what was wagered: what the average converges to)`);
}

function main() {
  const { command, flags } = parseArgs(process.argv.slice(2));
  switch (command) {
    case 'verify': return cmdVerify(flags);
    case 'hash': return cmdHash(flags);
    case 'odds': return cmdOdds(flags);
    case 'patterns': return cmdPatterns(flags);
    case 'simulate': return cmdSimulate(flags);
    case undefined:
    case 'help': return out(HELP);
    default: return fail(`unknown command "${command}"`);
  }
}

main();
