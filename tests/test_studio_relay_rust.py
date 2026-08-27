"""The Rust studio's relay, media probes and server ops — B5 pass 5.

The relay round-trips the castle emulator: status bridging (with the TTL
caches on both sides), transport verbs, card writes held to the byte
count, pulls through /studio/card, and the remote page. Probe and compare
ride the same binaries and the same Python scorer, so their answers are
byte-comparable; restart and stop are exercised for real — the servers
come back (execv) or die, on both implementations.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
import urllib.error
from pathlib import Path
from typing import Any, ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rust_case import CARGO, IN_CI, ROOT, StudioPair, fetch, wait_up

sys.path.insert(0, str(ROOT / "tools"))
import castle_emu

VOLATILE = ("uptime_s", "psram_free_kb", "heap_free_kb", "sd_free_kb")


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class EmulatorRelay(StudioPair):
    emu: ClassVar[castle_emu.CastleEmu]

    @classmethod
    def setUpClass(cls) -> None:
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=None, scenes=["vigil", "storm"])
        cls.emu.start()
        cls.HOST_ENV = f"127.0.0.1:{cls.emu.port}"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.emu.shutdown()
        cls.emu.server_close()

    def stable(self, raw: tuple[int, dict[str, str], bytes]) -> dict[str, Any]:
        d = self.parsed(raw)
        assert isinstance(d, dict)
        for k in VOLATILE:
            d.pop(k, None)
        return d

    def test_01_status_bridges_the_castle(self) -> None:
        a, b = self.both("/api/status")
        da, db = self.stable(a), self.stable(b)
        self.assertEqual(da, db)
        self.assertEqual(da.get("bridged"), self.HOST_ENV)
        self.assertNotIn("studio", da)

    def test_02_transport_verbs_relay_verbatim(self) -> None:
        for path in ("/api/scene?s=vigil", "/api/volume?v=40", "/api/stop"):
            a, b = self.both(path, method="POST")
            self.assertEqual(a[0], b[0], path)
            self.assertEqual(a[2], b[2], path)
        # A refused verb comes back as the castle's own verdict.
        a, b = self.both("/api/scene?s=nosuch", method="POST")
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[2], b[2])

    def test_03_card_writes_reads_and_deletes(self) -> None:
        payload = bytes(range(256)) * 8
        a = fetch(self.py_port, "/api/files/parity.mp3", "PUT", None, payload)
        b = fetch(self.rs_port, "/api/files/parity.mp3", "PUT", None, payload)
        self.assertEqual(a[0], b[0], (a[2], b[2]))
        self.assertEqual(a[2], b[2])
        a, b = self.both("/api/files")
        self.assertEqual(self.parsed(a), self.parsed(b))
        a, b = self.both("/studio/card/parity.mp3")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[2], payload)
        # One delete per server, against its own seeded file; the bodies
        # must agree once the name is normalised out.
        assert self.emu.sd_dir is not None
        for side, port in (("del_py", self.py_port), ("del_rs", self.rs_port)):
            (Path(self.emu.sd_dir) / f"{side}.mp3").write_bytes(b"x" * 64)
        a = fetch(self.py_port, "/api/files/del_py.mp3", "DELETE")
        b = fetch(self.rs_port, "/api/files/del_rs.mp3", "DELETE")
        self.assertEqual(a[0], b[0])
        self.assertEqual(
            a[2].replace(b"del_py", b"del_X"), b[2].replace(b"del_rs", b"del_X")
        )

    def test_04_the_remote_page_relays(self) -> None:
        a, b = self.both("/remote")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[1].get("content-type"), b[1].get("content-type"))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class PublishToTwinCastles(StudioPair):
    """Each server publishes toward its OWN emulator. sd_sync's host
    validation currently refuses a nonstandard-port castle (the emulator
    chain), so what parity can hold today is the FAILURE: both servers
    spawn the same sd_sync, get the same refusal, and answer the same
    body. The success path runs on the porch, against port 80."""

    emu_py: ClassVar[castle_emu.CastleEmu]
    emu_rs: ClassVar[castle_emu.CastleEmu]

    @classmethod
    def setUpClass(cls) -> None:
        cls.emu_py = castle_emu.CastleEmu(port=0, sd_dir=None, scenes=["vigil"])
        cls.emu_rs = castle_emu.CastleEmu(port=0, sd_dir=None, scenes=["vigil"])
        cls.emu_py.start()
        cls.emu_rs.start()
        cls.HOST_ENV_PY = f"127.0.0.1:{cls.emu_py.port}"
        cls.HOST_ENV_RS = f"127.0.0.1:{cls.emu_rs.port}"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        for emu in (cls.emu_py, cls.emu_rs):
            emu.shutdown()
            emu.server_close()

    def masked_all(self, text: str, side: str) -> str:
        host = self.HOST_ENV_PY if side == "py" else self.HOST_ENV_RS
        assert host is not None
        return self.masked(text, side).replace(host, "<CASTLE>")

    def cards_match(self) -> None:
        import gzip

        assert self.emu_py.sd_dir is not None and self.emu_rs.sd_dir is not None
        pa, pb = Path(self.emu_py.sd_dir), Path(self.emu_rs.sd_dir)
        la = sorted(str(f.relative_to(pa)) for f in pa.rglob("*") if f.is_file())
        lb = sorted(str(f.relative_to(pb)) for f in pb.rglob("*") if f.is_file())
        self.assertEqual(la, lb)
        for rel in la:
            da, db = (pa / rel).read_bytes(), (pb / rel).read_bytes()
            if rel.endswith(".gz"):
                # gzip stamps its mtime into the header; the content is
                # the contract.
                da, db = gzip.decompress(da), gzip.decompress(db)
            self.assertEqual(da, db, rel)

    def test_publish_reports_alike_even_in_refusal(self) -> None:
        a, b = self.both("/studio/publish", method="POST")
        self.assertEqual(a[0], 500, a[2][:400])
        da, db = self.parsed(a), self.parsed(b)
        assert isinstance(da, dict) and isinstance(db, dict)
        la, lb = str(da.pop("log")), str(db.pop("log"))
        self.assertEqual(da, db)
        self.assertEqual(self.masked_all(la, "py"), self.masked_all(lb, "rs"))
        self.assertEqual(da["error"], "sd_sync scenes failed")
        self.cards_match()  # nothing landed on either card


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class MediaAndOps(StudioPair):
    def test_00_publish_without_a_castle(self) -> None:
        a, b = self.both("/studio/publish", method="POST")
        self.assertEqual(a[0], 502)
        self.assertEqual(a[2], b[2])

    def test_01_probe_speaks_with_one_voice(self) -> None:
        hdrs = {"Content-Type": "application/json"}
        for url in ("notalink", "https://127.0.0.1:1/never.wav"):
            body = json.dumps({"url": url}).encode()
            a, b = self.both("/studio/probe", "POST", hdrs, body)
            self.assertEqual(a[0], b[0], url)
            self.assertEqual(a[2], b[2], url)

    def test_02_compare_ranks_the_codecs_alike(self) -> None:
        hdrs = {"Content-Type": "application/json"}
        a, b = self.both(
            "/studio/compare",
            "POST",
            hdrs,
            json.dumps({"id": "t_alpha", "take": 0.5, "bitrate": 64}).encode(),
        )
        self.assertEqual(a[0], 200, a[2][:400])
        da, db = self.parsed(a), self.parsed(b)
        assert isinstance(da, dict) and isinstance(db, dict)
        tok_a, tok_b = str(da.pop("token")), str(db.pop("token"))
        for d, tok in ((da, tok_a), (db, tok_b)):
            for row in d["codecs"]:
                row["url"] = str(row["url"]).replace(tok, "<TOK>")
        self.assertEqual(da, db)
        # The encodes behind the rows are the same bytes on both sides.
        a2 = fetch(self.py_port, f"/studio/compare/{tok_a}/mp3")
        b2 = fetch(self.rs_port, f"/studio/compare/{tok_b}/mp3")
        self.assertEqual(a2[0], 200)
        self.assertEqual(a2[2], b2[2])
        # And the mistakes match, word for word.
        a, b = self.both(
            "/studio/compare", "POST", hdrs, json.dumps({"id": "zzz"}).encode()
        )
        self.assertEqual(a[0], 404)
        self.assertEqual(a[2], b[2])
        a, b = self.both(
            "/studio/compare",
            "POST",
            hdrs,
            json.dumps({"id": "t_alpha", "start": "abc"}).encode(),
        )
        self.assertEqual(a[0], 500)
        self.assertEqual(a[2], b[2])

    def test_03_restart_answers_then_comes_back(self) -> None:
        a, b = self.both("/studio/server/restart", "POST")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        time.sleep(1.0)
        wait_up(self.py_port)
        wait_up(self.rs_port)
        a, b = self.both("/studio/tracks")
        self.assertEqual(a[0], 200)

    def test_04_stop_answers_then_dies(self) -> None:
        a, b = self.both("/studio/server/stop", "POST")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        for port in (self.py_port, self.rs_port):
            end = time.monotonic() + 15
            while time.monotonic() < end:
                try:
                    fetch(port, "/api/status")
                    time.sleep(0.1)
                except (urllib.error.URLError, OSError):
                    break
            else:
                self.fail(f"server on {port} never stopped")


if __name__ == "__main__":
    unittest.main()
