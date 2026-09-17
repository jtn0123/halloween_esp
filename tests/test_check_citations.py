"""The citation guard — `tools/check_citations.py` and what it refuses.

Item IDs renumber every audit, so a bare "grade report A1" names six reports
at once. The guard's whole job is to keep the next undated one out of the
tree, which means two things have to hold: it must PASS on the tree as it
stands (proved here against the live repo), and it must FAIL on a planted
bare citation, including the wrapped shapes real comments take.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_citations


class TestDetection(unittest.TestCase):
    """What counts as an undated citation, one shape per test."""

    def dated(self, text: str) -> list[bool]:
        return [d for _line, d, _x in check_citations.scan_text(text)]

    def test_a_dated_citation_passes(self) -> None:
        self.assertEqual(
            self.dated("# closed the seam (grade report 2026-08-31 B1)."), [True]
        )

    def test_a_bare_item_id_fails(self) -> None:
        self.assertEqual(self.dated("# closed the seam (grade report B1)."), [False])

    def test_a_citation_with_no_id_at_all_still_needs_its_date(self) -> None:
        self.assertEqual(self.dated("// as the grade report said"), [False])

    def test_a_wrapped_citation_is_read_whole(self) -> None:
        """A comment that runs long puts a line break and a leader between
        the two words, or between the words and the date. Both are still one
        citation, and neither may be counted twice."""
        self.assertEqual(self.dated("# ...(grade\n# report 2026-08-23 I5)"), [True])
        self.assertEqual(self.dated(" * (grade report\n * 2026-09-01 C3)"), [True])
        self.assertEqual(self.dated("# ...(grade\n# report I5)"), [False])

    def test_the_plural_is_prose_about_the_files_not_a_citation(self) -> None:
        self.assertEqual(self.dated("# the grade reports are audit output"), [])

    def test_the_path_form_is_not_a_citation(self) -> None:
        self.assertEqual(self.dated('AUDIT = {".claude/grade-report*.md"}'), [])

    def test_the_date_must_follow_the_words_immediately(self) -> None:
        """ "2026-08-23's grade report" reads fine and greps badly. One shape,
        so a search for an audit's citations finds all of them."""
        self.assertEqual(self.dated("# the 2026-08-23 grade report's G2"), [False])


class TestTree(unittest.TestCase):
    def test_the_repo_has_no_undated_citations(self) -> None:
        bare = [
            (rel, line) for rel, line, dated, _x in check_citations.scan() if not dated
        ]
        self.assertEqual(bare, [], "undated citations: run tools/check_citations.py")

    def test_a_planted_citation_is_caught(self) -> None:
        """The guard against the guard: scan() over a file that says it
        bare must report it."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "planted.py").write_text("# the fix for grade report A1\n")
            rows = check_citations.scan(root=root, files=[root / "planted.py"])
        self.assertEqual([(r[0], r[2]) for r in rows], [("planted.py", False)])

    def test_main_reports_the_pass_and_names_the_fix_on_failure(self) -> None:
        for rows, code, want in (
            ([], 0, "PASS"),
            ([("planted.py", 1, False, "grade report A1")], 1, "YYYY-MM-DD"),
        ):
            with mock.patch.object(check_citations, "scan", return_value=rows):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    got = check_citations.main([])
            self.assertEqual(got, code)
            self.assertIn(want, out.getvalue())


class TestExemptions(unittest.TestCase):
    def test_the_reports_themselves_are_out_of_scope(self) -> None:
        """They ARE the item lists — dating every ID inside one would be
        dating it against itself."""
        scanned = {rel for rel, _l, _d, _x in check_citations.scan()}
        self.assertFalse([r for r in scanned if r.startswith(".claude/grade-report")])

    def test_every_exemption_names_a_real_file_and_a_reason(self) -> None:
        for rel, why in check_citations.EXEMPT.items():
            self.assertTrue((ROOT / rel).exists(), rel)
            self.assertTrue(why.strip(), rel)


if __name__ == "__main__":
    unittest.main()
