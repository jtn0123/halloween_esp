"""The lean page: the desk the studio serves, with its inlined audio replaced
by /studio/scene-audio/<id> links.

`make preview` still writes the portable single file (data URIs for every
rendered scene) — that is what the committed page, the artifact and a copy
on the card need. The studio rewrites it at serve time because a phone on
the LAN should not download 1.9 MB of base64 for scenes it may never play.
What is asserted: the rewrite itself, that the route answers with the file
the page was built from (Range honoured), and that the two never disagree
about WHICH audio directory that is.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import gen_previewer as gp
import studio
from studio_case import ServerCase

MP3 = b"\xff\xfb\x90\x00" + bytes(range(256)) * 4  # 1028 fake mp3 bytes


def page_with(audio: dict[str, str]) -> str:
    """A page shaped like gen_previewer's output around the GEN block."""
    gen = {
        "scenes": [
            {"id": k, "file": f"0{i}_{k}.mp3", "yaml": "a: b"}
            for i, k in enumerate(audio, start=1)
        ],
        "audio": audio,
    }
    return (
        "<html><script>\n  // @GEN-DATA-START\n"
        f"  window.CASTLE_GEN = {json.dumps(gen)};\n"
        "  // @GEN-DATA-END\n</script></html>"
    )


class TestLeanRewrite(unittest.TestCase):
    def test_every_data_uri_becomes_its_route(self) -> None:
        b64 = base64.b64encode(MP3).decode()
        html = page_with(
            {
                "vigil": f"data:audio/mpeg;base64,{b64}",
                "storm_2": f"data:audio/mpeg;base64,{b64}",
            }
        )
        lean = gp.lean(html)
        self.assertNotIn("data:audio/mpeg", lean)
        self.assertIn('"vigil": "/studio/scene-audio/vigil"', lean)
        self.assertIn('"storm_2": "/studio/scene-audio/storm_2"', lean)
        # Everything else is untouched — the scenes block, the markers.
        self.assertIn('"file": "01_vigil.mp3"', lean)
        self.assertIn("// @GEN-DATA-END", lean)
        self.assertLess(len(lean), len(html) // 2)

    def test_a_page_without_audio_is_unchanged(self) -> None:
        html = page_with({})
        self.assertEqual(gp.lean(html), html)

    def test_scene_audio_resolves_by_id_only(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="castle-lean-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "03_crypt.mp3").write_bytes(MP3)
        self.assertEqual(gp.scene_audio(tmp, "crypt"), tmp / "03_crypt.mp3")
        self.assertIsNone(gp.scene_audio(tmp, "vigil"))
        self.assertIsNone(gp.scene_audio(tmp, "03_crypt.mp3"))  # the id, not the file
        self.assertIsNone(gp.scene_audio(tmp, "../03_crypt"))
        self.assertIsNone(gp.scene_audio(tmp, "*"))
        self.assertIsNone(gp.scene_audio(tmp, ""))

    def test_lean_page_is_cached_by_mtime_and_size(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="castle-lean-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        page = tmp / "desk.html"
        page.write_text(page_with({"a": "data:audio/mpeg;base64,AAAA"}))
        body1, etag1 = gp.lean_page(page)
        with mock.patch.object(gp, "lean", side_effect=AssertionError("re-ran")):
            body2, etag2 = gp.lean_page(page)
        self.assertEqual((body1, etag1), (body2, etag2))
        self.assertIn(b"/studio/scene-audio/a", body1)
        self.assertTrue(etag1.endswith('-lean"'))
        # A rewritten page (new mtime/size) is rewritten again.
        page.write_text(page_with({"b": "data:audio/mpeg;base64,AAAA"}))
        os.utime(
            page, ns=(page.stat().st_atime_ns, page.stat().st_mtime_ns + 10_000_000)
        )
        body3, etag3 = gp.lean_page(page)
        self.assertIn(b"/studio/scene-audio/b", body3)
        self.assertNotEqual(etag1, etag3)


class TestServedLean(ServerCase):
    """The route pair over HTTP, against a build of its own."""

    build: Path
    _served: "mock._patch[Any]"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.build = Path(tempfile.mkdtemp(prefix="castle-lean-srv-"))
        audio = cls.build / "audio"
        audio.mkdir()
        (audio / "01_vigil.mp3").write_bytes(MP3)
        page = cls.build / "castle-cue-desk.html"
        b64 = base64.b64encode(MP3).decode()
        page.write_text(
            page_with(
                {
                    "vigil": f"data:audio/mpeg;base64,{b64}",
                    "ghost": f"data:audio/mpeg;base64,{b64}",
                }
            )
        )
        cls._served = mock.patch.object(studio, "served", return_value=(page, audio))
        cls._served.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._served.stop()
        shutil.rmtree(cls.build, ignore_errors=True)
        super().tearDownClass()

    def test_root_serves_the_lean_page(self) -> None:
        code, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertNotIn(b"data:audio/mpeg", body)
        self.assertIn(b'"vigil": "/studio/scene-audio/vigil"', body)
        self.assertIn(b'"ghost": "/studio/scene-audio/ghost"', body)

    def test_scene_audio_streams_the_file_with_ranges(self) -> None:
        code, body = self.req("GET", "/studio/scene-audio/vigil")
        self.assertEqual(code, 200)
        self.assertEqual(body, MP3)
        code, body = self.req(
            "GET", "/studio/scene-audio/vigil", headers={"Range": "bytes=4-7"}
        )
        self.assertEqual(code, 206)
        self.assertEqual(body, MP3[4:8])

    def test_missing_and_hostile_ids_are_404(self) -> None:
        # In the page but not rendered: 404, like any other missing file.
        self.assertEqual(self.req("GET", "/studio/scene-audio/ghost")[0], 404)
        self.assertEqual(self.req("GET", "/studio/scene-audio/01_vigil.mp3")[0], 404)
        self.assertEqual(self.req("GET", "/studio/scene-audio/..%2F01_vigil")[0], 404)
        self.assertEqual(self.req("GET", "/studio/scene-audio/")[0], 404)


class TestServedPair(unittest.TestCase):
    """served() hands out the page and the audio dir from the SAME build."""

    def test_repo_build_by_default(self) -> None:
        with mock.patch.object(studio.bp, "sandboxed", return_value=False):
            page, audio = studio.served()
        self.assertEqual(page, studio.HTML)
        self.assertEqual(audio, studio.ROOT / "audio")

    def test_sandbox_build_once_it_exists(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="castle-lean-sb-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sb_page = tmp / "previewer" / "castle-cue-desk.html"
        with (
            mock.patch.object(studio.bp, "sandboxed", return_value=True),
            mock.patch.object(studio.bp, "PREVIEW_HTML", sb_page),
            mock.patch.object(studio.bp, "AUDIO", tmp / "audio"),
        ):
            # Not built yet: the repo's page AND the repo's audio.
            self.assertEqual(studio.served(), (studio.HTML, studio.ROOT / "audio"))
            sb_page.parent.mkdir(parents=True)
            sb_page.write_text("<html></html>")
            self.assertEqual(studio.served(), (sb_page, tmp / "audio"))


if __name__ == "__main__":
    unittest.main()
