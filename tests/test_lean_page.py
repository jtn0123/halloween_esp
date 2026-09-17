"""The lean page: the desk the studio serves, with its inlined audio replaced
by /studio/scene-audio/<id> links.

`make preview` still writes the portable single file (data URIs for every
rendered scene) — that is what the committed page, the artifact and a copy
on the card need. The studio rewrites it at serve time because a phone on
the LAN should not download 1.9 MB of base64 for scenes it may never play.
What is asserted here is the REWRITE itself — `gen_previewer.lean` and
`scene_audio`, which are Python and stay Python. The serving half (the
route, the Range, the ETag, and the studio's agreement with itself about
which audio directory it is serving from) moved to
`tests/test_studio_reads_rs.py` and `core/src/studio.rs` when the Python
studio retired (docs/RETIREMENT.md).
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import gen_previewer as gp

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


if __name__ == "__main__":
    unittest.main()
