#!/usr/bin/env python3
# -*- coding: ascii -*-
"""iPhone Rig: a solo Bitcoin "lottery" miner for a stock iPhone.

One file, standard library only. Runs in a-Shell (free on the App Store), iSH,
Pythonista, Termux, or any desktop Python 3.8+.

    python3 miner.py              first run asks for your Bitcoin address
    python3 miner.py bc1q...      or pass it once; it is remembered
    python3 miner.py --web        also serve a dashboard on port 8080

What it does on its own: checks itself against a real Bitcoin block, picks the
fastest SHA-256 this Python has, connects to a solo pool (moving to the next
one if a pool is down), asks for a share difficulty that suits the phone,
checks that every job pays your address, reconnects after sleep or network
loss, and remembers your best share between runs.

This is a lottery ticket, not an income. It pays nothing unless it finds a
whole block, and the odds it prints are real.
"""
from __future__ import print_function

import argparse
import binascii
import collections
import hashlib
import json
import os
import random
import socket
import sys
import threading
import time

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # pragma: no cover - Python older than 3.7
    ThreadingHTTPServer = None

VERSION = "1.0.0"
AGENT = "iphone-rig/" + VERSION
DIFF1 = 0xFFFF << 208          # the target that pools call "difficulty 1"
NONCE_SPACE = 1 << 32
HERE = os.path.dirname(os.path.abspath(__file__))

# Flight sheets: a named coin + pool list, like Hive OS. The first pool that
# answers is used; the others are backups. Add your own in flightsheets.json.
SHEETS = {
    "btc-solo": {
        "coin": "BTC",
        "about": "Bitcoin solo lottery on hobby-miner pools (you keep the block)",
        "payout_check": "enforce",
        "pools": [
            "public-pool.io:3333",
            "public-pool.io:21496",
            "pool.nerdminers.org:3333",
            "pool.solomining.de:3333",
            "pool.sethforprivacy.com:3333",
        ],
    },
}

# Known answer: the header of Bitcoin block 125552 and the nonce that solved it.
KAT_HEADER = binascii.unhexlify(
    "0100000081cd02ab7e569e8bcd9317e2fe99f2de44d49ab2b8851ba4a308000000000000"
    "e320b6c2fffc8d750423db8b1eb942ae710e951ed797f7affc8892b0f1fc122b"
    "c7f5d74df2b9441a42a14695")
KAT_NONCE = 2504433986
KAT_HASH = "00000000000000001e8d6829a8a21adc5d38d0a473b144b6765798e61f98bd1d"


# ---------------------------------------------------------------- bitcoin bits

def unhex(text):
    return binascii.unhexlify(text)


def sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def swap4(data):
    """Reverse the bytes inside every 4-byte word (Stratum's prevhash order)."""
    return b"".join(data[i:i + 4][::-1] for i in range(0, len(data), 4))


def nbits_to_target(nbits):
    exponent = nbits >> 24
    mantissa = nbits & 0x007FFFFF
    if exponent <= 3:
        return mantissa >> (8 * (3 - exponent))
    return mantissa << (8 * (exponent - 3))


def varint(data, pos):
    first = data[pos]
    if first < 0xFD:
        return first, pos + 1
    size = {0xFD: 2, 0xFE: 4, 0xFF: 8}[first]
    return int.from_bytes(data[pos + 1:pos + 1 + size], "little"), pos + 1 + size


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _b32_polymod(values):
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def address_to_script(address):
    """Output script that pays a Bitcoin mainnet address.

    Raises ValueError with a plain-language reason, so a typo is caught before
    any hashing is done for an address nobody owns.
    """
    address = address.strip()
    low = address.lower()
    if low.startswith("bc1"):
        if address != low and address != address.upper():
            raise ValueError("it mixes capital and small letters")
        data = [_B32.find(c) for c in low[3:]]
        if not 11 <= len(data) <= 87 or -1 in data:
            raise ValueError("it has characters a bc1 address cannot contain")
        check = _b32_polymod([3, 3, 0, 2, 3] + data)   # [3,3,0,2,3] is "bc"
        version = data[0]
        if not ((check == 1 and version == 0) or
                (check == 0x2BC830A3 and 1 <= version <= 16)):
            raise ValueError("its checksum does not match (probably a typo)")
        acc = bits = 0
        program = bytearray()
        for value in data[1:-6]:
            acc = (acc << 5) | value
            bits += 5
            if bits >= 8:
                bits -= 8
                program.append(acc >> bits)
                acc &= (1 << bits) - 1
        if bits >= 5 or acc or not 2 <= len(program) <= 40:
            raise ValueError("it is not a valid bc1 address")
        if version == 0 and len(program) not in (20, 32):
            raise ValueError("it is not a valid bc1 address")
        return bytes([0x50 + version if version else 0, len(program)]) + bytes(program)
    if address[:1] in ("1", "3"):
        number = 0
        for char in address:
            digit = _B58.find(char)
            if digit < 0:
                raise ValueError("it has characters a Bitcoin address cannot contain")
            number = number * 58 + digit
        try:
            raw = number.to_bytes(25, "big")
        except OverflowError:
            raise ValueError("it is too long")
        if sha256d(raw[:21])[:4] != raw[21:]:
            raise ValueError("its checksum does not match (probably a typo)")
        if raw[0] == 0 and address[0] == "1":
            return b"\x76\xa9\x14" + raw[1:21] + b"\x88\xac"
        if raw[0] == 5 and address[0] == "3":
            return b"\xa9\x14" + raw[1:21] + b"\x87"
    raise ValueError("it is not a Bitcoin address (those start with bc1, 1 or 3)")


def coinbase_outputs(coinbase):
    """[(satoshis, script), ...] of a serialized coinbase transaction."""
    pos = 4
    if coinbase[pos] == 0:          # segwit marker and flag
        pos += 2
    count, pos = varint(coinbase, pos)
    for _ in range(count):
        pos += 36
        size, pos = varint(coinbase, pos)
        pos += size + 4
    count, pos = varint(coinbase, pos)
    outputs = []
    for _ in range(count):
        value = int.from_bytes(coinbase[pos:pos + 8], "little")
        size, pos = varint(coinbase, pos + 8)
        if pos + size > len(coinbase):
            raise ValueError("truncated coinbase")
        outputs.append((value, coinbase[pos:pos + size]))
        pos += size
    return outputs


def block_height(coinb1):
    """Height of the block being mined (BIP34), or None."""
    try:
        pos = 4
        if coinb1[pos] == 0:
            pos += 2
        _, pos = varint(coinb1, pos + 1 + 36)
        size = coinb1[pos]
        if 1 <= size <= 5:
            return int.from_bytes(coinb1[pos + 1:pos + 1 + size], "little")
    except (IndexError, KeyError):
        pass
    return None


# ------------------------------------------------------------------- hashing
# Both scanners return [(nonce, digest)] for every hash that ends in `suffix`
# (the digest is little-endian, so trailing zero bytes are the hash's leading
# zeros). Which one is faster depends on the Python build; tune() measures.

def scan_midstate(sha, first64, tail12, start, count, suffix):
    copy = sha(first64).copy
    hits = []
    for nonce in range(start, start + count):
        ctx = copy()
        ctx.update(tail12 + nonce.to_bytes(4, "little"))
        digest = sha(ctx.digest()).digest()
        if digest.endswith(suffix):
            hits.append((nonce, digest))
    return hits


def scan_full(sha, first64, tail12, start, count, suffix):
    head = first64 + tail12
    hits = []
    for nonce in range(start, start + count):
        digest = sha(sha(head + nonce.to_bytes(4, "little")).digest()).digest()
        if digest.endswith(suffix):
            hits.append((nonce, digest))
    return hits


def sha_candidates():
    found = [("openssl", hashlib.sha256)]
    for module in ("_sha2", "_sha256"):
        try:
            found.append(("builtin", __import__(module).sha256))
            break
        except (ImportError, AttributeError):
            pass
    return found


def tune(seconds=0.25):
    """Self-test every SHA-256 this Python offers and return the fastest.

    Returns (hashes_per_second, name, sha, scan). A hash function that does
    not reproduce block 125552 is never used.
    """
    best = None
    for sha_name, sha in sha_candidates():
        for scan_name, scan in (("midstate", scan_midstate), ("full", scan_full)):
            hits = scan(sha, KAT_HEADER[:64], KAT_HEADER[64:76],
                        KAT_NONCE - 3, 8, b"\x00\x00\x00\x00")
            if [n for n, _ in hits] != [KAT_NONCE] or hits[0][1][::-1].hex() != KAT_HASH:
                continue
            done = 0
            started = time.perf_counter()
            while True:
                scan(sha, KAT_HEADER[:64], KAT_HEADER[64:76], done, 2000,
                     b"\xff\xff\xff\xff\xff\xff")
                done += 2000
                elapsed = time.perf_counter() - started
                if elapsed >= seconds:
                    break
            rate = done / elapsed
            if best is None or rate > best[0]:
                best = (rate, sha_name + "/" + scan_name, sha, scan)
    if best is None:
        raise SystemExit("Self-test failed: this Python's SHA-256 gives wrong "
                         "answers, so mining with it would be pointless.")
    return best


# ---------------------------------------------------------------- all cores
# iOS apps cannot start extra processes, and ordinary Python threads take
# turns on one core. Python 3.13+ can run several interpreters in one
# process, each with its own lock, so one per core hashes in parallel.
# Each is fed 86-byte jobs over a pipe and answers with the hits it found.

WORKER = r'''
import os
try:
    import _sha2 as _module
except ImportError:
    import _sha256 as _module
sha = _module.sha256
def read(size):
    data = b""
    while len(data) < size:
        part = os.read(RFD, size - len(data))
        if not part:
            raise SystemExit
        data += part
    return data
while True:
    job = read(86)
    count = int.from_bytes(job[80:84], "little")
    if not count:
        break
    start = int.from_bytes(job[76:80], "little")
    suffix = b"\x00" * job[84]
    copy = sha(job[:64]).copy
    tail = job[64:76]
    found = []
    for nonce in range(start, start + count):
        ctx = copy()
        ctx.update(tail + nonce.to_bytes(4, "little"))
        digest = sha(ctx.digest()).digest()
        if digest.endswith(suffix):
            found.append(nonce.to_bytes(4, "little") + digest)
    reply = b"".join(found)
    reply = len(reply).to_bytes(4, "little") + reply
    while reply:
        reply = reply[os.write(WFD, reply):]
'''


def interpreter_api():
    """(create, run, destroy) for sub-interpreters, or None if unsupported."""
    try:
        import _interpreters                       # Python 3.13 and later
        if hasattr(_interpreters, "exec"):
            def run(interp, code):
                failure = _interpreters.exec(interp, code)
                if failure is not None:
                    raise RuntimeError(getattr(failure, "msg", None) or str(failure))
            return _interpreters.create, run, _interpreters.destroy
    except ImportError:
        pass
    try:
        from concurrent import interpreters        # the public name, 3.14+
        return (interpreters.create, lambda interp, code: interp.exec(code),
                lambda interp: interp.close())
    except ImportError:
        return None


class Cores(object):
    """Hash on several cores at once. scan() has the scanners' signature."""

    def __init__(self, count):
        api = interpreter_api()
        if api is None:
            raise RuntimeError("this Python cannot run one interpreter per core")
        self.create, self.execute, self.destroy = api
        self.workers = []
        try:
            for _ in range(count):
                self._spawn()
            for worker in self.workers:     # every core must solve block 125552
                self._ask(worker, KAT_HEADER[:76], KAT_NONCE - 3, 8, 4)
                hits = self._answer(worker, 30)
                if [n for n, _ in hits] != [KAT_NONCE] or hits[0][1][::-1].hex() != KAT_HASH:
                    raise RuntimeError("a core failed the self-test")
        except Exception:
            self.close()
            raise

    def _spawn(self):
        job_r, job_w = os.pipe()
        res_r, res_w = os.pipe()
        interp = self.create()
        code = WORKER.replace("RFD", str(job_r)).replace("WFD", str(res_w))

        def body():
            try:
                self.execute(interp, code)
            except Exception:
                pass
            finally:                        # closing these wakes a waiting scan()
                for fd in (res_w, job_r):
                    try:
                        os.close(fd)
                    except OSError:
                        pass

        thread = threading.Thread(target=body)
        thread.daemon = True
        self.workers.append({"interp": interp, "thread": thread, "job_w": job_w, "res_r": res_r})
        thread.start()

    @staticmethod
    def _ask(worker, head, start, count, zeros):
        os.write(worker["job_w"], head + start.to_bytes(4, "little") +
                 count.to_bytes(4, "little") + bytes([zeros, 0]))

    @staticmethod
    def _read(fd, size):
        data = b""
        while len(data) < size:
            part = os.read(fd, size - len(data))
            if not part:
                raise RuntimeError("a hashing core stopped")
            data += part
        return data

    def _answer(self, worker, timeout):
        import select
        if not select.select([worker["res_r"]], [], [], timeout)[0]:
            raise RuntimeError("a hashing core did not answer")
        return self._parse(worker["res_r"])

    def _parse(self, fd):
        body = self._read(fd, int.from_bytes(self._read(fd, 4), "little"))
        return [(int.from_bytes(body[i:i + 4], "little"), body[i + 4:i + 36])
                for i in range(0, len(body), 36)]

    def scan(self, sha, first64, tail12, start, count, suffix):
        """Share out [start, start+count) in small slices, so that fast cores
        take more of them than slow ones (iPhones have both kinds)."""
        import select
        head = first64 + tail12
        size = max(500, count // (6 * len(self.workers)))
        cursor, end = start, start + count
        idle = list(self.workers)
        busy = {}
        hits = []
        while cursor < end or busy:
            while idle and cursor < end:
                worker = idle.pop()
                take = min(size, end - cursor)
                self._ask(worker, head, cursor, take, len(suffix))
                busy[worker["res_r"]] = worker
                cursor += take
            ready = select.select(list(busy), [], [], 60)[0]
            if not ready:
                raise RuntimeError("the hashing cores stopped answering")
            for fd in ready:
                hits.extend(self._parse(fd))
                idle.append(busy.pop(fd))
        return hits

    def close(self):
        for worker in self.workers:
            try:
                self._ask(worker, b"\x00" * 76, 0, 0, 0)       # count 0 means quit
            except OSError:
                pass
        while self.workers:                 # safe to call again if interrupted
            worker = self.workers[-1]
            worker["thread"].join(3)
            self.workers.pop()
            for fd in (worker["job_w"], worker["res_r"]):
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                self.destroy(worker["interp"])
            except Exception:
                pass


def measure(scan, sha, seconds):
    """Hashes per second of a scanner on throwaway work."""
    done, count = 0, 4000
    started = time.perf_counter()
    while True:
        scan(sha, KAT_HEADER[:64], KAT_HEADER[64:76], done % (NONCE_SPACE - count), count,
             b"\xff\xff\xff\xff\xff\xff")
        done += count
        elapsed = time.perf_counter() - started
        if elapsed >= seconds:
            return done / elapsed
        count = int(max(4000, min(2000000, done / elapsed * 0.1)))


# ---------------------------------------------------------------- formatting

def _sig3(value):
    if value >= 100:
        return "%.0f" % value
    if value >= 10:
        return "%.1f" % value
    return "%.2f" % value


def fmt_rate(rate):
    for unit, size in (("GH/s", 1e9), ("MH/s", 1e6), ("kH/s", 1e3)):
        if rate >= size:
            return "%s %s" % (_sig3(rate / size), unit)
    return "%.0f H/s" % rate


def fmt_diff(diff):
    for suffix, size in (("E", 1e18), ("P", 1e15), ("T", 1e12), ("G", 1e9),
                         ("M", 1e6), ("K", 1e3)):
        if diff >= size:
            return _sig3(diff / size) + suffix
    return "%.4g" % diff


def fmt_count(number):
    for word, size in (("quintillion", 1e18), ("quadrillion", 1e15),
                       ("trillion", 1e12), ("billion", 1e9), ("million", 1e6),
                       ("thousand", 1e3)):
        if number >= size:
            return "%s %s" % (_sig3(number / size), word)
    return _sig3(number)


def fmt_dur(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return "%dd %02dh %02dm" % (days, hours, minutes)
    return "%d:%02d:%02d" % (hours, minutes, seconds)


def short_address(address):
    return address if len(address) <= 16 else address[:8] + "..." + address[-6:]


# ------------------------------------------------------------------- stratum

class PoolError(Exception):
    pass


class PayoutError(PoolError):
    pass


def _error_text(error):
    if isinstance(error, (list, tuple)) and len(error) > 1:
        return str(error[1])
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(error)


class Job(object):
    """One mining.notify, with the fixed header fields already in wire order."""

    __slots__ = ("id", "coinb1", "coinb2", "branches", "version_le", "prev",
                 "ntime_hex", "ntime_le", "nbits_le", "clean", "target",
                 "net_diff", "height", "diff")

    def __init__(self, params, diff):
        job_id, prevhash, coinb1, coinb2, branches, version, nbits, ntime = params[:8]
        if len(prevhash) != 64 or len(version) != 8 or len(nbits) != 8 or len(ntime) != 8:
            raise ValueError("malformed job")
        self.id = job_id
        self.coinb1 = unhex(coinb1)
        self.coinb2 = unhex(coinb2)
        self.branches = [unhex(branch) for branch in branches]
        self.version_le = unhex(version)[::-1]
        self.prev = swap4(unhex(prevhash))
        self.ntime_hex = ntime
        self.ntime_le = unhex(ntime)[::-1]
        self.nbits_le = unhex(nbits)[::-1]
        self.clean = bool(params[8]) if len(params) > 8 else True
        self.target = nbits_to_target(int(nbits, 16))
        if self.target <= 0:
            raise ValueError("malformed job")
        self.net_diff = DIFF1 / self.target
        self.height = block_height(self.coinb1)
        self.diff = diff        # share difficulty in force when the job arrived


class Pool(object):
    """One Stratum v1 connection, read by its own thread."""

    def __init__(self, host, port, user, on_share, log):
        self.host, self.port, self.user = host, port, user
        self.name = "%s:%d" % (host, port)
        self.on_share, self.log = on_share, log
        self.sock = None
        self.alive = False
        self.error = ""
        self.send_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.last_id = 0
        self.en1 = b""
        self.en2_size = 4
        self.next_diff = 1.0
        self.job = None
        self.job_seq = 0
        self.opened = self.last_rx = self.last_tx = time.time()

    def open(self, suggest):
        self.sock = socket.create_connection((self.host, self.port), timeout=15)
        self.sock.settimeout(30)
        self.alive = True
        self.opened = self.last_rx = self.last_tx = time.time()
        reader = threading.Thread(target=self._reader)
        reader.daemon = True
        reader.start()
        reply = self.call("mining.subscribe", [AGENT])
        try:
            self.en1 = unhex(reply[1])
            self.en2_size = int(reply[2])
        except (TypeError, ValueError, IndexError):
            raise PoolError("unexpected reply to subscribe")
        if not 1 <= self.en2_size <= 16:
            raise PoolError("unsupported extranonce size")
        if self.call("mining.authorize", [self.user, "x"]) is not True:
            raise PoolError("the pool refused this address")
        self.suggest(suggest)

    def close(self):
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass

    def _register(self, slot):
        with self.pending_lock:
            self.last_id += 1
            self.pending[self.last_id] = slot
            return self.last_id

    def _send(self, message):
        data = (json.dumps(message) + "\n").encode("ascii")
        try:
            with self.send_lock:
                self.sock.sendall(data)
        except (OSError, AttributeError) as exc:
            self.error = self.error or "send failed: %s" % exc
            self.alive = False
            raise PoolError(self.error)
        self.last_tx = time.time()

    def call(self, method, params, timeout=20):
        slot = {"event": threading.Event()}
        rid = self._register(slot)
        self._send({"id": rid, "method": method, "params": params})
        slot["event"].wait(timeout)
        with self.pending_lock:
            self.pending.pop(rid, None)
        if "result" not in slot:
            raise PoolError(self.error or "no reply to " + method)
        if slot.get("error"):
            raise PoolError(_error_text(slot["error"]))
        return slot["result"]

    def suggest(self, difficulty):
        """Ask for a share difficulty. Also serves as the keep-alive."""
        rid = self._register({"kind": "ignore", "sent": time.time()})
        self._send({"id": rid, "method": "mining.suggest_difficulty",
                    "params": [difficulty]})

    def submit(self, job_id, en2_hex, ntime_hex, nonce, meta):
        rid = self._register({"kind": "submit", "meta": meta, "sent": time.time()})
        self._send({"id": rid, "method": "mining.submit",
                    "params": [self.user, job_id, en2_hex, ntime_hex, "%08x" % nonce]})

    def prune(self, now):
        with self.pending_lock:
            for rid in [r for r, s in self.pending.items()
                        if now - s.get("sent", now) > 120]:
                del self.pending[rid]

    def _reader(self):
        buffered = b""
        try:
            while self.alive:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise PoolError("the pool closed the connection")
                self.last_rx = time.time()
                buffered += chunk
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    if line.strip():
                        self._handle(line)
                if len(buffered) > (1 << 20):
                    raise PoolError("the pool sent an oversized message")
        except Exception as exc:        # any failure here means: reconnect
            if self.alive:
                self.error = self.error or str(exc) or exc.__class__.__name__
        finally:
            self.alive = False
            with self.pending_lock:
                waiting = [s["event"] for s in self.pending.values() if "event" in s]
            for event in waiting:
                event.set()

    def _handle(self, line):
        try:
            message = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        method = message.get("method")
        if method is not None:
            params = message.get("params") or []
            if method == "mining.notify":
                try:
                    job = Job(params, self.next_diff)
                except (ValueError, TypeError, IndexError):
                    return
                self.job = job
                self.job_seq += 1
            elif method == "mining.set_difficulty":
                try:
                    difficulty = float(params[0])
                except (ValueError, TypeError, IndexError):
                    return
                if difficulty > 0:
                    self.next_diff = difficulty
            elif method == "mining.set_extranonce":
                try:
                    self.en1, self.en2_size = unhex(params[0]), int(params[1])
                    self.job_seq += 1
                except (ValueError, TypeError, IndexError):
                    return
            elif method == "client.show_message" and params:
                text = str(params[0])[:120].encode("ascii", "replace").decode("ascii")
                self.log("pool says: " + text)
            elif method == "client.reconnect":
                raise PoolError("the pool asked us to reconnect")
            elif method == "client.get_version":
                self._send({"id": message.get("id"), "result": AGENT, "error": None})
            elif method == "mining.ping":
                self._send({"id": message.get("id"), "result": "pong", "error": None})
            return
        with self.pending_lock:
            slot = self.pending.get(message.get("id"))
            if slot is None:
                return
            if "event" not in slot:
                del self.pending[message.get("id")]
        slot["error"] = message.get("error")
        slot["result"] = message.get("result")
        if "event" in slot:
            slot["event"].set()
        elif slot.get("kind") == "submit":
            accepted = bool(slot["result"]) and not slot["error"]
            reason = "" if accepted else _error_text(slot["error"] or "refused")
            self.on_share(accepted, reason, slot["meta"], time.time() - slot["sent"])


# ----------------------------------------------------------------------- rig

def load_json(path, default):
    try:
        with open(path) as handle:
            data = json.load(handle)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
    try:
        with open(path + ".tmp", "w") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(path + ".tmp", path)
    except OSError:
        pass                     # a read-only folder must not stop the miner


class Rig(object):
    def __init__(self, address, sheet, worker="iphone", intensity=100, interval=30,
                 folder=HERE, web=None, web_host="127.0.0.1", backoff_cap=60, out=None,
                 cores=None):
        self.address = address
        self.cores_wanted = cores       # None: as many as help; 1: one core only
        self.cores = None
        self.single = None
        self.farewell = False
        self.worker = worker
        self.user = address + "." + worker if worker else address
        self.sheet = sheet
        self.pools = []
        for entry in sheet["pools"]:
            host, _, port = str(entry).rpartition(":")
            self.pools.append((host, int(port)))
        self.payout_mode = sheet.get("payout_check", "warn")
        self.script = None
        if sheet.get("coin", "BTC") == "BTC" and self.payout_mode != "off":
            self.script = address_to_script(address)
        self.intensity = max(1, min(100, int(intensity)))
        self.interval = max(1, interval)
        self.folder = folder
        self.web, self.web_host = web, web_host
        self.backoff_cap = backoff_cap
        self.out = out or sys.stdout
        self.stop = False

        self.lock = threading.Lock()
        self.state = "starting"
        self.pool_name = ""
        self.engine = ""
        self.speed = 0.0
        self.sha = self.scan = None
        self.started = time.time()
        self.hashes = 0
        self.accepted = self.rejected = 0
        self.best = 0.0
        self.pool_diff = None
        self.net_diff = self.height = self.reward = self.payout = None
        self.en2 = random.getrandbits(16)
        self.warned = set()
        self.samples = collections.deque(maxlen=1000)     # (time, total hashes)
        self.history = collections.deque(maxlen=720)      # (time, rate) every 5 s
        self.events = collections.deque(maxlen=40)
        now = time.time()
        self.t_sample = self.t_status = self.t_save = self.t_hist = now
        self.h_hist = 0
        self.life0 = load_json(os.path.join(folder, "rig_stats.json"), {})
        self.blocks = list(self.life0.get("blocks", []))

    # ---- output

    def log(self, text):
        with self.lock:
            self.events.append((int(time.time()), text))
        try:
            print("%s  %s" % (time.strftime("%H:%M:%S"), text), file=self.out)
            self.out.flush()
        except (OSError, ValueError):
            pass

    def rate(self, window):
        now = time.time()
        with self.lock:
            for stamp, total in self.samples:
                if stamp >= now - window:
                    if now - stamp < 2:
                        return 0.0
                    return (self.hashes - total) / (now - stamp)
        return 0.0

    def odds_years(self):
        """Expected years to find a block at the current speed, or None."""
        rate = self.rate(60) or self.rate(10) or self.speed * self.intensity / 100.0
        if not rate or not self.net_diff:
            return None
        return self.net_diff * NONCE_SPACE / rate / 31557600.0

    def status(self):
        if self.state == "mining":
            self.log("%s  A%d R%d  best %s" % (fmt_rate(self.rate(60) or self.rate(10)),
                                             self.accepted, self.rejected,
                                             fmt_diff(self.best)))

    def life(self):
        """All-time totals: what was on disk plus this run."""
        old = self.life0
        return {
            "since": old.get("since", int(self.started)),
            "hashes": int(old.get("hashes", 0)) + self.hashes,
            "seconds": int(old.get("seconds", 0) + time.time() - self.started),
            "accepted": int(old.get("accepted", 0)) + self.accepted,
            "rejected": int(old.get("rejected", 0)) + self.rejected,
            "best": max(float(old.get("best", 0)), self.best),
            "blocks": self.blocks,
        }

    def save_stats(self):
        save_json(os.path.join(self.folder, "rig_stats.json"), self.life())

    def snapshot(self):
        life = self.life()
        with self.lock:
            history = [[t, round(r)] for t, r in self.history]
            events = [[t, text] for t, text in self.events]
        return {
            "version": VERSION, "state": self.state, "pool": self.pool_name,
            "coin": self.sheet.get("coin", "BTC"), "worker": self.worker,
            "address": short_address(self.address), "engine": self.engine,
            "intensity": self.intensity, "now": time.time(),
            "uptime": time.time() - self.started,
            "rate_10s": self.rate(10), "rate_60s": self.rate(60),
            "rate_run": self.hashes / max(1.0, time.time() - self.started),
            "accepted": self.accepted, "rejected": self.rejected,
            "best": self.best, "best_ever": life["best"],
            "pool_diff": self.pool_diff, "net_diff": self.net_diff,
            "height": self.height, "payout": self.payout,
            "reward_btc": self.reward / 1e8 if self.reward else None,
            "odds_years": self.odds_years(), "blocks": len(self.blocks),
            "life_hashes": life["hashes"], "life_seconds": life["seconds"],
            "history": history, "events": events,
        }

    # ---- periodic work

    def chores(self, pool, now):
        if now - self.t_sample >= 1:
            self.t_sample = now
            with self.lock:
                self.samples.append((now, self.hashes))
        if now - self.t_hist >= 5:
            rate = (self.hashes - self.h_hist) / (now - self.t_hist)
            with self.lock:
                self.history.append((int(now), rate))
            self.t_hist, self.h_hist = now, self.hashes
        if now - self.t_status >= self.interval:
            self.t_status = now
            self.status()
        if now - self.t_save >= 60:
            self.t_save = now
            self.save_stats()
        if pool is not None and pool.alive:
            if now - max(pool.last_rx, pool.last_tx) > 60:
                pool.suggest(self.suggestion())
            if now - pool.last_rx > 600:
                raise PoolError("the pool went silent")
            pool.prune(now)

    def idle(self, seconds):
        end = time.time() + seconds
        while not self.stop and time.time() < end:
            time.sleep(0.25)
            self.chores(None, time.time())

    def suggestion(self):
        """Share difficulty that gives roughly one share every 20 seconds."""
        rate = self.rate(60) or self.speed * self.intensity / 100.0
        return float("%.2g" % max(rate * 20.0 / NONCE_SPACE, 1e-6))

    # ---- work

    def check_payout(self, pool, coinbase):
        if self.script is None:
            return
        total = mine = 0
        try:
            for value, script in coinbase_outputs(coinbase):
                total += value
                if script == self.script:
                    mine += value
        except (IndexError, KeyError, ValueError):
            total = 0
        if not total:
            self.payout = self.reward = None
            if pool.name not in self.warned:
                self.warned.add(pool.name)
                self.log("could not read this pool's payout, carrying on")
            return
        self.reward, self.payout = total, float(mine) / total
        if self.payout >= 0.9:
            return
        if self.payout_mode == "enforce":
            raise PayoutError("its work pays %d%% of the reward to your address"
                              % round(self.payout * 100))
        if pool.name not in self.warned:
            self.warned.add(pool.name)
            self.log("note: this pool's blocks pay the pool, which then "
                     "credits you (normal for shared pools)")

    def make_work(self, pool, job):
        self.en2 = (self.en2 + 1) % (1 << (8 * pool.en2_size))
        en2 = self.en2.to_bytes(pool.en2_size, "big")
        coinbase = job.coinb1 + pool.en1 + en2 + job.coinb2
        self.check_payout(pool, coinbase)
        root = sha256d(coinbase)
        for branch in job.branches:
            root = sha256d(root + branch)
        head = job.version_le + job.prev + root + job.ntime_le + job.nbits_le
        return (job, en2.hex(), head[:64], head[64:])

    def announce(self, job):
        self.net_diff, self.height = job.net_diff, job.height
        self.log("block %s  net diff %s" % (job.height if job.height else "?",
                                           fmt_diff(job.net_diff)))
        if self.reward and self.payout is not None:
            self.log("prize %.3f BTC, %d%% to you" % (self.reward / 1e8,
                                                    round(self.payout * 100)))
        years = self.odds_years()
        if years and years >= 1:
            self.log("odds 1 in %s per year" % fmt_count(years))

    def on_hit(self, pool, work, seq, nonce, digest, target):
        if sha256d(work[2] + work[3] + nonce.to_bytes(4, "little")) != digest:
            return                      # never trust a hit that does not re-check
        value = int.from_bytes(digest, "little")
        diff = DIFF1 / value if value else float("inf")
        if diff > self.best:
            self.best = diff
        if value > target:
            return
        job = work[0]
        if pool.job_seq != seq and pool.job is not None and pool.job.clean:
            return                      # the pool already threw this job away
        block = value <= job.target
        pool.submit(job.id, work[1], job.ntime_hex, nonce, {"diff": diff, "block": block})
        if block:
            self.blocks.append({
                "time": int(time.time()), "height": job.height, "pool": pool.name,
                "hash": digest[::-1].hex(), "nonce": "%08x" % nonce,
                "extranonce2": work[1], "ntime": job.ntime_hex, "job": str(job.id)})
            self.save_stats()
            self.log("*** BLOCK FOUND *** height %s" % job.height)
            self.log("hash " + digest[::-1].hex())
            self.log("Sent to the pool. Details saved in rig_stats.json.")

    def on_share(self, accepted, reason, meta, latency):
        if accepted:
            self.accepted += 1
            self.log("accepted #%d  diff %s  %dms" % (self.accepted, fmt_diff(meta["diff"]),
                                                    latency * 1000))
        else:
            self.rejected += 1
            self.log("rejected: %s" % (reason[:60] or "no reason given"))

    def mine(self, pool):
        """Hash on one live connection until it drops or we are told to stop."""
        seq = -1
        work = None
        prev = None
        nonce = 0
        diff = target = suffix = None
        chunk = max(500, int(self.speed * 0.1))
        last = time.time()
        while pool.alive and not self.stop:
            now = time.time()
            if now - last > 30:         # iOS froze the app; everything is stale
                with self.lock:
                    self.samples.clear()
                self.t_hist, self.h_hist = now, self.hashes
                raise PoolError("the phone was asleep for %s" % fmt_dur(now - last))
            last = now
            if pool.job_seq != seq and pool.job is not None:
                seq = pool.job_seq
                job = pool.job
                work = self.make_work(pool, job)
                nonce = 0
                if job.prev != prev:
                    prev = job.prev
                    self.announce(job)
            if work is None:
                if now - pool.opened > 45:
                    raise PoolError("no work arrived")
                time.sleep(0.2)
                self.chores(pool, time.time())
                continue
            wanted = max(work[0].diff, pool.next_diff)
            if wanted != diff:
                diff = self.pool_diff = wanted
                target = min(int(DIFF1 / diff), (1 << 256) - 1)
                zero_bytes = 32 - (target.bit_length() + 7) // 8
                suffix = b"\x00" * min(2, zero_bytes)
            count = min(chunk, NONCE_SPACE - nonce)
            began = time.perf_counter()
            try:
                hits = self.scan(self.sha, work[2], work[3], nonce, count, suffix)
            except (RuntimeError, OSError) as exc:
                if self.cores is None:
                    raise
                self.one_core(str(exc)[:50])
                chunk = max(500, int(self.speed * 0.1))
                continue
            took = time.perf_counter() - began
            nonce += count
            self.hashes += count
            for hit_nonce, digest in hits:
                self.on_hit(pool, work, seq, hit_nonce, digest, target)
            if nonce >= NONCE_SPACE:
                work = self.make_work(pool, work[0])
                nonce = 0
            if took > 0:                # aim for chunks of about a tenth of a second
                chunk = int(max(500, min(2000000, (chunk + count * 0.1 / took) / 2)))
            if self.intensity < 100:
                time.sleep(took * (100 - self.intensity) / self.intensity)
            self.chores(pool, time.time())

    # ---- lifecycle

    def start_web(self):
        if self.web is None or ThreadingHTTPServer is None:
            return
        rig = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/stats":
                    body, kind = json.dumps(rig.snapshot()).encode("ascii"), "application/json"
                elif path in ("/", "/index.html"):
                    body, kind = DASHBOARD.encode("ascii"), "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        try:
            server = ThreadingHTTPServer((self.web_host, self.web), Handler)
        except OSError as exc:
            self.log("dashboard not started: %s" % exc)
            return
        server.daemon_threads = True
        self.server = server
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        shown = "127.0.0.1" if self.web_host in ("0.0.0.0", "::") else self.web_host
        self.log("dashboard http://%s:%d" % (shown, server.server_address[1]))

    def pick_engine(self):
        """Fastest correct way to hash here: one core, or every core."""
        self.single = tune()
        self.speed, self.engine, self.sha, self.scan = self.single
        count = self.cores_wanted or os.cpu_count() or 1
        if count < 2 or interpreter_api() is None:
            return
        # Multi-core is new ground on iOS. If starting it ever kills the app,
        # this note is still on disk next time and the rig stays on one core.
        note_path = os.path.join(self.folder, "rig_engine.json")
        note = load_json(note_path, {}).get("multicore")
        if note == "trying":
            save_json(note_path, {"multicore": "off"})
            self.log("all-core mode did not start last time, using one core")
            self.log("(python3 miner.py --cores %d tries it again)" % count)
            return
        if note == "off":
            return
        save_json(note_path, {"multicore": "trying"})
        try:
            cores = Cores(min(count, 16))
            rate = measure(cores.scan, None, 1.0)
        except Exception as exc:
            save_json(note_path, {"multicore": "off"})
            self.log("all-core mode is not available here (%s)" % str(exc)[:60])
            return
        save_json(note_path, {"multicore": "ok"})
        if self.cores_wanted or rate > self.speed * 1.1:    # asked for, or simply faster
            self.cores = cores
            self.speed, self.scan = rate, cores.scan
            self.engine = "%d cores" % len(cores.workers)
        else:
            cores.close()

    def one_core(self, reason):
        """Drop back to single-core hashing after an all-core failure."""
        self.log("all-core mode stopped (%s), continuing on one core" % reason)
        if self.cores is not None:
            self.cores.close()
            self.cores = None
        self.speed, self.engine, self.sha, self.scan = self.single

    def run(self):
        self.pick_engine()
        print("iPhone Rig %s - solo %s lottery" % (VERSION, self.sheet.get("coin", "BTC")),
              file=self.out)
        print("address  %s" % short_address(self.address), file=self.out)
        print("worker   %s" % (self.worker or "-"), file=self.out)
        print("engine   %s, %s" % (self.engine, fmt_rate(self.speed)), file=self.out)
        if self.intensity < 100:
            print("effort   %d%%" % self.intensity, file=self.out)
        print("Ctrl-C stops. Stats are kept between runs.", file=self.out)
        self.start_web()
        refused = set()
        index = failures = 0
        try:
            while not self.stop:
                live = [p for p in self.pools if p not in refused]
                if not live:
                    self.log("No pool on this sheet pays your address. Stopping.")
                    break
                host, port = live[index % len(live)]
                pool = Pool(host, port, self.user, self.on_share, self.log)
                self.state, self.pool_name = "connecting", pool.name
                self.log("connecting to %s" % pool.name)
                try:
                    pool.open(self.suggestion())
                    self.state = "mining"
                    self.log("connected")
                    self.mine(pool)
                    if not self.stop:
                        self.log("%s: %s" % (pool.name, pool.error or "connection lost"))
                except PayoutError as exc:
                    refused.add((host, port))
                    self.log("%s refused: %s" % (pool.name, exc))
                except (PoolError, OSError, ValueError) as exc:
                    self.log("%s: %s" % (pool.name, exc))
                finally:
                    pool.close()
                if self.stop:
                    break
                self.state = "waiting"
                if pool.job is not None and time.time() - pool.opened > 60:
                    failures = 0        # it worked for a while: try it again first
                    self.idle(1)
                else:
                    failures += 1
                    index += 1
                    self.idle(min(self.backoff_cap, 2 ** min(failures, 6)))
        except KeyboardInterrupt:
            pass
        finally:
            while True:                 # a second Ctrl-C must not skip the clean-up
                try:
                    self.finish()
                    break
                except KeyboardInterrupt:
                    pass

    def finish(self):
        self.state = "stopped"
        if self.cores is not None:
            self.cores.close()
            self.cores = None
        self.save_stats()
        if self.farewell:
            return
        self.farewell = True
        life = self.life()
        print("\nstopped after %s: %s hashes, %d shares, best %s" % (
            fmt_dur(time.time() - self.started), fmt_count(self.hashes),
            self.accepted, fmt_diff(self.best)), file=self.out)
        print("all time: %s hashes, best share %s, blocks found %d" % (
            fmt_count(life["hashes"]), fmt_diff(life["best"]), len(self.blocks)),
            file=self.out)


# ----------------------------------------------------------------------- cli

def all_sheets(folder):
    sheets = dict(SHEETS)
    extra = load_json(os.path.join(folder, "flightsheets.json"), {})
    for name, sheet in extra.items():
        if isinstance(sheet, dict) and sheet.get("pools"):
            sheets[name] = sheet
    return sheets


def ask_address():
    print("No Bitcoin address saved yet.")
    print("Paste the address that should get the reward if you ever find a block.")
    print("Use a wallet you control. Never type a seed phrase or private key here.")
    while True:
        try:
            answer = input("BTC address: ").strip()
        except EOFError:
            raise SystemExit("\nNo address given. Run: python3 miner.py YOUR_ADDRESS")
        if not answer:
            continue
        try:
            address_to_script(answer)
            return answer
        except ValueError as exc:
            print("That address was not accepted: %s. Try again." % exc)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="miner.py", description="Solo Bitcoin lottery miner for a stock iPhone.")
    parser.add_argument("address", nargs="?",
                        help="your Bitcoin address (asked once, then remembered)")
    parser.add_argument("--worker", help="name shown on the pool's page (default iphone)")
    parser.add_argument("--sheet", help="flight sheet to use (default btc-solo)")
    parser.add_argument("--pool", action="append", metavar="HOST:PORT",
                        help="use this pool instead of the sheet's; repeat for backups")
    parser.add_argument("--intensity", type=int, metavar="1-100",
                        help="percent of the time spent hashing (default 100)")
    parser.add_argument("--cores", type=int, metavar="N",
                        help="cores to hash on (default: all that help; 1 runs coolest)")
    parser.add_argument("--web", nargs="?", const=8080, type=int, metavar="PORT",
                        help="serve the dashboard (port 8080 unless given)")
    parser.add_argument("--web-host", metavar="IP",
                        help="dashboard address; 0.0.0.0 shares it on your Wi-Fi")
    parser.add_argument("--interval", type=int, default=30,
                        help="seconds between status lines (default 30)")
    parser.add_argument("--benchmark", action="store_true",
                        help="measure hashing speed and exit")
    parser.add_argument("--sheets", action="store_true", help="list flight sheets and exit")
    parser.add_argument("--forget", action="store_true",
                        help="forget the saved address and settings, then exit")
    args = parser.parse_args(argv)

    folder = os.environ.get("IPHONE_RIG_HOME") or (
        HERE if os.access(HERE, os.W_OK) else os.getcwd())
    config_path = os.path.join(folder, "rig.json")
    sheets = all_sheets(folder)

    if args.forget or args.cores is not None:      # asking for cores retries all-core mode
        for name in ("rig_engine.json",) + (("rig.json",) if args.forget else ()):
            if os.path.exists(os.path.join(folder, name)):
                os.remove(os.path.join(folder, name))
    if args.forget:
        print("Saved settings forgotten.")
        return 0
    if args.sheets:
        for name in sorted(sheets):
            print("%s  (%s)" % (name, sheets[name].get("about", sheets[name].get("coin", "?"))))
            for entry in sheets[name]["pools"]:
                print("    " + str(entry))
        return 0
    if args.benchmark:
        rate, engine, _, _ = tune(1.0)
        print("one core: %s with %s (self-test passed)" % (fmt_rate(rate), engine))
        count = args.cores or os.cpu_count() or 1
        if count > 1:
            try:
                cores = Cores(min(count, 16))
                try:
                    print("%d cores: %s (self-test passed)" % (count, fmt_rate(
                        measure(cores.scan, None, 3.0))))
                finally:
                    cores.close()
            except Exception as exc:
                print("all-core mode is not available here (%s)" % exc)
        return 0

    config = load_json(config_path, {})
    for key in ("address", "worker", "sheet", "intensity", "cores", "web", "web_host"):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    name = config.get("sheet", "btc-solo")
    if name not in sheets:
        raise SystemExit("No flight sheet called %r. See: python3 miner.py --sheets" % name)
    sheet = dict(sheets[name])
    if args.pool:
        sheet["pools"] = args.pool
        sheet["payout_check"] = "warn"

    if sheet.get("coin", "BTC") == "BTC":
        if config.get("address"):
            try:
                address_to_script(config["address"])
            except ValueError as exc:
                raise SystemExit("The address %s was not accepted: %s."
                                 % (config["address"], exc))
        else:
            config["address"] = ask_address()
    elif not config.get("address"):
        raise SystemExit("Give the payout address: python3 miner.py YOUR_ADDRESS")
    save_json(config_path, config)

    try:
        rig = Rig(config["address"], sheet, worker=config.get("worker", "iphone"),
                  intensity=config.get("intensity", 100), interval=args.interval,
                  folder=folder, web=config.get("web") or None,
                  web_host=config.get("web_host", "127.0.0.1"),
                  cores=config.get("cores") or None)
    except ValueError as exc:
        raise SystemExit("This flight sheet has a bad pool entry: %s" % exc)
    rig.run()
    return 0


DASHBOARD = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>iPhone Rig</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --series: #2a78d6;
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --series: #3987e5;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-text-size-adjust: 100%; }
main { max-width: 760px; margin: 0 auto; padding: 16px 16px calc(24px + env(safe-area-inset-bottom)); }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; margin-bottom: 12px; }
h1 { font-size: 17px; font-weight: 600; margin: 0; }
h2 { font-size: 15px; font-weight: 600; margin: 0; }
.pill { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px;
  border: 1px solid var(--border); border-radius: 999px; background: var(--surface); font-size: 13px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: none; }
.card, .tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.card { padding: 16px; margin-bottom: 12px; }
.label { color: var(--ink2); font-size: 13px; }
.sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
.hero .value { font-size: 52px; font-weight: 600; line-height: 1.1; letter-spacing: -0.02em; }
.hero .unit { font-size: 20px; font-weight: 500; color: var(--ink2); margin-left: 8px; }
.tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px; }
@media (min-width: 620px) { .tiles { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.tile { padding: 12px 14px; min-width: 0; }
.tile .value { font-size: 22px; font-weight: 600; margin-top: 2px; overflow-wrap: anywhere; }
.chart { position: relative; margin-top: 10px; }
.chart svg { display: block; width: 100%; height: 210px; touch-action: pan-y; outline: none; }
.chart svg:focus-visible { outline: 2px solid var(--series); outline-offset: 2px; border-radius: 6px; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.tip { position: absolute; top: 0; left: 0; pointer-events: none; display: none; white-space: nowrap;
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 6px 9px;
  font-size: 12px; color: var(--ink2); box-shadow: 0 2px 10px rgba(0,0,0,0.18); }
.tip b { display: block; font-size: 14px; font-weight: 600; color: var(--ink); }
.key { display: inline-block; width: 12px; height: 2px; border-radius: 1px; background: var(--series);
  vertical-align: middle; margin-right: 6px; }
.empty { color: var(--muted); font-size: 13px; padding: 28px 0; text-align: center; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 10px 0 0; font-size: 14px; }
dt { color: var(--ink2); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
details { margin-top: 10px; font-size: 13px; }
summary { color: var(--ink2); cursor: pointer; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 4px 0; border-bottom: 1px solid var(--grid); font-weight: 400; }
th { color: var(--ink2); }
th:last-child, td:last-child { text-align: right; }
.log { margin: 10px 0 0; padding: 0; list-style: none; max-height: 230px; overflow-y: auto;
  font: 12px/1.55 ui-monospace, Menlo, Consolas, monospace; color: var(--ink2); overflow-wrap: anywhere; }
.log time { color: var(--muted); margin-right: 8px; }
button { font: inherit; font-size: 13px; color: var(--ink); background: var(--surface);
  border: 1px solid var(--border); border-radius: 999px; padding: 6px 12px; }
footer { display: flex; justify-content: space-between; align-items: center; gap: 12px;
  flex-wrap: wrap; color: var(--muted); font-size: 12px; }
.stale .card, .stale .tile { opacity: 0.55; }
</style>
</head>
<body>
<main id="app">
<header>
  <h1>iPhone Rig</h1>
  <span class="pill" role="status"><span class="dot" id="dot"></span><span id="state">Loading</span></span>
</header>

<section class="card hero">
  <div class="label">Hashrate now</div>
  <div><span class="value" id="rate">-</span><span class="unit" id="rateUnit"></span></div>
  <div class="sub" id="rateSub"></div>
</section>

<section class="card">
  <h2>Hashrate over time</h2>
  <div class="sub" id="chartSub"></div>
  <div class="chart">
    <svg id="svg" tabindex="0" role="img" aria-label="Line chart of hashrate over time. The table view below lists the same values."></svg>
    <div class="tip" id="tip"><b id="tipValue"></b><span class="key"></span><span id="tipTime"></span></div>
    <div class="empty" id="empty">Waiting for the first samples</div>
  </div>
  <details>
    <summary>Table view</summary>
    <table><thead><tr><th>Minute</th><th id="thRate">Hashrate</th></tr></thead><tbody id="tbody"></tbody></table>
  </details>
</section>

<section class="tiles">
  <div class="tile"><div class="label">Shares accepted</div><div class="value" id="accepted">-</div></div>
  <div class="tile"><div class="label">Shares rejected</div><div class="value" id="rejected">-</div></div>
  <div class="tile"><div class="label">Best share this run</div><div class="value" id="best">-</div></div>
  <div class="tile"><div class="label">Best share ever</div><div class="value" id="bestEver">-</div></div>
  <div class="tile"><div class="label">Share difficulty</div><div class="value" id="poolDiff">-</div></div>
  <div class="tile"><div class="label">Running for</div><div class="value" id="uptime">-</div></div>
</section>

<section class="card">
  <h2>The lottery</h2>
  <dl>
    <dt>Block being mined</dt><dd id="height">-</dd>
    <dt>Network difficulty</dt><dd id="netDiff">-</dd>
    <dt>Prize for a block</dt><dd id="reward">-</dd>
    <dt>Paid to your address</dt><dd id="payout">-</dd>
    <dt>Chance of a block</dt><dd id="odds">-</dd>
    <dt>Blocks found</dt><dd id="blocks">-</dd>
    <dt>Pool</dt><dd id="pool">-</dd>
  </dl>
</section>

<section class="card">
  <h2>Recent events</h2>
  <ul class="log" id="log"></ul>
</section>

<footer><span id="foot"></span><button id="wake" hidden>Keep screen on</button></footer>
</main>
<script>
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var points = [], geom = null, hoverTime = null;
  var STATES = {
    mining: ['\u2713 Mining', '--good'], connecting: ['\u2026 Connecting', '--warning'],
    waiting: ['\u2026 Waiting to retry', '--warning'], starting: ['\u2026 Starting', '--warning'],
    stopped: ['! Stopped', '--critical'], offline: ['! Rig not reachable', '--critical']
  };

  function sig(v) { return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2); }
  function unit(v) {
    if (v >= 1e9) return ['GH/s', 1e9];
    if (v >= 1e6) return ['MH/s', 1e6];
    if (v >= 1e3) return ['kH/s', 1e3];
    return ['H/s', 1];
  }
  function rate(v) { var u = unit(v); return [u[1] === 1 ? v.toFixed(0) : sig(v / u[1]), u[0]]; }
  function diff(d) {
    if (d === null || d === undefined) return '-';
    var steps = [['E', 1e18], ['P', 1e15], ['T', 1e12], ['G', 1e9], ['M', 1e6], ['K', 1e3]];
    for (var i = 0; i < steps.length; i++) if (d >= steps[i][1]) return sig(d / steps[i][1]) + steps[i][0];
    return String(Number(d.toPrecision(4)));
  }
  function words(n) {
    var steps = [['quintillion', 1e18], ['quadrillion', 1e15], ['trillion', 1e12], ['billion', 1e9],
                 ['million', 1e6], ['thousand', 1e3]];
    for (var i = 0; i < steps.length; i++) if (n >= steps[i][1]) return sig(n / steps[i][1]) + ' ' + steps[i][0];
    return sig(n);
  }
  function two(n) { return (n < 10 ? '0' : '') + n; }
  function clock(t, seconds) {
    var d = new Date(t * 1000);
    return two(d.getHours()) + ':' + two(d.getMinutes()) + (seconds ? ':' + two(d.getSeconds()) : '');
  }
  function duration(s) {
    s = Math.floor(s);
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return d ? d + 'd ' + two(h) + 'h ' + two(m) + 'm' : h + ':' + two(m) + ':' + two(s % 60);
  }
  function text(id, value) { $(id).textContent = value; }
  function node(name, attrs) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function draw() {
    var svg = $('svg');
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    geom = null;
    var has = points.length >= 2;
    $('empty').style.display = has ? 'none' : 'block';
    svg.style.display = has ? 'block' : 'none';
    if (!has) { $('tip').style.display = 'none'; return; }

    var W = svg.clientWidth || 320, H = 210, m = { l: 44, r: 14, t: 10, b: 24 };
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    var t1 = points[points.length - 1][0], t0 = Math.min(points[0][0], t1 - 60);
    var peak = 0;
    points.forEach(function (p) { if (p[1] > peak) peak = p[1]; });
    var u = unit(peak), step = 1, top = 1;
    if (peak > 0) {
      var rough = peak / u[1] / 4, pow = Math.pow(10, Math.floor(Math.log10(rough)));
      step = 10 * pow;
      [1, 2, 2.5, 5].some(function (c) { if (c * pow >= rough) { step = c * pow; return true; } return false; });
      top = Math.max(step, Math.ceil(peak / u[1] / step - 1e-9) * step);
    }
    var x = function (t) { return m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r); };
    var y = function (v) { return H - m.b - v / (top * u[1]) * (H - m.t - m.b); };

    for (var v = 0; v <= top + step / 2; v += step) {
      var gy = Math.round(y(v * u[1])) + 0.5;
      svg.appendChild(node('line', { x1: m.l, x2: W - m.r, y1: gy, y2: gy,
        stroke: v === 0 ? 'var(--axis)' : 'var(--grid)', 'stroke-width': 1 }));
      var label = node('text', { x: m.l - 8, y: gy + 4, 'text-anchor': 'end', 'class': 'tick' });
      label.textContent = String(Number(v.toFixed(3)));
      svg.appendChild(label);
    }
    [[t0, 'start'], [(t0 + t1) / 2, 'middle'], [t1, 'end']].forEach(function (tick) {
      var label = node('text', { x: x(tick[0]), y: H - 6, 'text-anchor': tick[1], 'class': 'tick' });
      label.textContent = clock(tick[0]);
      svg.appendChild(label);
    });

    var runs = [], run = [];
    points.forEach(function (p, i) {
      if (i && p[0] - points[i - 1][0] > 20) { runs.push(run); run = []; }
      run.push(p);
    });
    runs.push(run);
    runs.forEach(function (r) {
      if (r.length < 2) return;
      var line = r.map(function (p, i) { return (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); }).join('');
      svg.appendChild(node('path', { d: line + 'L' + x(r[r.length - 1][0]).toFixed(1) + ' ' + y(0) +
        'L' + x(r[0][0]).toFixed(1) + ' ' + y(0) + 'Z', fill: 'var(--series)', 'fill-opacity': 0.1 }));
      svg.appendChild(node('path', { d: line, fill: 'none', stroke: 'var(--series)', 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    });
    var last = points[points.length - 1];
    svg.appendChild(node('circle', { cx: x(last[0]), cy: y(last[1]), r: 4, fill: 'var(--series)',
      stroke: 'var(--surface)', 'stroke-width': 2 }));

    var cross = node('line', { y1: m.t, y2: H - m.b, stroke: 'var(--axis)', 'stroke-width': 1, visibility: 'hidden' });
    var mark = node('circle', { r: 4, fill: 'var(--series)', stroke: 'var(--surface)', 'stroke-width': 2, visibility: 'hidden' });
    svg.appendChild(cross);
    svg.appendChild(mark);
    geom = { x: x, y: y, W: W, cross: cross, mark: mark, unit: u };
    if (hoverTime !== null) show(nearest(hoverTime));
  }

  function nearest(t) {
    var best = 0, gap = Infinity;
    for (var i = 0; i < points.length; i++) {
      var g = Math.abs(points[i][0] - t);
      if (g < gap) { gap = g; best = i; }
    }
    return best;
  }
  function show(i) {
    if (!geom || !points[i]) return;
    var p = points[i], px = geom.x(p[0]), py = geom.y(p[1]), tip = $('tip'), r = rate(p[1]);
    hoverTime = p[0];
    geom.cross.setAttribute('x1', px); geom.cross.setAttribute('x2', px);
    geom.cross.setAttribute('visibility', 'visible');
    geom.mark.setAttribute('cx', px); geom.mark.setAttribute('cy', py);
    geom.mark.setAttribute('visibility', 'visible');
    text('tipValue', r[0] + ' ' + r[1]);
    text('tipTime', clock(p[0], true));
    tip.style.display = 'block';
    var left = Math.max(0, Math.min(geom.W - tip.offsetWidth, px - tip.offsetWidth / 2));
    tip.style.transform = 'translate(' + left + 'px,' + Math.max(0, py - tip.offsetHeight - 12) + 'px)';
  }
  function hide() {
    hoverTime = null;
    $('tip').style.display = 'none';
    if (geom) { geom.cross.setAttribute('visibility', 'hidden'); geom.mark.setAttribute('visibility', 'hidden'); }
  }
  function fromPointer(e) {
    if (!geom) return;
    var box = $('svg').getBoundingClientRect(), best = 0, gap = Infinity;
    for (var i = 0; i < points.length; i++) {
      var g = Math.abs(geom.x(points[i][0]) - (e.clientX - box.left));
      if (g < gap) { gap = g; best = i; }
    }
    show(best);
  }
  var svgEl = $('svg');
  svgEl.addEventListener('pointermove', fromPointer);
  svgEl.addEventListener('pointerdown', fromPointer);
  svgEl.addEventListener('pointerleave', function () { if (document.activeElement !== svgEl) hide(); });
  svgEl.addEventListener('focus', function () { if (hoverTime === null) show(points.length - 1); });
  svgEl.addEventListener('blur', hide);
  svgEl.addEventListener('keydown', function (e) {
    if (!points.length) return;
    var i = hoverTime === null ? points.length - 1 : nearest(hoverTime);
    if (e.key === 'ArrowLeft') i = Math.max(0, i - 1);
    else if (e.key === 'ArrowRight') i = Math.min(points.length - 1, i + 1);
    else if (e.key === 'Home') i = 0;
    else if (e.key === 'End') i = points.length - 1;
    else return;
    e.preventDefault();
    show(i);
  });
  window.addEventListener('resize', draw);

  function table() {
    var minutes = {}, order = [];
    points.forEach(function (p) {
      var k = Math.floor(p[0] / 60);
      if (!minutes[k]) { minutes[k] = [0, 0]; order.push(k); }
      minutes[k][0] += p[1]; minutes[k][1] += 1;
    });
    var body = $('tbody'), u = geom ? geom.unit : ['H/s', 1];
    while (body.firstChild) body.removeChild(body.firstChild);
    text('thRate', 'Hashrate (' + u[0] + ')');
    order.reverse().forEach(function (k) {
      var row = document.createElement('tr'), a = document.createElement('td'), b = document.createElement('td');
      a.textContent = clock(k * 60);
      b.textContent = u[1] === 1 ? (minutes[k][0] / minutes[k][1]).toFixed(0) : sig(minutes[k][0] / minutes[k][1] / u[1]);
      row.appendChild(a); row.appendChild(b); body.appendChild(row);
    });
  }

  function state(name) {
    var s = STATES[name] || [name, '--warning'];
    text('state', s[0]);
    $('dot').style.background = 'var(' + s[1] + ')';
  }

  function render(d) {
    state(d.state);
    var now = rate(d.rate_10s || d.rate_60s || 0), minute = rate(d.rate_60s || 0), run = rate(d.rate_run || 0);
    text('rate', now[0]); text('rateUnit', now[1]);
    text('rateSub', 'Last minute ' + minute[0] + ' ' + minute[1] + ' \u00b7 this run ' + run[0] + ' ' + run[1]);
    points = d.history || [];
    draw();
    table();
    var span = points.length >= 2 ? Math.max(60, points[points.length - 1][0] - points[0][0]) : 0;
    text('chartSub', geom ? geom.unit[0] + ', last ' + Math.round(span / 60) + (span < 90 ? ' minute' : ' minutes') : '');
    text('accepted', d.accepted.toLocaleString());
    text('rejected', d.rejected.toLocaleString());
    text('best', d.best ? diff(d.best) : '-');
    text('bestEver', d.best_ever ? diff(d.best_ever) : '-');
    text('poolDiff', diff(d.pool_diff));
    text('uptime', duration(d.uptime));
    text('height', d.height ? d.height.toLocaleString() : '-');
    text('netDiff', diff(d.net_diff));
    text('reward', d.reward_btc ? d.reward_btc.toFixed(4) + ' ' + d.coin : '-');
    text('payout', d.payout === null ? 'Not checked' : Math.round(d.payout * 100) + '%');
    text('odds', d.odds_years ? (d.odds_years >= 1 ? '1 in ' + words(d.odds_years) + ' per year' : 'Likely within a year') : '-');
    text('blocks', String(d.blocks));
    text('pool', d.pool || '-');
    var log = $('log');
    while (log.firstChild) log.removeChild(log.firstChild);
    (d.events || []).slice().reverse().forEach(function (e) {
      var li = document.createElement('li'), t = document.createElement('time');
      t.textContent = clock(e[0], true);
      li.appendChild(t);
      li.appendChild(document.createTextNode(e[1]));
      log.appendChild(li);
    });
    text('foot', 'v' + d.version + ' \u00b7 ' + d.engine + ' \u00b7 effort ' + d.intensity + '% \u00b7 ' +
      d.worker + ' \u00b7 ' + d.address);
  }

  function poll() {
    fetch('/api/stats', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      $('app').classList.remove('stale');
      render(d);
    }).catch(function () {
      $('app').classList.add('stale');
      state('offline');
    }).then(function () { setTimeout(poll, 3000); });
  }
  poll();

  var wake = $('wake'), lock = null, wanted = false;
  function acquire() {
    navigator.wakeLock.request('screen').then(function (l) {
      lock = l; wake.textContent = 'Screen stays on \u2713';
    }).catch(function () { wanted = false; wake.textContent = 'Keep screen on'; });
  }
  if ('wakeLock' in navigator) {
    wake.hidden = false;
    wake.addEventListener('click', function () {
      wanted = !wanted;
      if (wanted) acquire();
      else { if (lock) lock.release(); lock = null; wake.textContent = 'Keep screen on'; }
    });
    document.addEventListener('visibilitychange', function () {
      if (wanted && document.visibilityState === 'visible') acquire();
    });
  }
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
