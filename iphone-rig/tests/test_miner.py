"""Tests for miner.py. Run from the iphone-rig folder:  python3 -m unittest discover tests

The unit tests use real Bitcoin data (the genesis block, block 125552, block
170 and the address examples from BIP173/BIP350). The rig tests run the whole
miner against tests/mock_pool.py, which re-checks every share independently.
"""
import binascii
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import miner                                    # noqa: E402
from mock_pool import MockPool                  # noqa: E402

ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"          # BIP173 example
SCRIPT = binascii.unhexlify("0014751e76e8199196d454941c45d1b3a323f1433bd6")
GENESIS_COINBASE = (
    "01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff4d04ffff"
    "001d0104455468652054696d65732030332f4a616e2f32303039204368616e63656c6c6f72206f6e206272696e"
    "6b206f66207365636f6e64206261696c6f757420666f722062616e6b73ffffffff0100f2052a01000000434104"
    "678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51e"
    "c112de5c384df7ba0b8d578a4c702b6bf11d5fac00000000")
GENESIS_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"


class FakePool(object):
    name = "fake:1"

    def __init__(self, en1, en2_size):
        self.en1, self.en2_size = en1, en2_size


def sheet(*pools, **extra):
    data = {"coin": "BTC", "payout_check": "enforce", "pools": list(pools)}
    data.update(extra)
    return data


def closed_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return "127.0.0.1:%d" % port


class Bitcoin(unittest.TestCase):
    def test_block_125552_hash_and_nonce(self):
        self.assertEqual(miner.sha256d(miner.KAT_HEADER)[::-1].hex(), miner.KAT_HASH)
        self.assertEqual(int.from_bytes(miner.KAT_HEADER[76:], "little"), miner.KAT_NONCE)

    def test_every_scanner_finds_the_known_nonce(self):
        for _, sha in miner.sha_candidates():
            for scan in (miner.scan_midstate, miner.scan_full):
                hits = scan(sha, miner.KAT_HEADER[:64], miner.KAT_HEADER[64:76],
                            miner.KAT_NONCE - 500, 1000, b"\x00\x00\x00\x00")
                self.assertEqual([n for n, _ in hits], [miner.KAT_NONCE])
                self.assertEqual(hits[0][1][::-1].hex(), miner.KAT_HASH)

    def test_genesis_block_rebuilt_from_a_stratum_job(self):
        # Cut the real genesis coinbase where a pool would: extranonce1 and
        # extranonce2 are 4 bytes each from the middle of its message.
        raw = binascii.unhexlify(GENESIS_COINBASE)
        coinb1, en1, en2, coinb2 = raw[:60], raw[60:64], raw[64:68], raw[68:]
        job = miner.Job(["1", "00" * 32, coinb1.hex(), coinb2.hex(), [], "00000001",
                         "1d00ffff", "495fab29", True], 1.0)
        rig = miner.Rig(ADDRESS, sheet("x:1", payout_check="off"), folder=tempfile.mkdtemp())
        rig.en2 = int.from_bytes(en2, "big") - 1
        _, en2_hex, first64, tail12 = rig.make_work(FakePool(en1, 4), job)
        self.assertEqual(en2_hex, en2.hex())
        header = first64 + tail12 + (2083236893).to_bytes(4, "little")
        self.assertEqual(miner.sha256d(header)[::-1].hex(), GENESIS_HASH)
        self.assertEqual(job.target, miner.DIFF1)
        self.assertAlmostEqual(job.net_diff, 1.0)
        self.assertEqual(miner.coinbase_outputs(raw)[0][0], 50 * 10 ** 8)
        # the genesis hash itself is a "difficulty 2536" share
        self.assertAlmostEqual(miner.DIFF1 / int(GENESIS_HASH, 16), 2536.43, places=2)

    def test_block_125552_header_fields_from_stratum_encoding(self):
        shown = "00000000000008a3a41b85b8b29ad444def299fee21793cd8b9e567eab02cd81"
        wire = binascii.unhexlify(shown)[::-1]
        stratum = b"".join(wire[i:i + 4][::-1] for i in range(0, 32, 4)).hex()
        job = miner.Job(["1", stratum, "00" * 50, "00", [], "00000001", "1a44b9f2",
                         "4dd7f5c7", True], 1.0)
        self.assertEqual(job.version_le + job.prev, miner.KAT_HEADER[:36])
        self.assertEqual(job.ntime_le + job.nbits_le, miner.KAT_HEADER[68:76])
        self.assertAlmostEqual(job.net_diff, 244112.49, places=1)

    def test_merkle_fold_matches_block_170(self):
        coinbase = "b1fea52486ce0c62bb442b530a3f0132b826c74e473d1f2c220bfa78111c5082"
        payment = "f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16"
        root = "7dac2c5666815c17a3b36427de37bb9d2e2c5ccec3f8633eb91a4205cb4c10ff"
        folded = miner.sha256d(binascii.unhexlify(coinbase)[::-1] + binascii.unhexlify(payment)[::-1])
        self.assertEqual(folded[::-1].hex(), root)

    def test_malformed_jobs_are_rejected(self):
        for params in (["1", "00" * 31, "00", "00", [], "00000001", "1d00ffff", "495fab29"],
                       ["1", "00" * 32, "zz", "00", [], "00000001", "1d00ffff", "495fab29"],
                       ["1", "00" * 32, "00", "00", [], "00000001", "00000000", "495fab29"]):
            with self.assertRaises((ValueError, TypeError)):
                miner.Job(params, 1.0)


class Addresses(unittest.TestCase):
    def test_known_addresses(self):
        cases = {
            ADDRESS: SCRIPT.hex(),
            ADDRESS.upper(): SCRIPT.hex(),
            "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0":
                "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa":
                "76a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac",
            "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy":
                "a914b472a266d0bd89c13706a4132ccfb16f7c3b9fcb87",
        }
        for address, script in cases.items():
            self.assertEqual(miner.address_to_script(address).hex(), script, address)

    def test_typos_and_foreign_addresses_are_refused(self):
        for bad in (ADDRESS[:-1] + "5", ADDRESS[:10] + "Q" + ADDRESS[11:],
                    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb", "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNL",
                    "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "0x52908400098527886E0F7030069857D2E4169EE7",
                    "bc1", "", "hello"):
            with self.assertRaises(ValueError, msg=bad):
                miner.address_to_script(bad)


class Numbers(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(miner.nbits_to_target(0x1D00FFFF), miner.DIFF1)
        self.assertEqual(miner.nbits_to_target(0x1A44B9F2), 0x44B9F2 << (8 * 23))

    def test_formatting(self):
        self.assertEqual(miner.fmt_rate(1.4e6), "1.40 MH/s")
        self.assertEqual(miner.fmt_rate(52300), "52.3 kH/s")
        self.assertEqual(miner.fmt_rate(999.96), "1000 H/s")
        self.assertEqual(miner.fmt_diff(1.52e14), "152T")
        self.assertEqual(miner.fmt_diff(0.004657), "0.004657")
        self.assertEqual(miner.fmt_count(2.04e10), "20.4 billion")
        self.assertEqual(miner.fmt_count(999.96e6), "1000 million")
        self.assertEqual(miner.fmt_dur(3725), "1:02:05")
        self.assertEqual(miner.fmt_dur(200000), "2d 07h 33m")

    def test_payout_share_of_a_coinbase(self):
        pool = MockPool(SCRIPT)
        try:
            for fraction, mode, expect in ((1.0, "enforce", 1.0), (0.98, "enforce", 0.98),
                                           (0.0, "warn", 0.0)):
                pool.pay_fraction = fraction
                session = {"en1": "01020304", "en2_size": 4, "diff": 1, "jobs": {}, "prev": None,
                           "count": 0, "height": 1}
                params = pool._job(session, True)["params"]
                rig = miner.Rig(ADDRESS, sheet("x:1", payout_check=mode), folder=tempfile.mkdtemp(),
                                out=io.StringIO())
                rig.make_work(FakePool(b"\x01\x02\x03\x04", 4), miner.Job(params, 1.0))
                self.assertAlmostEqual(rig.payout, expect, places=6)
                self.assertEqual(rig.reward, pool.reward)
            pool.pay_fraction = 0.5
            params = pool._job(session, True)["params"]
            rig = miner.Rig(ADDRESS, sheet("x:1"), folder=tempfile.mkdtemp(), out=io.StringIO())
            with self.assertRaises(miner.PayoutError):
                rig.make_work(FakePool(b"\x01\x02\x03\x04", 4), miner.Job(params, 1.0))
        finally:
            pool.stop()


class RigRuns(unittest.TestCase):
    """The whole miner against a pool that re-checks every share."""

    def setUp(self):
        self.pools, self.rig, self.thread = [], None, None
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        self.finish()
        for pool in self.pools:
            pool.stop()

    def pool(self, **options):
        pool = MockPool(options.pop("pay_script", SCRIPT), **options)
        self.pools.append(pool)
        return pool

    def start(self, *pools, **options):
        options.setdefault("cores", 1)
        self.out = io.StringIO()
        self.rig = miner.Rig(ADDRESS, sheet(*pools), interval=1, folder=self.folder,
                             backoff_cap=1, out=self.out, **options)
        self.thread = threading.Thread(target=self.rig.run)
        self.thread.daemon = True
        self.thread.start()
        return self.rig

    def wait(self, condition, seconds=40):
        end = time.time() + seconds
        while time.time() < end:
            if condition():
                return True
            time.sleep(0.1)
        return False

    def saved(self, name):
        with open(os.path.join(self.folder, name)) as handle:
            return json.load(handle)

    def finish(self):
        if self.rig is not None and self.thread.is_alive():
            self.rig.stop = True
            self.thread.join(15)
        return self.out.getvalue() if self.rig is not None else ""

    def test_shares_are_accepted_by_an_independent_checker(self):
        pool = self.pool()
        rig = self.start(pool.address)
        self.assertTrue(self.wait(lambda: pool.accepted >= 5), self.out.getvalue())
        log = self.finish()
        self.assertEqual(pool.rejected, 0, pool.reasons)
        self.assertEqual(pool.users, [ADDRESS + ".iphone"])
        self.assertEqual(pool.agents, [miner.AGENT])
        self.assertLessEqual(abs(rig.accepted - pool.accepted), 1)
        self.assertEqual(rig.payout, 1.0)
        self.assertEqual(rig.reward, pool.reward)
        self.assertGreater(rig.height, 921000)
        self.assertEqual(rig.pool_diff, 0.0002)
        self.assertIn("accepted #1", log)
        self.assertIn("odds 1 in", log)
        saved = self.saved("rig_stats.json")
        self.assertGreater(saved["hashes"], 0)
        self.assertGreaterEqual(saved["accepted"], 5)
        self.assertGreater(saved["best"], 0.0002)

    def test_requested_difficulty_is_used_once_the_pool_grants_it(self):
        pool = self.pool(honor_suggest=True, diff=1.0)
        rig = self.start(pool.address)
        self.assertTrue(self.wait(lambda: pool.accepted >= 1, 90), self.out.getvalue())
        self.finish()
        self.assertEqual(pool.rejected, 0, pool.reasons)
        self.assertTrue(1e-6 <= pool.suggested[0] < 1.0, pool.suggested)
        self.assertEqual(rig.pool_diff, pool.suggested[0])

    def test_public_pool_style_difficulty_takes_effect_without_a_new_job(self):
        # public-pool opens at difficulty 100000, answers the suggestion with a
        # set_difficulty but no new job, and sends jobs only once a minute.
        pool = self.pool(diff=100000, honor_suggest=True, suggest_sends_job=False,
                         judge_by_session=True, job_every=600)
        with mock.patch.object(miner.Rig, "suggestion", return_value=0.0002):
            rig = self.start(pool.address)
            self.assertTrue(self.wait(lambda: pool.accepted >= 3, 30), self.out.getvalue())
        log = self.finish()
        self.assertEqual(pool.rejected, 0, pool.reasons)
        self.assertEqual(rig.pool_diff, 0.0002)
        self.assertIn("accepted #1", log)

    def test_a_dead_pool_is_skipped_for_the_backup(self):
        dead, pool = closed_port(), self.pool()
        self.start(dead, pool.address)
        self.assertTrue(self.wait(lambda: pool.accepted >= 2), self.out.getvalue())
        log = self.finish()
        self.assertIn("connecting to " + dead, log)
        self.assertIn("connecting to " + pool.address, log)

    def test_a_pool_that_pays_someone_else_gets_no_hashes(self):
        thief = self.pool(pay_script=binascii.unhexlify("76a914" + "11" * 20 + "88ac"))
        honest = self.pool()
        self.start(thief.address, honest.address)
        self.assertTrue(self.wait(lambda: honest.accepted >= 2), self.out.getvalue())
        log = self.finish()
        self.assertEqual(thief.submits, 0)
        self.assertIn("refused: its work pays 0% of the reward to your address", log)

    def test_a_refused_login_moves_on(self):
        strict, pool = self.pool(refuse_login=True), self.pool()
        self.start(strict.address, pool.address)
        self.assertTrue(self.wait(lambda: pool.accepted >= 1), self.out.getvalue())
        self.assertIn("Unauthorized worker", self.finish())

    def test_it_reconnects_when_the_pool_drops_it(self):
        pool = self.pool(drop_after=2.5)
        self.start(pool.address)
        self.assertTrue(self.wait(lambda: pool.connections >= 3 and pool.accepted >= 3),
                        self.out.getvalue())
        self.finish()
        self.assertEqual(pool.rejected, 0, pool.reasons)

    def test_it_recovers_after_the_phone_slept(self):
        pool = self.pool()
        self.start(pool.address)
        self.assertTrue(self.wait(lambda: pool.accepted >= 1), self.out.getvalue())
        real = time.time
        miner.time.time = lambda: real() + 100       # the clock jumps, as after a freeze
        try:
            self.assertTrue(self.wait(lambda: pool.connections >= 2 and pool.accepted >= 3),
                            self.out.getvalue())
            log = self.finish()
        finally:
            miner.time.time = real
        self.assertIn("the phone was asleep for 0:01:4", log)
        self.assertEqual(pool.rejected, 0, pool.reasons)

    def test_a_found_block_is_reported_and_saved(self):
        pool = self.pool(nbits="207fffff")           # every share is also a block
        rig = self.start(pool.address)
        self.assertTrue(self.wait(lambda: pool.blocks >= 1 and rig.blocks), self.out.getvalue())
        log = self.finish()
        self.assertIn("*** BLOCK FOUND ***", log)
        saved = self.saved("rig_stats.json")
        self.assertEqual(saved["blocks"][0]["pool"], pool.address)
        self.assertEqual(len(saved["blocks"][0]["hash"]), 64)

    def test_all_core_mode_produces_valid_shares(self):
        if miner.interpreter_api() is None:
            self.skipTest("this Python has no sub-interpreters")
        pool = self.pool()
        rig = self.start(pool.address, cores=2)
        self.assertTrue(self.wait(lambda: pool.accepted >= 5), self.out.getvalue())
        engine = rig.engine
        self.finish()
        self.assertEqual(pool.rejected, 0, pool.reasons)
        self.assertEqual(engine, "2 cores")
        self.assertEqual(self.saved("rig_engine.json"), {"multicore": "ok"})
        self.assertIsNone(rig.cores)                 # closed cleanly on stop

    def test_all_core_mode_is_not_retried_after_it_died_starting(self):
        with open(os.path.join(self.folder, "rig_engine.json"), "w") as handle:
            json.dump({"multicore": "trying"}, handle)
        pool = self.pool()
        rig = self.start(pool.address, cores=None)
        self.assertTrue(self.wait(lambda: pool.accepted >= 1), self.out.getvalue())
        self.assertNotIn("cores", rig.engine)
        self.assertIn("did not start last time", self.finish())

    def test_losing_the_cores_mid_run_falls_back_to_one(self):
        if miner.interpreter_api() is None:
            self.skipTest("this Python has no sub-interpreters")
        pool = self.pool()
        rig = self.start(pool.address, cores=2)
        self.assertTrue(self.wait(lambda: pool.accepted >= 1), self.out.getvalue())
        def broken(*args):
            raise RuntimeError("core lost")
        rig.scan = broken
        before = pool.accepted
        self.assertTrue(self.wait(lambda: pool.accepted >= before + 3), self.out.getvalue())
        log = self.finish()
        self.assertIn("continuing on one core", log)
        self.assertEqual(pool.rejected, 0, pool.reasons)

    def test_dashboard_serves_page_and_live_numbers(self):
        pool = self.pool()
        rig = self.start(pool.address, web=0)
        self.assertTrue(self.wait(lambda: pool.accepted >= 1 and hasattr(rig, "server")))
        base = "http://127.0.0.1:%d" % rig.server.server_address[1]
        page = urllib.request.urlopen(base + "/").read().decode()
        self.assertIn("<title>iPhone Rig</title>", page)
        stats = json.loads(urllib.request.urlopen(base + "/api/stats").read().decode())
        self.assertEqual(stats["state"], "mining")
        self.assertEqual(stats["pool"], pool.address)
        self.assertGreaterEqual(stats["accepted"], 1)
        self.assertEqual(stats["address"], "bc1qw508...v8f3t4")
        self.assertNotIn(ADDRESS, json.dumps(stats))
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/rig.json")


class CommandLine(unittest.TestCase):
    def test_settings_are_remembered_and_typos_stop_it(self):
        folder = tempfile.mkdtemp()
        os.environ["IPHONE_RIG_HOME"] = folder
        try:
            with self.assertRaises(SystemExit) as stop:
                miner.main([ADDRESS[:-1] + "5"])
            self.assertIn("typo", str(stop.exception))
            self.assertFalse(os.path.exists(os.path.join(folder, "rig.json")))
            with self.assertRaises(SystemExit):
                miner.main([ADDRESS, "--sheet", "nope"])
        finally:
            del os.environ["IPHONE_RIG_HOME"]


if __name__ == "__main__":
    unittest.main()
