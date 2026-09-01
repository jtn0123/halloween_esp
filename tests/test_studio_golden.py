"""The Rust studio against the recorded answers — the parity that outlives
the Python one.

`tests/test_studio*_rust.py` compare two LIVE servers; when `tools/studio.py`
is retired off-season (after Halloween 2026) those comparisons have no second
side left. This suite has no second side to lose: it boots ONLY castle-core's
`studio` bin and holds it against `tests/golden/*.json`, captured from the
Python studio while that was still the reference
(`tools/gen_golden.py`). Nothing here imports or launches the Python studio,
and nothing here may start doing so — that is the entire point.

What it guards, in order of what it would cost to lose: the splice-refusal
strings (the desk's UX contract, and today the product of ONE Python
implementation the Rust studio reaches through `tools/scene_check.py` — these
goldens are the spec a native Rust validator would be written against), then
the castle-less read surface and the shapes of its 404s.

Skipped, not failed, without cargo — except in CI. The corpus, the sandbox
and the normalisation all live in golden_case.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import cargo_gate
import golden_case as gc
import golden_corpus as corpus
from check_loc import SCENE_LIMIT
from helpers import SANDBOX_ENV

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "studio"


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class GoldenReplay(unittest.TestCase):
    """One Rust studio, one sandbox, the corpus replayed once."""

    tmp: ClassVar[Path]
    proc: ClassVar[subprocess.Popen[bytes] | None]
    read: ClassVar[dict[str, Any]]
    scenes: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        built = cargo_gate.build()
        assert built.returncode == 0, built.stderr
        cls.tmp = Path(tempfile.mkdtemp(prefix="studio-golden-rs-"))
        cls.proc = None
        box = gc.Sandbox(cls.tmp)
        box.seed()
        # The operator's own exported knobs must not reach the child; the
        # Rust studio's own children (tools/scene_check.py) still need
        # CASTLE_PY, which is not one of the four (CLAUDE.md).
        env = {k: v for k, v in os.environ.items() if k not in SANDBOX_ENV}
        cls.proc, port = gc.launch([str(BIN)], box, env)
        cls.read = gc.capture_read(port)
        cls.scenes = gc.capture_scene_errors(port, box, SCENE_LIMIT)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.proc is not None:
            cls.proc.terminate()
            cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _compare(self, want: dict[str, Any], got: dict[str, Any], what: str) -> None:
        """Case by case, so a failure names the ONE route that moved rather
        than printing two dictionaries the size of the corpus."""
        self.assertEqual(
            sorted(want),
            sorted(got),
            f"{what}: the corpus and the goldens disagree about which cases "
            "exist — regenerate with `.venv/bin/python tools/gen_golden.py`",
        )
        for name in sorted(want):
            with self.subTest(case=name):
                self.assertEqual(want[name], got[name], f"{what}/{name} drifted")

    def test_read_routes_match_the_goldens(self) -> None:
        self._compare(gc.load(gc.READ_FILE), self.read, "read_routes")

    def test_scene_refusals_match_the_goldens(self) -> None:
        self._compare(gc.load(gc.SCENE_FILE), self.scenes, "scene_errors")

    def test_the_goldens_cover_the_whole_corpus(self) -> None:
        """A missing or truncated golden file must fail loudly rather than
        letting an empty tests/golden/ pass as agreement. `load` refuses an
        absent or empty file; this adds the count, so a golden that lost
        half its cases to a bad merge is caught too."""
        read = gc.load(gc.READ_FILE)
        scenes = gc.load(gc.SCENE_FILE)
        self.assertEqual(sorted(read), sorted(n for n, _m, _p in corpus.READ_CASES))
        want_scenes = [n for n, _b in corpus.SCENE_CASES] + [corpus.CEILING_CASE[0]]
        self.assertEqual(sorted(scenes), sorted(want_scenes))
        for group in (read, scenes):
            for name, rec in group.items():
                self.assertIn("status", rec, name)
                self.assertIn("body", rec, name)

    def test_the_ceiling_refusal_still_says_why(self) -> None:
        """The one golden worth asserting ABOUT rather than only diffing:
        the thirteenth scene is refused with the sentence that explains the
        board's dram0 ceiling and names the way forward (grade report
        2026-08-31 A8). A future validator that answers "invalid scene" here
        would still match nothing — but if this file is ever regenerated in
        haste, this test says what the sentence has to contain."""
        rec = gc.load(gc.SCENE_FILE)["scene_ceiling"]
        self.assertEqual(rec["status"], 400)
        msg = str(rec["body"]["error"])
        self.assertIn("the show is full", msg)
        self.assertIn(str(SCENE_LIMIT), msg)
        self.assertIn("card-loaded format", msg)
        self.assertEqual(rec, self.scenes["scene_ceiling"])


if __name__ == "__main__":
    unittest.main()
