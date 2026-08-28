"""The publish path, desk to card — the seam that failed on 08-22 (D2/A1).

A scene added through the studio produced three correct local artifacts and
a castle that had never heard of it; nothing asserted the two ends agree.
These tests drive studio_publish against the EMULATOR (a byte-level port of
the firmware), so the assertion is about real HTTP against the real wire
shapes: the rendered track lands in /sd/scenes/, the lean page in /sd/site/,
and a scene the firmware was not built with is REPORTED, not swallowed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import castle_link as cl
import helpers  # noqa: F401  (hermetic env)
import studio_publish as sp


def runner_via_sd_sync(host: str):
    """A studio_scenes-style Runner that really executes sd_sync in-process
    (importing it fresh would re-read env), capturing its stdout."""
    import contextlib
    import io

    import sd_sync

    def run(args: list[str]) -> tuple[bool, str]:
        # args = [python, .../sd_sync.py, host, cmd]
        cmd = args[-1]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = {"scenes": sd_sync.cmd_scenes, "site": sd_sync.cmd_site}[cmd](
                    host
                )
            return code == 0, out.getvalue()
        except SystemExit as e:
            return False, out.getvalue() + f"\n{e}"

    return run


class TestPublish(unittest.TestCase):
    def setUp(self) -> None:
        self.card = TemporaryDirectory(prefix="publish-card-")
        self.addCleanup(self.card.cleanup)
        # The emulator's build knows vigil only — the second scene below is
        # "newer than the firmware", exactly the 08-22 shape.
        self.emu = castle_emu.CastleEmu(
            port=0, sd_dir=Path(self.card.name), scenes=["vigil"]
        )
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.host = f"127.0.0.1:{self.emu.port}"

        self.repo = TemporaryDirectory(prefix="publish-repo-")
        self.addCleanup(self.repo.cleanup)
        root = Path(self.repo.name)
        (root / "audio").mkdir()
        (root / "audio" / "01_vigil.mp3").write_bytes(b"\xff\xfbVIGIL" * 40)
        (root / "audio" / "02_wisp.mp3").write_bytes(b"\xff\xfbWISP" * 40)
        (root / "previewer").mkdir()
        (root / "previewer" / "castle-cue-desk.html").write_text(
            '<html>"vigil": "data:audio/mpeg;base64,AA"</html>'
        )
        scenes = root / "scenes.yaml"
        scenes.write_text("scenes:\n  - id: vigil\n  - id: wisp\n")

        env = mock.patch.dict(
            "os.environ", {"CASTLE_HOST": self.host, "CASTLE_SCENES": str(scenes)}
        )
        env.start()
        self.addCleanup(env.stop)
        cl._cache.clear()
        self.addCleanup(cl._cache.clear)

        import sd_sync

        rootpatch = mock.patch.object(sd_sync, "ROOT", root)
        rootpatch.start()
        self.addCleanup(rootpatch.stop)
        # studio_publish reads scene ids through build_paths.SCENES, and
        # sd_sync reads its artefacts through build_paths too — the fake
        # repo must be the build root, or the REAL repo's renders leak in.
        import build_paths as bp

        for attr, val in (
            ("SCENES", scenes),
            ("AUDIO", root / "audio"),
            ("PREVIEW_HTML", root / "previewer" / "castle-cue-desk.html"),
        ):
            sc = mock.patch.object(bp, attr, val)
            sc.start()
            self.addCleanup(sc.stop)

    def test_publish_lands_tracks_and_page_and_reports_the_stale_scene(self) -> None:
        body, code = sp.publish(runner_via_sd_sync(self.host))
        self.assertEqual(code, 200, body)
        card = Path(self.card.name)
        self.assertTrue((card / "scenes" / "01_vigil.mp3").exists())
        self.assertTrue((card / "scenes" / "02_wisp.mp3").exists())
        self.assertTrue((card / "site" / "index.html.gz").exists())
        self.assertIn(
            '"vigil": "/site/vigil.mp3"', (card / "site" / "index.html").read_text()
        )
        # The half a push cannot fix is REPORTED (the 08-22 silence):
        self.assertEqual(body["needs_firmware"], ["wisp"])
        self.assertIn("OTA", body["note"])

    def test_no_castle_publishes_nothing_and_says_so(self) -> None:
        with mock.patch.dict("os.environ", {"CASTLE_HOST": ""}):
            cl._cache.clear()
            body, code = sp.publish(runner_via_sd_sync(""))
        self.assertEqual(code, 502)
        self.assertFalse(body["pushed"])
        self.assertIn("no castle", body["error"])

    def test_a_second_publish_skips_the_unchanged_tracks(self) -> None:
        run = runner_via_sd_sync(self.host)
        sp.publish(run)
        body, code = sp.publish(run)
        self.assertEqual(code, 200, body)
        self.assertIn("unchanged, skipped", body["log"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
