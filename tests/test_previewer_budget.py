"""Tests for the built page's weight budget (tools/previewer_budget.py).

Split out of test_gen_previewer.py with the code it covers: the budget, the
un-inlining that keeps the page under it, and the build that stops when even
an un-inlined page is over.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import gen_previewer as gp
import previewer_budget as pgb
import yaml
from test_gen_previewer import scene


class TestBudgetComplaint(unittest.TestCase):
    """The ceiling on the built page is a ceiling, not a suggestion.

    It was a warning through two audits, and both times it was crossed the
    constant moved (3 -> 4 MB) instead of the page. `enforce_budget` is the
    version that can actually stop a build (grade report G2) — and since
    `fit_budget` un-inlines first, reaching it means the markup and the
    bundle alone are over, which no scene can be blamed for.
    """

    AUDIO: ClassVar[dict[str, str]] = {
        "vigil": "d" * 400_000,
        "ballad": "d" * 2_000_000,
    }

    def setUp(self) -> None:
        self._budget = pgb.PAGE_BUDGET_KB

    def tearDown(self) -> None:
        pgb.PAGE_BUDGET_KB = self._budget

    def test_a_page_within_budget_has_no_complaint(self) -> None:
        pgb.PAGE_BUDGET_KB = 4 * 1024
        self.assertIsNone(pgb.enforce_budget(b"x" * 1024, self.AUDIO, gp.HTML))

    def test_the_budget_is_the_edge_not_a_range(self) -> None:
        """Exactly at the ceiling passes; one KB past it does not."""
        pgb.PAGE_BUDGET_KB = 1
        self.assertIsNone(pgb.enforce_budget(b"x" * 2047, self.AUDIO, gp.HTML))
        self.assertIsNotNone(pgb.enforce_budget(b"x" * 2048, self.AUDIO, gp.HTML))

    def test_an_over_budget_page_names_the_size_the_ceiling_and_the_scenes(
        self,
    ) -> None:
        pgb.PAGE_BUDGET_KB = 1024
        got = pgb.enforce_budget(b"x" * 3_000_000, self.AUDIO, gp.HTML)
        assert got is not None
        self.assertIn("FAILED", got)
        self.assertIn("2.86 MB", got)  # the honest size, not a rounded "3 MB"
        self.assertIn("1 MB ceiling", got)
        # The heaviest scene first: "the page is too big" is not actionable.
        self.assertIn("ballad", got)
        self.assertLess(got.index("ballad"), got.index("vigil"))

    def test_the_complaint_says_the_last_good_page_was_kept(self) -> None:
        """The operator's first question is whether the tree still has a page."""
        pgb.PAGE_BUDGET_KB = 0
        got = pgb.enforce_budget(b"x" * 4096, self.AUDIO, gp.HTML)
        assert got is not None
        self.assertIn("Nothing was written", got)


class TestFitBudget(unittest.TestCase):
    """The page stops growing with the show (grade report G2).

    Before `fit_budget`, one more song scene meant one more megabyte and a
    raised constant. Now the overflow scenes swap their data URI for the
    `/studio/scene-audio/<id>` link the lean rewrite already emits, and the
    build says which ones went that way.
    """

    #: A show in order: each scene's "audio" is its own size in bytes.
    AUDIO: ClassVar[dict[str, str]] = {
        "vigil": "a" * 100,
        "storm": "b" * 100,
        "ballad": "c" * 100,
    }

    @staticmethod
    def page(src: dict[str, str]) -> bytes:
        """A stand-in build: 20 bytes of markup plus whatever it was given."""
        return b"x" * 20 + "".join(src.values()).encode()

    def test_a_page_that_already_fits_keeps_every_scene_inlined(self) -> None:
        body, linked = pgb.fit_budget(self.page, self.AUDIO, 10_000)
        self.assertEqual(linked, [])
        self.assertEqual(len(body), 320)

    def test_the_overflow_is_given_up_from_the_back_of_the_show(self) -> None:
        """Scenes an operator reaches first are the ones that stay portable."""
        body, linked = pgb.fit_budget(self.page, self.AUDIO, 250)
        self.assertEqual(linked, ["ballad"])
        self.assertLessEqual(len(body), 250)
        self.assertIn(b"a" * 100, body)
        self.assertIn(b"/studio/scene-audio/ballad", body)
        self.assertNotIn(b"c" * 100, body)

    def test_linked_scenes_come_back_in_show_order(self) -> None:
        _, linked = pgb.fit_budget(self.page, self.AUDIO, 200)
        self.assertEqual(linked, ["storm", "ballad"])

    def test_an_impossible_budget_gives_up_everything_and_stops_there(self) -> None:
        """Nothing left to un-inline is enforce_budget's job, not this one."""
        body, linked = pgb.fit_budget(self.page, self.AUDIO, 1)
        self.assertEqual(linked, ["vigil", "storm", "ballad"])
        self.assertGreater(len(body), 1)


class TestBuildFitsTheBudget(unittest.TestCase):
    """main() end to end on a scratch show: over budget, the audio goes to
    links — and when even that is not enough the build fails AND leaves the
    previous page alone."""

    TEMPLATE = (
        "<style>/* @STYLES */</style>\n"
        "<script>\n"
        "  // @GEN-DATA-START\n"
        "  // @GEN-DATA-END\n"
        "</script>\n"
        "<script>/* @BUNDLE */</script>\n"
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (
            gp.SRC,
            gp.MARKERS_FILE,
            gp.TEMPLATE,
            gp.HTML,
            gp.AUDIO,
            gp.STYLES,
            gp.PANELS,
            gp.MOBILE,
            gp.WEB,
            gp.BUNDLE,
            gp.subprocess,
            pgb.PAGE_BUDGET_KB,
        )
        gp.SRC = self.tmp / "scenes.yaml"
        gp.SRC.write_text(yaml.safe_dump({"scenes": [scene()]}))
        gp.MARKERS_FILE = self.tmp / "markers.json"
        gp.TEMPLATE = self.tmp / "template.html"
        gp.TEMPLATE.write_text(self.TEMPLATE)
        gp.HTML = self.tmp / "out" / "castle-cue-desk.html"
        gp.HTML.parent.mkdir()
        gp.HTML.write_text("the last good build")
        gp.AUDIO = self.tmp / "audio"
        gp.AUDIO.mkdir()
        (gp.AUDIO / "01_probe.mp3").write_bytes(b"\xff\xfb" + b"m" * 40_000)
        for name in ("STYLES", "PANELS", "MOBILE"):
            p = self.tmp / f"{name.lower()}.css"
            p.write_text("/* css */\n")
            setattr(gp, name, p)
        gp.WEB = self.tmp / "web"
        (gp.WEB / "node_modules").mkdir(parents=True)
        gp.BUNDLE = gp.WEB / "dist" / "bundle.js"
        gp.BUNDLE.parent.mkdir(parents=True)

        def run(*_a: object, **_k: object) -> types.SimpleNamespace:
            gp.BUNDLE.write_text("console.log(1);")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        gp.subprocess = types.SimpleNamespace(run=run)  # type: ignore[assignment]  # test double

    def tearDown(self) -> None:
        (
            gp.SRC,
            gp.MARKERS_FILE,
            gp.TEMPLATE,
            gp.HTML,
            gp.AUDIO,
            gp.STYLES,
            gp.PANELS,
            gp.MOBILE,
            gp.WEB,
            gp.BUNDLE,
            gp.subprocess,
            pgb.PAGE_BUDGET_KB,
        ) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_page_within_budget_is_written_and_the_build_succeeds(self) -> None:
        pgb.PAGE_BUDGET_KB = 4 * 1024
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gp.main(), 0)
        self.assertIn("data:audio/mpeg;base64,", gp.HTML.read_text())

    def test_over_budget_links_the_audio_instead_of_inlining_it(self) -> None:
        pgb.PAGE_BUDGET_KB = 1  # the 40 KB mp3 alone blows a 1 KB ceiling
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gp.main(), 0)
        page = gp.HTML.read_text()
        self.assertNotIn("data:audio/mpeg;base64,", page)
        self.assertIn("/studio/scene-audio/probe", page)
        # And it SAYS so — a silently un-inlined scene is its own surprise.
        self.assertIn("probe", out.getvalue())
        self.assertIn("live synth", out.getvalue())

    def test_a_page_over_budget_with_nothing_inlined_still_fails(self) -> None:
        """Past un-inlining the weight is markup and bundle; stop the build."""
        pgb.PAGE_BUDGET_KB = 1  # 1 KB, and the bundle alone is 2 KB

        def fat(*_a: object, **_k: object) -> types.SimpleNamespace:
            gp.BUNDLE.write_text("// " + "b" * 2048)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        gp.subprocess = types.SimpleNamespace(run=fat)  # type: ignore[assignment]  # test double
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(gp.main(), 1)
        self.assertIn("page budget FAILED", err.getvalue())
        self.assertEqual(gp.HTML.read_text(), "the last good build")


class TestPageWeight(unittest.TestCase):
    """G1's growth, made visible the moment it happens (grade report D3)."""

    # The built page is 3.31 MB at ten scenes, ~89% inlined audio, growing
    # ~1.2 MB per song scene. If a new scene pushes it over, raising this
    # number must be a deliberate act in the same commit — the alternative
    # is A5/G1: serve the lean page from the device and stop inlining.
    BUDGET = 3_600_000

    def test_built_page_stays_under_its_byte_budget(self) -> None:
        # The page is generated and gitignored, so it is simply absent in
        # any checkout that has not run `make preview` — CI's unit job
        # included, which errored here on every run rather than measuring
        # anything (grade report D1). The job that DOES build the page
        # (workflows/ci.yml, `web`) runs this test by name right after.
        page = ROOT / "previewer" / "castle-cue-desk.html"
        if not page.exists():
            self.skipTest("previewer/castle-cue-desk.html not built (make preview)")
        size = page.stat().st_size
        self.assertLessEqual(
            size,
            self.BUDGET,
            f"previewer/castle-cue-desk.html is {size:,} bytes, over the "
            f"{self.BUDGET:,}-byte budget. Each song scene adds ~1.2 MB of "
            "inlined audio (grade report G1/D3). If the growth is deliberate, "
            "raise BUDGET here in the same commit that adds the scene.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
