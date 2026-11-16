/*!
 * limbo-core
 *
 * Exact, dependency-free implementation of the provably fair Limbo result
 * algorithm used by Stake-style casinos:
 *
 *   digest = HMAC-SHA256(key = serverSeed, message = `${clientSeed}:${nonce}:0`)
 *   float  = first 4 digest bytes read as a base-256 fraction  (0 <= float < 1)
 *   raw    = 1e8 / (floor(float * 1e8) + 1) * 0.99
 *   result = max(floor(raw * 100) / 100, 1)
 *
 * The same file runs unchanged in Node (require) and in a browser (<script>).
 * SHA-256 and HMAC are implemented here in plain JavaScript so that results can
 * be replayed synchronously and in bulk; test/ checks them against node:crypto.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.LimboCore = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * Bytes and hex
   * ------------------------------------------------------------------ */

  const encoder = new TextEncoder();
  const utf8 = (s) => encoder.encode(String(s));

  const HEX = '0123456789abcdef';
  function toHex(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += HEX[bytes[i] >> 4] + HEX[bytes[i] & 15];
    return s;
  }
  const isHex = (s) => typeof s === 'string' && s.length % 2 === 0 && /^[0-9a-fA-F]*$/.test(s);
  function fromHex(s) {
    if (!isHex(s)) throw new Error('not a hex string');
    const out = new Uint8Array(s.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
    return out;
  }

  /* ------------------------------------------------------------------ *
   * SHA-256 (FIPS 180-4)
   * ------------------------------------------------------------------ */

  const K = new Int32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);
  const IV = new Int32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const W = new Int32Array(64);

  /** One SHA-256 compression: folds the 64-byte block at p[off..off+64) into state H. */
  function compress(H, p, off) {
    for (let i = 0; i < 16; i++) {
      const j = off + (i << 2);
      W[i] = (p[j] << 24) | (p[j + 1] << 16) | (p[j + 2] << 8) | p[j + 3];
    }
    for (let i = 16; i < 64; i++) {
      const x = W[i - 15];
      const y = W[i - 2];
      const s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
      const s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
    }
    let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + W[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0;
      d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
    H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
  }

  const TAIL = new Uint8Array(128);

  /**
   * Absorb data[start..end) and the SHA-256 padding into H.
   * totalLen is the length in bytes of the whole message, including anything
   * that was compressed into H before this call (a multiple of 64 bytes).
   */
  function finish(H, data, start, end, totalLen) {
    let off = start;
    while (end - off >= 64) { compress(H, data, off); off += 64; }
    const rem = end - off;
    const tailLen = rem < 56 ? 64 : 128;
    TAIL.fill(0, 0, tailLen);
    for (let i = 0; i < rem; i++) TAIL[i] = data[off + i];
    TAIL[rem] = 0x80;
    const bitsHi = Math.floor(totalLen / 0x20000000);
    const bitsLo = (totalLen * 8) % 4294967296;
    TAIL[tailLen - 8] = (bitsHi >>> 24) & 255; TAIL[tailLen - 7] = (bitsHi >>> 16) & 255;
    TAIL[tailLen - 6] = (bitsHi >>> 8) & 255;  TAIL[tailLen - 5] = bitsHi & 255;
    TAIL[tailLen - 4] = (bitsLo >>> 24) & 255; TAIL[tailLen - 3] = (bitsLo >>> 16) & 255;
    TAIL[tailLen - 2] = (bitsLo >>> 8) & 255;  TAIL[tailLen - 1] = bitsLo & 255;
    compress(H, TAIL, 0);
    if (tailLen === 128) compress(H, TAIL, 64);
  }

  function stateToBytes(H, out) {
    for (let i = 0; i < 8; i++) {
      const v = H[i];
      out[i * 4] = v >>> 24; out[i * 4 + 1] = (v >>> 16) & 255;
      out[i * 4 + 2] = (v >>> 8) & 255; out[i * 4 + 3] = v & 255;
    }
    return out;
  }

  /** SHA-256 of a Uint8Array (or of a string, taken as UTF-8). Returns 32 bytes. */
  function sha256(input) {
    const bytes = typeof input === 'string' ? utf8(input) : input;
    const H = new Int32Array(IV);
    finish(H, bytes, 0, bytes.length, bytes.length);
    return stateToBytes(H, new Uint8Array(32));
  }
  const sha256Hex = (input) => toHex(sha256(input));

  /* ------------------------------------------------------------------ *
   * HMAC-SHA256 (RFC 2104)
   * ------------------------------------------------------------------ */

  /**
   * Pre-hash an HMAC key. The two pad blocks are compressed once here, so every
   * later digest with the same key costs two compressions instead of four.
   */
  function hmacKey(key) {
    let k = typeof key === 'string' ? utf8(key) : key;
    if (k.length > 64) k = sha256(k);
    const pad = new Uint8Array(64);
    const inner = new Int32Array(IV);
    const outer = new Int32Array(IV);
    for (let i = 0; i < 64; i++) pad[i] = (i < k.length ? k[i] : 0) ^ 0x36;
    compress(inner, pad, 0);
    for (let i = 0; i < 64; i++) pad[i] ^= 0x36 ^ 0x5c;
    compress(outer, pad, 0);
    return { inner, outer };
  }

  const HS = new Int32Array(8);
  const INNER = new Uint8Array(32);

  /** HMAC of msg[0..len) under a key prepared by hmacKey(). Writes 32 bytes to out. */
  function hmacInto(keyState, msg, len, out) {
    HS.set(keyState.inner);
    finish(HS, msg, 0, len, 64 + len);
    stateToBytes(HS, INNER);
    HS.set(keyState.outer);
    finish(HS, INNER, 0, 32, 96);
    return stateToBytes(HS, out);
  }

  /** HMAC-SHA256(key, message). Strings are taken as UTF-8. Returns 32 bytes. */
  function hmacSha256(key, message) {
    const msg = typeof message === 'string' ? utf8(message) : message;
    return hmacInto(hmacKey(key), msg, msg.length, new Uint8Array(32));
  }

  /* ------------------------------------------------------------------ *
   * Limbo
   * ------------------------------------------------------------------ */

  const TWO32 = 4294967296;
  /** Return-to-player factor. Stake's code names this constant `houseEdge`. */
  const RTP = 0.99;
  const SCALE = 1e8;
  /** Largest result the formula can produce (float below 1e-8). */
  const MAX_MULTIPLIER = 99000000;

  /**
   * The float is `b0/256 + b1/256^2 + b2/256^3 + b3/256^4`. Every term and every
   * partial sum is exactly representable, so it equals uint32 / 2^32 exactly and
   * there are exactly 2^32 possible floats.
   */
  const floatFromUint32 = (k) => k / TWO32;

  /**
   * Multiplier for one of the 2^32 possible floats. The operation order matches
   * the client-side verification code, so the doubles are bit-identical.
   *
   * Note: Stake's public documentation prints `1e8 / (float * 1e8) * houseEdge`.
   * That differs from this formula whenever the "+ 1" moves the second decimal,
   * and badly for very small floats; the published test vectors only match the
   * version below.
   */
  function multiplierFromUint32(k) {
    const float = k / TWO32;
    const floatPoint = SCALE / (Math.floor(float * SCALE) + 1) * RTP;
    const crashPoint = Math.floor(floatPoint * 100) / 100;
    return Math.max(crashPoint, 1);
  }

  /** The formula exactly as printed in the public documentation (for comparison only). */
  function documentedMultiplierFromUint32(k) {
    const float = k / TWO32;
    const floatPoint = SCALE / (float * SCALE) * RTP;
    const crashPoint = Math.floor(floatPoint * 100) / 100;
    return Math.max(crashPoint, 1);
  }

  const limboMessage = (clientSeed, nonce) => `${clientSeed}:${nonce}:0`;

  function checkNonce(nonce) {
    if (!Number.isSafeInteger(nonce) || nonce < 0) throw new Error('nonce must be a non-negative integer');
  }

  /** Every intermediate value for a single bet, for step-by-step verification. */
  function limboResult(serverSeed, clientSeed, nonce) {
    checkNonce(nonce);
    const message = limboMessage(clientSeed, nonce);
    const digest = hmacSha256(serverSeed, message);
    const uint32 = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
    const float = uint32 / TWO32;
    const divisor = Math.floor(float * SCALE) + 1;
    const floatPoint = SCALE / divisor * RTP;
    return {
      message,
      hmacHex: toHex(digest),
      bytes: [digest[0], digest[1], digest[2], digest[3]],
      uint32,
      float,
      divisor,
      floatPoint,
      multiplier: multiplierFromUint32(uint32),
    };
  }

  const DIGITS = new Uint8Array(20);

  /**
   * The raw 32-bit draw for `count` consecutive nonces starting at startNonce.
   * This is the fast path: one prepared key, one reused message buffer.
   */
  function replayUint32(serverSeed, clientSeed, startNonce, count, out) {
    checkNonce(startNonce);
    if (!Number.isSafeInteger(count) || count < 0) throw new Error('count must be a non-negative integer');
    const keyState = hmacKey(serverSeed);
    const prefix = utf8(clientSeed + ':');
    const msg = new Uint8Array(prefix.length + 22);
    msg.set(prefix);
    const digest = new Uint8Array(32);
    const res = out || new Uint32Array(count);
    for (let i = 0; i < count; i++) {
      let n = startNonce + i;
      let nd = 0;
      do { DIGITS[nd++] = 48 + (n % 10); n = Math.floor(n / 10); } while (n > 0);
      let len = prefix.length;
      while (nd > 0) msg[len++] = DIGITS[--nd];
      msg[len++] = 58; // ':'
      msg[len++] = 48; // '0'  (cursor round 0: Limbo uses the first four bytes)
      hmacInto(keyState, msg, len, digest);
      res[i] = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
    }
    return res;
  }

  /** Multipliers for `count` consecutive nonces starting at startNonce. */
  function replay(serverSeed, clientSeed, startNonce, count) {
    const raw = replayUint32(serverSeed, clientSeed, startNonce, count);
    const out = new Float64Array(count);
    for (let i = 0; i < count; i++) out[i] = multiplierFromUint32(raw[i]);
    return out;
  }

  /* ------------------------------------------------------------------ *
   * Exact odds
   * ------------------------------------------------------------------ */

  /**
   * How many of the 2^32 equally likely draws give a result >= target.
   *
   * multiplierFromUint32 never increases as k grows (each step of the formula is
   * a monotone IEEE-754 operation), so the winning draws are exactly
   * k = 0 .. winCount-1 and a binary search finds the boundary exactly.
   * research/exact_enum.c confirms the monotonicity over all 2^32 draws.
   */
  function winCount(target) {
    if (!(target > 1)) return TWO32;
    if (multiplierFromUint32(0) < target) return 0;
    let lo = 0;
    let hi = TWO32 - 1;
    while (lo < hi) {
      const mid = lo + Math.ceil((hi - lo) / 2);
      if (multiplierFromUint32(mid) >= target) lo = mid;
      else hi = mid - 1;
    }
    return lo + 1;
  }

  /** Exact chance that a round's result is >= target. */
  const winProbability = (target) => winCount(target) / TWO32;

  /** Exact odds for a target: win chance, return to player, house edge. */
  function odds(target) {
    const count = winCount(target);
    const p = count / TWO32;
    return { target, winCount: count, p, rtp: p * target, edge: 1 - p * target };
  }

  /* ------------------------------------------------------------------ *
   * Seed commitment
   * ------------------------------------------------------------------ */

  /**
   * Does a revealed server seed match the hash that was shown while it was
   * active? The casino hashes the seed's text; the raw-bytes reading of a hex
   * seed is tried as well so a different convention is still recognised.
   */
  function checkCommitment(serverSeed, hashedServerSeed) {
    const want = String(hashedServerSeed).trim().toLowerCase();
    const seed = String(serverSeed).trim();
    const asText = sha256Hex(seed);
    if (asText === want) return { match: true, mode: 'text', computed: asText };
    if (seed.length > 0 && isHex(seed)) {
      const asBytes = sha256Hex(fromHex(seed));
      if (asBytes === want) return { match: true, mode: 'bytes', computed: asBytes };
    }
    return { match: false, mode: null, computed: asText };
  }

  return {
    // bytes
    utf8, toHex, fromHex, isHex,
    // hashing
    sha256, sha256Hex, hmacKey, hmacInto, hmacSha256,
    // limbo
    TWO32, RTP, MAX_MULTIPLIER,
    floatFromUint32, multiplierFromUint32, documentedMultiplierFromUint32,
    limboMessage, limboResult, replayUint32, replay,
    // odds
    winCount, winProbability, odds,
    // commitment
    checkCommitment,
  };
});
