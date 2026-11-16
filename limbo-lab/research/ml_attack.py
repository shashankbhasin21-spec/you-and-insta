#!/usr/bin/env python3
"""
Machine-learning attack on Limbo: can a model trained on millions of past
results predict whether the next round reaches 2.00x?

The script contains its own implementation of the algorithm (Python's hashlib,
nothing shared with the JavaScript), so its checksum line doubles as a
cross-check of src/limbo-core.js.

Two models are trained on the first 3,000,000 rounds and scored on the next
1,000,000 rounds they have never seen:
  - logistic regression on the last 32 results
  - a neural network (64 -> 128 -> 64 -> 1) on the same inputs

Each is run twice:
  - on a copy of the data with a small pattern planted in it (a win is made
    2 percentage points likelier after a loss). This proves the method can find
    a pattern of that size when there is one.
  - on the real, untouched Limbo results of two independent seed pairs.

Usage: python3 research/ml_attack.py
Needs numpy. Seeds are fixed, so the report is reproducible.
"""
import hashlib
import math
import time

import numpy as np

N = 4_000_000
TRAIN = 3_000_000
HISTORY = 32
TARGET = 2.0

def seed_pair(i: int):
    """The same fixed seed pairs as research/predictability.js."""
    return hashlib.sha256(f"limbo-lab research server seed #{i}".encode()).hexdigest(), f"limbo-lab-research-{i}"

TWO32 = 4294967296


# --------------------------------------------------------------------------
# The algorithm, independently of the JavaScript
# --------------------------------------------------------------------------

def draws(server_seed: str, client_seed: str, first_nonce: int, count: int) -> np.ndarray:
    """First four HMAC-SHA256 bytes, as a 32-bit integer, for consecutive nonces."""
    key = server_seed.encode()
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key.ljust(64, b"\0")
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key))
    outer = hashlib.sha256(bytes(b ^ 0x5C for b in key))
    prefix = client_seed.encode() + b":"
    out = np.empty(count, dtype=np.uint32)
    for i in range(count):
        h = inner.copy()
        h.update(prefix + str(first_nonce + i).encode() + b":0")
        o = outer.copy()
        o.update(h.digest())
        out[i] = int.from_bytes(o.digest()[:4], "big")
    return out


def multipliers(k: np.ndarray) -> np.ndarray:
    """The same double-precision steps as the JavaScript, applied to every draw."""
    f = k.astype(np.float64) / TWO32
    float_point = 1e8 / (np.floor(f * 1e8) + 1.0) * 0.99
    return np.maximum(np.floor(float_point * 100.0) / 100.0, 1.0)


def multiplier(k: int) -> float:
    f = k / TWO32
    float_point = 1e8 / (math.floor(f * 1e8) + 1) * 0.99
    return max(math.floor(float_point * 100) / 100, 1)


def win_count(target: float) -> int:
    """How many of the 2^32 draws give a result >= target (the result never rises as the draw grows)."""
    lo, hi = 0, TWO32 - 1
    while lo < hi:
        mid = lo + (hi - lo + 1) // 2
        if multiplier(mid) >= target:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def self_check() -> None:
    vectors = [
        ("server seed", "client seed", 1, 1.32),
        ("server seed", "client seed", 2, 1.83),
        ("server seed", "client seed", 3, 4.28),
        ("e8df2cc3b9ccb583ce5ea92336842387", "83e27f682128eb1852b048203dfd6931", 1942124, 3807692.3),
    ]
    for server, client, nonce, want in vectors:
        got = multiplier(int(draws(server, client, nonce, 1)[0]))
        assert got == want, (server, client, nonce, got, want)
    assert win_count(2.0) == 2126008812  # the count from research/exact_enum.c


# --------------------------------------------------------------------------
# Models (plain numpy)
# --------------------------------------------------------------------------

class Adam:
    def __init__(self, params, lr):
        self.params = params
        self.lr = lr
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]
        self.t = 0

    def step(self, grads):
        self.t += 1
        for p, g, m, v in zip(self.params, grads, self.m, self.v):
            m *= 0.9
            m += 0.1 * g
            v *= 0.999
            v += 0.001 * g * g
            m_hat = m / (1 - 0.9 ** self.t)
            v_hat = v / (1 - 0.999 ** self.t)
            p -= self.lr * m_hat / (np.sqrt(v_hat) + 1e-8)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


class Logistic:
    name = "logistic regression"

    def __init__(self, d, rng, base_logit):
        self.w = np.zeros((d, 1), dtype=np.float32)
        self.b = np.full(1, base_logit, dtype=np.float32)
        self.opt = Adam([self.w, self.b], 1e-3)

    def predict(self, x):
        return sigmoid(x @ self.w + self.b)[:, 0]

    def train_step(self, x, y):
        p = self.predict(x)
        g = (p - y)[:, None] / len(y)
        self.opt.step([x.T @ g, g.sum(axis=0)])


class Network:
    name = "neural network"

    def __init__(self, d, rng, base_logit):
        def layer(n_in, n_out):
            return (rng.standard_normal((n_in, n_out)) * math.sqrt(2.0 / n_in)).astype(np.float32)

        self.w1, self.b1 = layer(d, 128), np.zeros(128, dtype=np.float32)
        self.w2, self.b2 = layer(128, 64), np.zeros(64, dtype=np.float32)
        self.w3, self.b3 = layer(64, 1) * 0.1, np.full(1, base_logit, dtype=np.float32)
        self.opt = Adam([self.w1, self.b1, self.w2, self.b2, self.w3, self.b3], 1e-3)

    def forward(self, x):
        h1 = np.maximum(x @ self.w1 + self.b1, 0)
        h2 = np.maximum(h1 @ self.w2 + self.b2, 0)
        return h1, h2, sigmoid(h2 @ self.w3 + self.b3)[:, 0]

    def predict(self, x):
        return self.forward(x)[2]

    def train_step(self, x, y):
        h1, h2, p = self.forward(x)
        g3 = (p - y)[:, None] / len(y)
        g2 = (g3 @ self.w3.T) * (h2 > 0)
        g1 = (g2 @ self.w2.T) * (h1 > 0)
        self.opt.step([x.T @ g1, g1.sum(axis=0), h1.T @ g2, g2.sum(axis=0), h2.T @ g3, g3.sum(axis=0)])


# --------------------------------------------------------------------------
# Experiment
# --------------------------------------------------------------------------

def features(mult: np.ndarray):
    """For round i, the HISTORY results before it: size (log scale) and win/loss at the target."""
    size = (np.log10(np.minimum(mult, 1000.0)) / 3.0).astype(np.float32)
    won = (mult >= TARGET).astype(np.float32)
    size_w = np.lib.stride_tricks.sliding_window_view(size, HISTORY)[:-1]
    won_w = np.lib.stride_tricks.sliding_window_view(won, HISTORY)[:-1]
    label = won[HISTORY:]
    return size_w, won_w, label, float(size.mean()), float(size.std()), float(won.mean()), float(won.std())


def batch(size_w, won_w, idx, stats):
    size_mean, size_std, won_mean, won_std = stats
    return np.concatenate([(size_w[idx] - size_mean) / size_std, (won_w[idx] - won_mean) / won_std], axis=1)


def auc(score: np.ndarray, label: np.ndarray, rng) -> float:
    order = np.argsort(score + rng.uniform(-1e-9, 1e-9, len(score)), kind="stable")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1)
    pos = label > 0.5
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    return (ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def attack(mult: np.ndarray, title: str) -> None:
    size_w, won_w, label, *stats = features(mult)
    n = len(label)
    n_train = TRAIN - HISTORY
    test = np.arange(n_train, n)
    y_test = label[test]
    base_rate = float(label[:n_train].mean())
    base_logit = math.log(base_rate / (1 - base_rate))
    baseline_loss = float(-(y_test * math.log(base_rate) + (1 - y_test) * math.log(1 - base_rate)).mean())
    test_rate = float(y_test.mean())
    majority = max(test_rate, 1 - test_rate)

    print(f"\n{title}")
    print(f"   trained on rounds 1..{TRAIN:,}; scored on the next {len(test):,} unseen rounds (win rate there {test_rate * 100:.4f}%)")
    print(f"   {'model':22}{'log-loss':>10}{'no-model':>10}{'accuracy %':>12}{'no-model %':>12}{'AUC':>9}{'z':>8}{'top 10% win %':>15}{'z':>8}")
    for cls in (Logistic, Network):
        rng = np.random.default_rng(12345)
        model = cls(2 * HISTORY, rng, base_logit)
        for _epoch in range(3):
            order = rng.permutation(n_train)
            for start in range(0, n_train, 4096):
                idx = np.sort(order[start:start + 4096])
                model.train_step(batch(size_w, won_w, idx, stats), label[idx])
        prob = np.concatenate([model.predict(batch(size_w, won_w, test[s:s + 65536], stats)) for s in range(0, len(test), 65536)])
        prob = np.clip(prob.astype(np.float64), 1e-7, 1 - 1e-7)
        loss = float(-(y_test * np.log(prob) + (1 - y_test) * np.log(1 - prob)).mean())
        acc = float(((prob > 0.5) == (y_test > 0.5)).mean())
        a = auc(prob, y_test, rng)
        n_pos, n_neg = y_test.sum(), len(y_test) - y_test.sum()
        z_auc = (a - 0.5) / math.sqrt((n_pos + n_neg + 1) / (12 * n_pos * n_neg))
        # bet only the tenth of rounds the model likes best
        k = len(test) // 10
        pick = np.argsort(-prob, kind="stable")[:k]
        picked = float(y_test[pick].mean())
        rest = (y_test.sum() - y_test[pick].sum()) / (len(test) - k)
        z_pick = (picked - rest) / math.sqrt(test_rate * (1 - test_rate) * (1 / k + 1 / (len(test) - k)))
        print(f"   {model.name:22}{loss:10.6f}{baseline_loss:10.6f}{acc * 100:12.4f}{majority * 100:12.4f}{a:9.5f}{z_auc:8.2f}{picked * 100:15.4f}{z_pick:8.2f}")


def main() -> None:
    self_check()
    print("MACHINE-LEARNING ATTACK ON LIMBO")
    print("================================")
    print("columns       log-loss below 'no-model' = the model knows something (no-model: always answer the base rate)")
    print("              accuracy above 'no-model %' = better than always guessing the commoner outcome")
    print("              AUC 0.5 = coin flip; 'top 10%' = win rate of the tenth of rounds the model rated most likely to win")
    print("              |z| < 2 is luck")
    for i in (1, 2):
        server, client = seed_pair(i)
        t0 = time.time()
        k = draws(server, client, 1, N)
        mult = multipliers(k)
        print(f"\nSEED PAIR {i}")
        print(f"rounds        {N:,}  (nonces 1..{N}), generated in {time.time() - t0:.1f} s by the Python implementation")
        print(f"server seed   {server}")
        print(f"client seed   {client}")
        print(
            f"checksum      draws {hashlib.sha256(k[:1_000_000].astype('<u4').tobytes()).hexdigest()[:16]}"
            f"  results {hashlib.sha256(mult[:1_000_000].astype('<f8').tobytes()).hexdigest()[:16]}"
            + ("  (first 1,000,000 rounds; research/predictability.js prints the same)" if i == 1 else "")
        )
        if i == 1:
            # Positive control: plant a pattern. After a losing round, draws in a band just above
            # the win boundary are moved below it, which makes a win 2 percentage points likelier.
            boundary = win_count(TARGET)
            band = int(0.02 * TWO32)
            lost_before = np.concatenate([[False], mult[:-1] < TARGET])
            planted = k.astype(np.int64)
            move = lost_before & (planted >= boundary) & (planted < boundary + band)
            planted[move] -= band
            attack(multipliers(planted.astype(np.uint32)), "POSITIVE CONTROL: a pattern planted on purpose (a win is 2 points likelier after a loss)")
        attack(mult, "REAL LIMBO RESULTS")


if __name__ == "__main__":
    main()
