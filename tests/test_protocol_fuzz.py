"""The protocol fuzz (tools/castle_fuzz.py) on a small budget, every run.

Seeded and deterministic: CASTLE_FUZZ_SEED picks the seed (default 1), and
every failure message carries it so the run replays with
`tools/castle_fuzz.py --seed N`. The storm is the invariant check; the
named cases below are the edges the fuzz cannot time (slow-loris) or that
earned their own regression line.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu  # noqa: E402
import castle_emu_http  # noqa: E402
from castle_fuzz import Fuzzer, raw_request  # noqa: E402

SEED = int(os.environ.get("CASTLE_FUZZ_SEED", "1"))


class FuzzCase(unittest.TestCase):
    jail: Path
    card: Path
    emu: castle_emu.CastleEmu

    @classmethod
    def setUpClass(cls) -> None:
        cls.jail = Path(tempfile.mkdtemp(prefix="fuzz-jail-"))
        cls.card = cls.jail / "card"
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=cls.card,
                                       scenes=["vigil", "storm", "stop"])
        cls.emu.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()
        cls.emu.server_close()

    def req(self, method: str, target: str, **kw):
        return raw_request("127.0.0.1", self.emu.port, method, target, **kw)

    def fuzzer(self) -> Fuzzer:
        return Fuzzer("127.0.0.1", self.emu.port, SEED, self.card)


class TestStorm(FuzzCase):
    def test_single_threaded_storm_holds_every_invariant(self) -> None:
        self.fuzzer().run(600, threads=1)

    def test_concurrent_storm_holds_every_invariant(self) -> None:
        self.fuzzer().run(900, threads=6)

    def test_nothing_landed_outside_the_card(self) -> None:
        """The jail holds the card and nothing else, whatever the names were."""
        stray = [p for p in self.jail.iterdir() if p.name != "card"]
        self.assertEqual(stray, [])


class TestBodies(FuzzCase):
    def test_zero_byte_put(self) -> None:
        code, body, _ = self.req("PUT", "/api/files/empty.bin", body=b"")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"path": "/sd/empty.bin", "bytes": 0})
        self.assertEqual((self.card / "empty.bin").read_bytes(), b"")

    def test_two_megabyte_put_arrives_intact(self) -> None:
        payload = os.urandom(2 * 1024 * 1024)
        code, body, _ = self.req("PUT", "/api/files/big.bin", body=payload, timeout=30)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["bytes"], len(payload))
        self.assertEqual((self.card / "big.bin").read_bytes(), payload)
        (self.card / "big.bin").unlink()

    def test_slow_loris_body_is_a_short_write_not_a_hang(self) -> None:
        """Half the body, then silence on an open socket: the httpd's recv
        timer fires, the half-file is unlinked, the client hears 500."""
        with mock.patch.object(castle_emu_http.Handler, "timeout", 0.5):
            t0 = time.monotonic()
            code, body, _ = self.req("PUT", "/api/files/loris.bin", body=b"x" * 4000,
                                     send_fraction=0.5, hang=True, timeout=5)
            took = time.monotonic() - t0
        self.assertEqual((code, body), (500, b"short write"))
        self.assertLess(took, 3.0)
        self.assertFalse((self.card / "loris.bin").exists())
        # and the server is fine
        self.assertEqual(self.req("GET", "/api/status")[0], 200)

    def test_short_body_with_eof_is_a_short_write(self) -> None:
        code, body, _ = self.req("PUT", "/api/files/short.bin", body=b"abc", declared=10)
        self.assertEqual((code, body), (500, b"short write"))
        self.assertFalse((self.card / "short.bin").exists())

    def test_extra_bytes_never_reach_the_file(self) -> None:
        code, _, _ = self.req("PUT", "/api/files/extra.bin", body=b"abcdef", declared=4)
        if code:                 # the server may RST the unread tail instead
            self.assertEqual(code, 200)
        self.assertEqual((self.card / "extra.bin").read_bytes(), b"abcd")

    def test_body_on_a_post_is_ignored(self) -> None:
        code, body, _ = self.req("POST", "/api/stop", body=b"{}" * 100)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"queued": True})


class TestNulAndOddNames(FuzzCase):
    """The firmware's url_decode can mint a NUL; C then truncates there."""

    def test_nul_only_name_is_cannot_create_file(self) -> None:
        """PUT /api/files/%zz → safe_name("\\0") passes, fopen("/sd/") fails."""
        code, body, _ = self.req("PUT", "/api/files/%zz", body=b"x")
        self.assertEqual((code, body), (500, b"cannot create file"))

    def test_nul_truncates_the_stored_name(self) -> None:
        code, body, _ = self.req("PUT", "/api/files/ab%zzcd.mp3", body=b"x")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["path"], "/sd/ab")
        self.assertTrue((self.card / "ab").is_file())
        self.assertEqual(self.req("DELETE", "/api/files/ab%00cd")[0], 200)

    def test_nul_in_play_does_not_kill_the_ticker(self) -> None:
        """Regression: the old emulator's ticker died on Path("\\0") and
        never applied another command — a silent, permanent hang."""
        self.assertEqual(self.req("POST", "/api/play?f=%00")[0], 200)
        self.assertEqual(self.req("POST", "/api/volume?v=42")[0], 200)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if json.loads(self.req("GET", "/api/status")[1])["volume"] == 42:
                break
            time.sleep(0.05)
        self.assertEqual(json.loads(self.req("GET", "/api/status")[1])["volume"], 42)

    def test_plus_is_a_space_and_percent_3f_cuts_the_name(self) -> None:
        self.assertEqual(self.req("PUT", "/api/files/a+b.mp3", body=b"x")[0], 200)
        self.assertTrue((self.card / "a b.mp3").is_file())
        _, body, _ = self.req("PUT", "/api/files/cut%3Fhere.mp3", body=b"x")
        self.assertEqual(json.loads(body)["path"], "/sd/cut")
        for n in ("a b.mp3", "cut"):
            (self.card / n).unlink()

    def test_unicode_name_is_measured_in_bytes(self) -> None:
        ok = "é" * 49 + ".mp3"          # 98 + 4 = 102 bytes → too long
        self.assertEqual(self.req("PUT", "/api/files/" + ok.encode().decode("latin-1"),
                                  body=b"x")[0], 400)
        fine = "é" * 40 + ".mp3"        # 84 bytes
        self.assertEqual(self.req("PUT", "/api/files/" + fine.encode().decode("latin-1"),
                                  body=b"x")[0], 200)
        (self.card / fine).unlink()


if __name__ == "__main__":
    unittest.main()
