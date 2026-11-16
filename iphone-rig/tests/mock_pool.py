"""A small Stratum v1 pool that runs on 127.0.0.1, for testing miner.py.

It checks every submitted share on its own, without importing the miner. The
header is put together the way NerdMiner_v2 does it (src/utils.cpp): join the
hex fields as they arrive, then reverse the version, every 4-byte word of the
previous hash, the time and the bits. So a share only passes here if the
miner built the same bytes a real, widely used miner would.
"""
import binascii
import hashlib
import json
import os
import socket
import struct
import threading
import time

DIFF1 = 0xFFFF * 2 ** 208


def dsha(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def header_like_nerdminer(version, prevhash, merkle_root, ntime, nbits, nonce):
    raw = bytearray(binascii.unhexlify(
        version + prevhash + binascii.hexlify(merkle_root).decode() + ntime + nbits + "00000000"))
    raw[0:4] = raw[0:4][::-1]
    for offset in range(4, 36, 4):
        raw[offset:offset + 4] = raw[offset:offset + 4][::-1]
    raw[68:72] = raw[68:72][::-1]
    raw[72:76] = raw[72:76][::-1]
    raw[76:80] = struct.pack("<I", nonce)
    return bytes(raw)


def target_from_bits(nbits_hex):
    bits = int(nbits_hex, 16)
    return (bits & 0x7FFFFF) << (8 * ((bits >> 24) - 3))


class MockPool(object):
    def __init__(self, pay_script, other_script=b"\x51", pay_fraction=1.0,
                 nbits="1702c4e4", reward=314159265, diff=0.0002, honor_suggest=False,
                 job_every=1.5, drop_after=None, refuse_login=False, height=921000,
                 suggest_sends_job=True, judge_by_session=False):
        self.pay_script, self.other_script = pay_script, other_script
        self.pay_fraction = pay_fraction
        self.nbits, self.reward = nbits, reward
        self.diff, self.honor_suggest = diff, honor_suggest
        self.job_every, self.drop_after = job_every, drop_after
        self.refuse_login = refuse_login
        # public-pool answers a suggestion with set_difficulty only (no new
        # job) and judges every share by the difficulty in force right now.
        self.suggest_sends_job, self.judge_by_session = suggest_sends_job, judge_by_session
        self.height = height
        self.lock = threading.Lock()
        self.accepted = self.rejected = self.blocks = self.connections = self.submits = 0
        self.reasons, self.users, self.suggested, self.agents = [], [], [], []
        self.running = True
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.address = "127.0.0.1:%d" % self.port
        thread = threading.Thread(target=self._accept)
        thread.daemon = True
        thread.start()

    def stop(self):
        self.running = False
        try:
            self.listener.close()
        except OSError:
            pass

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            with self.lock:
                self.connections += 1
            thread = threading.Thread(target=self._serve, args=(conn,))
            thread.daemon = True
            thread.start()

    # ---- one job

    def _job(self, session, new_block):
        if new_block or session["prev"] is None:
            with self.lock:
                self.height += 1
                session["height"] = self.height
            shown = b"\x00" * 8 + os.urandom(24)              # hash as explorers show it
            wire = shown[::-1]                                  # header byte order
            session["prev"] = binascii.hexlify(
                b"".join(wire[i:i + 4][::-1] for i in range(0, 32, 4))).decode()
        session["count"] += 1
        job_id = "%x" % session["count"]
        en2_size = session["en2_size"]
        script_head = b"\x03" + struct.pack("<I", session["height"])[:3] + b"\x08mockpool"
        script_tail = b"\x06/test/"
        script_len = len(script_head) + 4 + en2_size + len(script_tail)
        coinb1 = (b"\x01\x00\x00\x00" + b"\x01" + b"\x00" * 32 + b"\xff\xff\xff\xff" +
                  bytes([script_len]) + script_head)
        mine = int(self.reward * self.pay_fraction)
        outputs = []
        if mine:
            outputs.append((mine, self.pay_script))
        if self.reward - mine:
            outputs.append((self.reward - mine, self.other_script))
        outputs.append((0, b"\x6a\x24\xaa\x21\xa9\xed" + os.urandom(32)))
        coinb2 = script_tail + b"\xff\xff\xff\xff" + bytes([len(outputs)])
        for value, script in outputs:
            coinb2 += struct.pack("<Q", value) + bytes([len(script)]) + script
        coinb2 += b"\x00\x00\x00\x00"
        job = {
            "id": job_id, "prev": session["prev"],
            "coinb1": binascii.hexlify(coinb1).decode(), "coinb2": binascii.hexlify(coinb2).decode(),
            "branches": [binascii.hexlify(os.urandom(32)).decode() for _ in range(3)],
            "version": "20000000", "nbits": self.nbits, "ntime": "%08x" % int(time.time()),
            "diff": session["diff"], "seen": set(),
        }
        if new_block:
            session["jobs"].clear()
        session["jobs"][job_id] = job
        return {"id": None, "method": "mining.notify", "params": [
            job_id, job["prev"], job["coinb1"], job["coinb2"], job["branches"],
            job["version"], job["nbits"], job["ntime"], bool(new_block)]}

    def _check(self, session, params):
        _, job_id, en2, ntime, nonce = params
        job = session["jobs"].get(job_id)
        if job is None:
            return [21, "Job not found", None]
        if (en2, ntime, nonce) in job["seen"]:
            return [22, "Duplicate share", None]
        job["seen"].add((en2, ntime, nonce))
        if len(en2) != 2 * session["en2_size"] or ntime != job["ntime"]:
            return [20, "Bad share fields", None]
        root = dsha(binascii.unhexlify(job["coinb1"] + session["en1"] + en2 + job["coinb2"]))
        for branch in job["branches"]:
            root = dsha(root + binascii.unhexlify(branch))
        header = header_like_nerdminer(job["version"], job["prev"], root, ntime,
                                       job["nbits"], int(nonce, 16))
        value = int.from_bytes(dsha(header), "little")
        needed = session["diff"] if self.judge_by_session else job["diff"]
        if value > int(DIFF1 / needed):
            return [23, "Low difficulty share", None]
        if value <= target_from_bits(job["nbits"]):
            with self.lock:
                self.blocks += 1
        return None

    # ---- one connection

    def _serve(self, conn):
        session = {"en1": binascii.hexlify(os.urandom(4)).decode(), "en2_size": 4,
                   "diff": self.diff, "jobs": {}, "prev": None, "count": 0,
                   "height": self.height, "authorized": False}
        started = time.time()
        write_lock = threading.Lock()

        def send(message):
            with write_lock:
                conn.sendall((json.dumps(message) + "\n").encode())

        def ticker():
            beat = 0
            while self.running and session["authorized"]:
                time.sleep(self.job_every)
                beat += 1
                try:
                    if self.drop_after and time.time() - started > self.drop_after:
                        conn.shutdown(socket.SHUT_RDWR)
                        return
                    send(self._job(session, beat % 3 == 0))
                except OSError:
                    return

        try:
            for line in conn.makefile("r"):
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                method, params, rid = message.get("method"), message.get("params"), message.get("id")
                if method == "mining.subscribe":
                    self.agents.append(params[0] if params else "")
                    send({"id": rid, "error": None, "result": [
                        [["mining.set_difficulty", "d"], ["mining.notify", "n"]],
                        session["en1"], session["en2_size"]]})
                elif method == "mining.authorize":
                    self.users.append(params[0])
                    if self.refuse_login:
                        send({"id": rid, "error": [24, "Unauthorized worker", None], "result": None})
                        continue
                    send({"id": rid, "error": None, "result": True})
                    send({"id": None, "method": "mining.set_difficulty", "params": [session["diff"]]})
                    send(self._job(session, True))
                    session["authorized"] = True
                    thread = threading.Thread(target=ticker)
                    thread.daemon = True
                    thread.start()
                elif method == "mining.suggest_difficulty":
                    self.suggested.append(params[0])
                    send({"id": rid, "error": None, "result": True})
                    if self.honor_suggest:
                        session["diff"] = float(params[0])
                        send({"id": None, "method": "mining.set_difficulty", "params": [session["diff"]]})
                        if session["authorized"] and self.suggest_sends_job:
                            send(self._job(session, True))
                elif method == "mining.submit":
                    error = self._check(session, params)
                    with self.lock:
                        self.submits += 1
                        if error:
                            self.rejected += 1
                            self.reasons.append(error[1])
                        else:
                            self.accepted += 1
                    send({"id": rid, "error": error, "result": None if error else True})
        except (OSError, ValueError):
            pass
        finally:
            session["authorized"] = False
            try:
                conn.close()
            except OSError:
                pass
