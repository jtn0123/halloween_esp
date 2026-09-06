"""The Rust studio's publish, media probes and server ops — absolutely.

The other half of the port described in tests/test_studio_relay_rs.py:
what `tests/test_studio_relay_rust.py`'s `PublishToTwinCastles` and
`MediaAndOps` measured by holding two servers side by side, restated as
the answer itself now that only one server is left.

Publish gets an emulated castle of its own so the push has somewhere real
to land and the card can be listed afterwards; probe, compare and the
server ops get a castle-less studio, because none of them talk to
hardware. Restart and stop live in their own fixture: one execv's the
process and the other kills it, and neither should be able to take a
suite's shared server with it.
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

from studio_rs_case import CARGO, IN_CI, ROOT, StudioCase, fetch, wait_up

sys.path.insert(0, str(ROOT / "tools"))
import castle_emu

JSON_HDRS = {"Content-Type": "application/json"}


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Publish(StudioCase):
    """POST /studio/publish with a castle answering: the scene tracks and
    the lean page go to the card, and what a push CANNOT fix is named."""

    emu: ClassVar[castle_emu.CastleEmu]

    @classmethod
    def setUpClass(cls) -> None:
        # Built knowing only `vigil` — so the show's second scene is a
        # firmware gap the publish has to report rather than paper over.
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=None, scenes=["vigil"])
        cls.emu.start()
        cls.HOST_ENV = f"127.0.0.1:{cls.emu.port}"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.emu.shutdown()
        cls.emu.server_close()

    def test_publish_lands_on_the_card(self) -> None:
        code, body = self.json("/studio/publish", "POST")
        self.assertEqual(code, 200, body)
        log = str(body.pop("log"))
        self.assertEqual(
            body,
            {
                "ok": True,
                "pushed": True,
                "needs_firmware": ["storm"],
                "note": "1 scene(s) missing from the running firmware — "
                "make sd-build, stop audio, then OTA",
            },
        )
        masked = self.masked(log).replace(self.HOST_ENV, "<CASTLE>")
        self.assertIn("source: <BUILD>/audio/", masked)
        self.assertIn("uploading 01_vigil.mp3", masked)
        self.assertIn("1 scene tracks in /sd/scenes/", masked)
        self.assertIn("http://<CASTLE>/ now serves the LEAN cue desk", masked)
        # The scene track to /sd/scenes, the lean page pair and the
        # per-scene mp3 to /sd/site — those four files and nothing else.
        sd = Path(self.emu.sd_dir)
        self.assertEqual(
            sorted(str(f.relative_to(sd)) for f in sd.rglob("*") if f.is_file()),
            [
                "scenes/01_vigil.mp3",
                "site/index.html",
                "site/index.html.gz",
                "site/vigil.mp3",
            ],
        )
        # The lean page is the rewrite, not the inlined desk: the scene
        # data URIs are gone and the non-scene one survived.
        page = (sd / "site" / "index.html").read_bytes()
        self.assertNotIn(b"SGVsbG8=", page)
        self.assertIn(b"data:audio/mpeg;base64,QUJD", page)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Media(StudioCase):
    """Probe, compare and the castle-less publish — no hardware needed."""

    def post(self, path: str, obj: object) -> tuple[int, dict[str, Any]]:
        code, _, raw = self.req(path, "POST", JSON_HDRS, json.dumps(obj).encode())
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), parsed
        return code, parsed

    def test_00_publish_without_a_castle(self) -> None:
        code, body = self.json("/studio/publish", "POST")
        self.assertEqual(
            (code, body),
            (
                502,
                {
                    "ok": False,
                    "pushed": False,
                    "error": "no castle answered — nothing pushed",
                },
            ),
        )

    def test_01_probe_refuses_a_non_link_before_it_spawns_anything(self) -> None:
        code, body = self.post("/studio/probe", {"url": "notalink"})
        self.assertEqual(
            (code, body),
            (400, {"ok": False, "error": "that does not look like a link"}),
        )

    def test_02_probe_of_an_unreachable_url_is_a_400_not_a_500(self) -> None:
        code, body = self.post(
            "/studio/probe", {"url": "https://127.0.0.1:1/never.wav"}
        )
        self.assertEqual(code, 400)
        self.assertIs(body["ok"], False)
        # The fetcher's own words, carrying the address that failed —
        # the operator needs to see WHICH url did not answer.
        self.assertIn("127.0.0.1", str(body["error"]))

    def test_03_compare_ranks_the_codecs(self) -> None:
        code, body = self.post(
            "/studio/compare", {"id": "t_alpha", "take": 0.5, "bitrate": 64}
        )
        self.assertEqual(code, 200, body)
        self.assertIs(body["ok"], True)
        self.assertEqual(body["reference"], "wav")
        token = str(body["token"])
        self.assertTrue(token.startswith("t_alpha-"), token)
        rows = body["codecs"]
        self.assertEqual([r["codec"] for r in rows], ["wav", "mp3", "opus", "flac"])
        for row in rows:
            self.assertEqual(row["url"], f"/api/compare/{token}/{row['codec']}")
            self.assertGreater(row["bytes"], 0, row)
            self.assertGreaterEqual(row["db"], 0.0, row)
        # The lossless pair scores a flat 0 dB against itself; the lossy
        # ones cost something. That ordering is the whole point of the
        # panel, so it is asserted rather than merely printed.
        by_codec = {str(r["codec"]): r for r in rows}
        self.assertEqual(by_codec["wav"]["db"], 0.0)
        self.assertEqual(by_codec["flac"]["db"], 0.0)
        self.assertGreater(by_codec["mp3"]["db"], 0.0)
        # The encode behind the row is servable, and it is that many bytes.
        code, hdrs, raw = self.req(f"/studio/compare/{token}/mp3")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("content-type"), "audio/mpeg")
        self.assertEqual(len(raw), by_codec["mp3"]["bytes"])

    def test_04_an_unknown_id_is_a_404(self) -> None:
        code, body = self.post("/studio/compare", {"id": "zzz"})
        self.assertEqual((code, body), (404, {"ok": False, "error": "no such track"}))
        code, body = self.json("/studio/compare/nosuchtoken/mp3")
        self.assertEqual((code, body), (404, {"error": "no such comparison"}))

    def test_05_a_typo_in_a_number_is_a_400_not_a_500(self) -> None:
        """Grade report 2026-08-31 A5: these came back as a 500 with a
        traceback, which read to the desk as "the studio is broken"."""
        for bad in (
            {"start": "abc"},
            {"bitrate": "high"},
            {"take": "soon"},
            {"channels": "two"},
        ):
            code, body = self.post("/studio/compare", {"id": "t_alpha", **bad})
            self.assertEqual(code, 400, (bad, body))
            self.assertIs(body["ok"], False, bad)
            self.assertIn("ValueError", str(body["error"]), bad)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class ServerOps(StudioCase):
    """Its own server, because both tests here end it."""

    def test_01_restart_answers_then_comes_back(self) -> None:
        # Three rounds: a restart races its own dying sockets for the
        # port, and the failure mode is a process that is simply gone
        # (grade report 2026-08-31 A1). Once was a coin flip.
        for round_ in range(3):
            code, body = self.json("/studio/server/restart", "POST")
            self.assertEqual((code, body), (200, {"ok": True, "restarting": True}))
            time.sleep(1.0)
            wait_up(self.port)
            code, body = self.json("/studio/tracks")
            self.assertEqual(code, 200, round_)
            # The same sandbox, re-read: an execv that lost its
            # environment would come back serving the real library.
            self.assertEqual(
                [t["id"] for t in body["tracks"]],
                ["t_alpha", "t_beta", "t_del", "t_empty", "t_meta"],
                round_,
            )

    def test_02_stop_answers_then_the_process_dies(self) -> None:
        code, body = self.json("/studio/server/stop", "POST")
        self.assertEqual((code, body), (200, {"ok": True, "stopping": True}))
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try:
                fetch(self.port, "/api/status")
                time.sleep(0.1)
            except (urllib.error.URLError, OSError):
                break
        else:
            self.fail(f"server on {self.port} never stopped")
        self.assertIsNotNone(self.procs[0].wait(timeout=10))


if __name__ == "__main__":
    unittest.main()
