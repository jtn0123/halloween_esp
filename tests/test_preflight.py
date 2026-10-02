"""`make setup` → `make check` on a clean box: the lock, the numpy pin and
the preflight (grade report 2026-09-24 I1).

That run ended in 19 failures and an error, from four gaps: the venv came
from the loose requirement files rather than the hashed lock, `make check`
never set CI's numpy dispatch pin, and nothing said that `lame` or
`web/node_modules` was missing. These hold each closed — the Makefile's
install and pin to CI's own spelling, and tools/preflight.py to naming every
gap with its fix and stopping `check` before the gap can.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import preflight

MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
WORKFLOWS = ROOT / ".github" / "workflows"


def row(name: str, wrong: str | None, required: bool = True) -> preflight.Row:
    return preflight.Row(
        name,
        f"{name} is needed",
        lambda: wrong,
        {"darwin": f"brew install {name}", "": f"apt-get install {name}"},
        required,
    )


def run_main(rows: tuple[preflight.Row, ...], *argv: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = preflight.main(list(argv), rows)
    return code, err.getvalue()


class ReportTests(unittest.TestCase):
    def test_every_missing_tool_is_named_with_its_fix_at_once(self) -> None:
        rows = (row("lame", "not on PATH"), row("ffmpeg", None), row("x", "gone"))
        lines, short = preflight.report(rows, "linux")
        self.assertEqual(short, 2)
        self.assertEqual(len(lines), 2)
        self.assertIn("missing: lame (not on PATH) — lame is needed", lines[0])
        self.assertTrue(lines[0].endswith("Fix: apt-get install lame"))
        self.assertIn("Fix: apt-get install x", lines[1])

    def test_the_fix_is_this_platforms(self) -> None:
        r = row("lame", "not on PATH")
        self.assertEqual(r.fix_for("darwin"), "brew install lame")
        self.assertEqual(r.fix_for("linux"), "apt-get install lame")
        self.assertEqual(r.fix_for("freebsd"), "apt-get install lame")

    def test_a_missing_required_tool_stops_check(self) -> None:
        code, err = run_main((row("lame", "not on PATH"),))
        self.assertEqual(code, 1)
        self.assertIn("preflight: missing: lame", err)
        self.assertIn("1 required tool(s) missing", err)

    def test_warn_names_it_and_lets_setup_finish(self) -> None:
        code, err = run_main((row("lame", "not on PATH"),), "--warn")
        self.assertEqual(code, 0)
        self.assertIn("missing: lame", err)

    def test_an_optional_tool_is_a_note_and_never_a_failure(self) -> None:
        code, err = run_main((row("cargo", "not on PATH", required=False),))
        self.assertEqual(code, 0)
        self.assertIn("note: optional: cargo", err)

    def test_a_complete_machine_prints_nothing(self) -> None:
        self.assertEqual(run_main((row("lame", None),)), (0, ""))


class RealRowTests(unittest.TestCase):
    def test_the_rows_cover_what_the_clean_box_lacked(self) -> None:
        names = {r.name for r in preflight.ROWS if r.required}
        self.assertLessEqual(
            {"lame", "ffmpeg", "node", "web/node_modules", "yt-dlp"}, names
        )
        for r in preflight.ROWS:
            with self.subTest(row=r.name):
                self.assertTrue(r.fix_for("linux"))
                self.assertTrue(r.fix_for("darwin"))

    def test_every_probe_answers_on_this_machine(self) -> None:
        for r in preflight.ROWS:
            with self.subTest(row=r.name):
                self.assertIn(type(r.probe()), (str, type(None)))

    def test_node_older_than_nvmrc_is_named(self) -> None:
        old = mock.Mock(stdout="v18.19.1\n")
        with (
            mock.patch.object(preflight.shutil, "which", return_value="/bin/node"),
            mock.patch.object(preflight.subprocess, "run", return_value=old),
        ):
            got = preflight.node_probe()
        self.assertEqual(got, f"v18.19.1 is older than {preflight.NODE_MIN}")
        with mock.patch.object(preflight.shutil, "which", return_value=None):
            self.assertEqual(preflight.node_probe(), "not on PATH")

    def test_ytdlp_is_looked_for_where_the_importer_looks(self) -> None:
        with mock.patch.object(preflight.exe_paths, "ytdlp", return_value=None):
            self.assertIsNotNone(preflight.ytdlp_probe())
        with mock.patch.object(preflight.exe_paths, "ytdlp", return_value="/v/yt"):
            self.assertIsNone(preflight.ytdlp_probe())


def recipe(target: str) -> list[str]:
    lines = MAKEFILE.splitlines()
    start = next(i for i, x in enumerate(lines) if x.startswith(f"{target}:"))
    out = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        out.append(line.strip())
    return out


class MakefileTests(unittest.TestCase):
    def test_setup_installs_the_lock_with_cis_own_flags(self) -> None:
        self.assertIn(".venv/bin/pip install --quiet $(PIP_LOCKED)", recipe("setup"))
        ours = re.search(r"^PIP_LOCKED := (.+)$", MAKEFILE, re.MULTILINE)
        assert ours is not None
        ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        theirs = re.findall(
            r"- run: pip install (--require-hashes .+)$", ci, re.MULTILINE
        )
        self.assertTrue(theirs, "ci.yml no longer installs the lock")
        self.assertEqual(set(theirs), {ours.group(1)})

    def test_setup_no_longer_resolves_the_loose_files(self) -> None:
        self.assertFalse([x for x in recipe("setup") if "requirements.txt" in x])

    def test_setup_ends_in_the_preflight_and_check_starts_with_it(self) -> None:
        self.assertIn("@.venv/bin/python tools/preflight.py --warn", recipe("setup"))
        self.assertIn("@$(PY) tools/preflight.py", recipe("preflight"))
        check = re.search(r"^check: (.+)$", MAKEFILE, re.MULTILINE)
        assert check is not None
        self.assertEqual(check.group(1).split()[0], "preflight")

    def test_the_numpy_pin_is_cis_on_x86_64(self) -> None:
        ours = re.search(
            r"^ifeq \(\$\(shell uname -m\),x86_64\)\n"
            r"export NPY_DISABLE_CPU_FEATURES \?= (.+)\nendif$",
            MAKEFILE,
            re.MULTILINE,
        )
        assert ours is not None, "the Makefile no longer exports the numpy pin"
        for wf in ("ci.yml", "sonar.yml", "cross-platform.yml"):
            text = (WORKFLOWS / wf).read_text(encoding="utf-8")
            theirs = re.findall(r'NPY_DISABLE_CPU_FEATURES[:=] ?"?([A-Z0-9_ ]+)', text)
            with self.subTest(workflow=wf):
                self.assertEqual({t.strip() for t in theirs}, {ours.group(1)})


if __name__ == "__main__":
    unittest.main()
