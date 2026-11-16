'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const Core = require('../src/limbo-core.js');

/* Small deterministic generator so failures are reproducible. */
function prng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0);
  };
}
function randomBytes(next, len) {
  const b = new Uint8Array(len);
  for (let i = 0; i < len; i++) b[i] = next() & 255;
  return b;
}

test('SHA-256 matches the FIPS 180-4 examples', () => {
  assert.equal(Core.sha256Hex(''), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  assert.equal(Core.sha256Hex('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(
    Core.sha256Hex('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'),
    '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
  );
});

test('SHA-256 matches node:crypto for every length from 0 to 300 bytes', () => {
  const next = prng(1);
  for (let len = 0; len <= 300; len++) {
    const data = randomBytes(next, len);
    const want = crypto.createHash('sha256').update(data).digest('hex');
    assert.equal(Core.sha256Hex(data), want, `length ${len}`);
  }
});

test('HMAC-SHA256 matches the RFC 4231 test cases', () => {
  // Test case 1
  assert.equal(
    Core.toHex(Core.hmacSha256(Core.fromHex('0b'.repeat(20)), 'Hi There')),
    'b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7',
  );
  // Test case 2
  assert.equal(
    Core.toHex(Core.hmacSha256('Jefe', 'what do ya want for nothing?')),
    '5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843',
  );
  // Test case 6: key longer than one block
  assert.equal(
    Core.toHex(Core.hmacSha256(Core.fromHex('aa'.repeat(131)), 'Test Using Larger Than Block-Size Key - Hash Key First')),
    '60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54',
  );
});

test('HMAC-SHA256 matches node:crypto for random keys and messages', () => {
  const next = prng(2);
  for (let i = 0; i < 3000; i++) {
    const key = randomBytes(next, next() % 150);
    const msg = randomBytes(next, next() % 200);
    const want = crypto.createHmac('sha256', key).update(msg).digest('hex');
    assert.equal(Core.toHex(Core.hmacSha256(key, msg)), want, `case ${i}`);
  }
});

test('hex helpers round-trip and reject bad input', () => {
  const next = prng(3);
  const bytes = randomBytes(next, 64);
  assert.deepEqual(Core.fromHex(Core.toHex(bytes)), bytes);
  assert.equal(Core.isHex('zz'), false);
  assert.equal(Core.isHex('abc'), false);
  assert.throws(() => Core.fromHex('xyz'));
});

/*
 * Published vectors from the open-source `fair` crate (docs.rs/crate/fair),
 * whose formula was taken from the casino's client-side verification code.
 */
const VECTORS = [
  { server: 'server seed', client: 'client seed', nonce: 1, want: 1.32 },
  { server: 'server seed', client: 'client seed', nonce: 2, want: 1.83 },
  { server: 'server seed', client: 'client seed', nonce: 3, want: 4.28 },
  {
    server: 'e8df2cc3b9ccb583ce5ea92336842387',
    client: '83e27f682128eb1852b048203dfd6931',
    nonce: 1942124,
    want: 3807692.3,
  },
];

test('Limbo results match the published test vectors', () => {
  for (const v of VECTORS) {
    const r = Core.limboResult(v.server, v.client, v.nonce);
    assert.equal(r.multiplier, v.want, `${v.client}:${v.nonce}`);
    assert.equal(r.message, `${v.client}:${v.nonce}:0`);
  }
});

test('the formula printed in the public docs fails the tiny-float vector', () => {
  const v = VECTORS[3];
  const r = Core.limboResult(v.server, v.client, v.nonce);
  assert.equal(Core.documentedMultiplierFromUint32(r.uint32), 3940702.15);
  assert.notEqual(Core.documentedMultiplierFromUint32(r.uint32), v.want);
});

test('limboResult exposes consistent intermediate values', () => {
  const r = Core.limboResult('server seed', 'client seed', 1);
  assert.equal(r.hmacHex, 'bf27f216a44fb12d42a468efe880471991877549e22ea9c0abaad93ba53603d4');
  assert.deepEqual(r.bytes, [0xbf, 0x27, 0xf2, 0x16]);
  assert.equal(r.uint32, 0xbf27f216);
  assert.equal(r.float, 0.7467032722197473);
  // The float is exactly the documented byte-by-byte sum.
  const sum = r.bytes.reduce((acc, b, i) => acc + b / 256 ** (i + 1), 0);
  assert.equal(r.float, sum);
});

test('replay agrees with limboResult and with node:crypto', () => {
  const next = prng(4);
  for (let round = 0; round < 40; round++) {
    const server = Core.toHex(randomBytes(next, 32));
    const client = Core.toHex(randomBytes(next, 1 + (next() % 12)));
    const start = next() % 2000000;
    const count = 60;
    const raw = Core.replayUint32(server, client, start, count);
    const mult = Core.replay(server, client, start, count);
    for (let i = 0; i < count; i++) {
      const nonce = start + i;
      const digest = crypto.createHmac('sha256', server).update(`${client}:${nonce}:0`).digest();
      assert.equal(raw[i], digest.readUInt32BE(0), `uint32 at nonce ${nonce}`);
      assert.equal(mult[i], Core.limboResult(server, client, nonce).multiplier, `multiplier at nonce ${nonce}`);
    }
  }
});

test('replay handles nonce digit rollovers and non-ASCII client seeds', () => {
  const server = 'a'.repeat(64);
  for (const client of ['x', 'héllo-世界', '']) {
    for (const start of [0, 8, 98, 999, 99998, 4294967290]) {
      const raw = Core.replayUint32(server, client, start, 5);
      for (let i = 0; i < 5; i++) {
        const digest = crypto.createHmac('sha256', server).update(`${client}:${start + i}:0`).digest();
        assert.equal(raw[i], digest.readUInt32BE(0), `${client}:${start + i}`);
      }
    }
  }
});

test('multiplier range: 1.00 at the top of the float range, 99,000,000 at the bottom', () => {
  assert.equal(Core.multiplierFromUint32(0), Core.MAX_MULTIPLIER);
  assert.equal(Core.multiplierFromUint32(4294967295), 1);
  // floor(float * 1e8) is 0 for k <= 42, so those draws all hit the maximum.
  assert.equal(Core.multiplierFromUint32(42), 99000000);
  assert.equal(Core.multiplierFromUint32(43), 49500000);
});

test('multiplier never increases as the draw grows (sampled)', () => {
  const next = prng(5);
  for (let i = 0; i < 200000; i++) {
    const a = next();
    const b = next();
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    assert.ok(Core.multiplierFromUint32(lo) >= Core.multiplierFromUint32(hi));
  }
  // and densely across a window that straddles several result steps
  let prev = Infinity;
  for (let k = 2126008811 - 5000; k < 2126008811 + 5000; k++) {
    const m = Core.multiplierFromUint32(k);
    assert.ok(m <= prev);
    prev = m;
  }
});

test('winCount is the exact boundary between winning and losing draws', () => {
  for (const target of [1.01, 1.1, 1.5, 2, 3, 7, 10, 13.37, 100, 1000, 99999.99, 1000000]) {
    const n = Core.winCount(target);
    assert.ok(n > 0 && n < Core.TWO32);
    assert.ok(Core.multiplierFromUint32(n - 1) >= target, `last winning draw for ${target}`);
    assert.ok(Core.multiplierFromUint32(n) < target, `first losing draw for ${target}`);
  }
  assert.equal(Core.winCount(1), Core.TWO32);
  assert.equal(Core.winCount(0.5), Core.TWO32);
  assert.equal(Core.winCount(99000000), 43);
  assert.equal(Core.winCount(99000000.01), 0);
});

test('win chance is 99/target percent and the edge is 1% for ordinary targets', () => {
  for (const target of [1.01, 1.5, 2, 3, 5, 10, 50, 100, 1000, 10000]) {
    const o = Core.odds(target);
    // The draw is one of 2^32 values, so the chance sits within one draw of 0.99/target
    // after the divisor is rounded down to a whole number of 1e-8 steps.
    assert.ok(Math.abs(o.p - 0.99 / target) < 2e-8, `p for ${target}`);
    assert.ok(o.edge > 0.01 - 1e-6 && o.edge < 0.0101, `edge for ${target}: ${o.edge}`);
  }
});

test('win chance matches a direct count over real replayed results', () => {
  const mult = Core.replay('f'.repeat(64), 'count-check', 1, 400000);
  for (const target of [1.5, 2, 10]) {
    let wins = 0;
    for (let i = 0; i < mult.length; i++) if (mult[i] >= target) wins++;
    const p = Core.winProbability(target);
    const z = (wins - mult.length * p) / Math.sqrt(mult.length * p * (1 - p));
    assert.ok(Math.abs(z) < 4.5, `target ${target}: z = ${z}`);
  }
});

test('checkCommitment recognises the text and raw-byte conventions', () => {
  const seed = '3c1f0e7a9b2d4c6e8f0a1b3c5d7e9f00112233445566778899aabbccddeeff00';
  const textHash = crypto.createHash('sha256').update(seed).digest('hex');
  const byteHash = crypto.createHash('sha256').update(Buffer.from(seed, 'hex')).digest('hex');
  assert.deepEqual(Core.checkCommitment(seed, textHash), { match: true, mode: 'text', computed: textHash });
  assert.equal(Core.checkCommitment(seed, byteHash.toUpperCase()).mode, 'bytes');
  assert.equal(Core.checkCommitment(`  ${seed}\n`, textHash).match, true);
  const wrong = Core.checkCommitment(seed, '00'.repeat(32));
  assert.equal(wrong.match, false);
  assert.equal(wrong.computed, textHash);
});

test('invalid nonces are rejected', () => {
  assert.throws(() => Core.limboResult('s', 'c', -1));
  assert.throws(() => Core.limboResult('s', 'c', 1.5));
  assert.throws(() => Core.replayUint32('s', 'c', 0, -1));
});
