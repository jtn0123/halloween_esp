"""tools/run_checks.py — the one spelling of `make test` / `make test-radio`.

The cross-platform CI job runs it on a Windows runner with no make, so these
pin the two things that would let that job quietly test less than a Mac:
the Makefile must delegate to it rather than keep a second copy, and every
file it names must exist.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_checks


def _done(code: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=code)


class PlanTests(unittest.TestCase):
    def test_test_implies_pycheck_once_and_first(self) -> None:
        self.assertEqual(
            run_checks.plan(["test", "test-radio", "pycheck"]),
            ["pycheck", "test", "test-radio"],
        )

    def test_radio_alone_has_no_prerequisite(self) -> None:
        self.assertEqual(run_checks.plan(["test-radio"]), ["test-radio"])

    def test_unknown_step_is_refused_before_anything_runs(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(subprocess, "run", return_value=_done(0)) as spawn,
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(run_checks.main(["test", "lint"]), 2)
        self.assertIn("unknown step 'lint'", err.getvalue())
        # `test` ran first; the refusal is about the step it reached.
        self.assertEqual(spawn.call_count, 1)

    def test_no_steps_prints_usage(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(run_checks.main([]), 2)
        self.assertIn("STEP is one of", err.getvalue())


class CommandTests(unittest.TestCase):
    def test_suites_run_under_this_interpreter(self) -> None:
        (unit,) = run_checks.commands("test")
        self.assertEqual(unit[:4], [sys.executable, "-m", "unittest", "discover"])
        self.assertIn("tests", unit)

    def test_every_radio_node_file_exists(self) -> None:
        with mock.patch.object(shutil, "which", return_value="node"):
            _unit, node = run_checks.commands("test-radio")
        self.assertEqual(node[:2], ["node", "--test"])
        for rel in node[2:]:
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_radio_without_node_says_so(self) -> None:
        with (
            mock.patch.object(shutil, "which", return_value=None),
            self.assertRaisesRegex(run_checks.StepError, "node is not on PATH"),
        ):
            run_checks.commands("test-radio")

    def test_print_runs_nothing(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.object(subprocess, "run") as spawn,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(run_checks.main(["--print", "test"]), 0)
        spawn.assert_not_called()
        self.assertIn("unittest discover -s tests", out.getvalue())


class RunTests(unittest.TestCase):
    def test_first_failure_stops_the_run_with_its_code(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(shutil, "which", return_value="node"),
            mock.patch.object(
                subprocess, "run", side_effect=[_done(0), _done(3)]
            ) as spawn,
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(run_checks.run(["test", "test-radio"]), 3)
        self.assertEqual(spawn.call_count, 2)  # test, then radio's unit half
        self.assertIn("test-radio failed (exit 3)", err.getvalue())
        self.assertEqual(spawn.call_args.kwargs["cwd"], run_checks.ROOT)

    def test_old_interpreter_is_told_and_nothing_runs(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(sys, "version_info", (3, 11, 9)),
            mock.patch.object(subprocess, "run") as spawn,
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(run_checks.run(["test"]), 1)
        spawn.assert_not_called()
        self.assertIn("python 3.11.9 is too old", err.getvalue())

    def test_current_interpreter_passes_pycheck(self) -> None:
        self.assertIsNone(run_checks._python_too_old((3, 13, 0)))


class MakefileDelegatesTests(unittest.TestCase):
    """The Makefile is the second door, not a second definition."""

    def _recipe(self, target: str) -> str:
        lines = (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        start = lines.index(next(x for x in lines if x.startswith(f"{target}:")))
        body = []
        for line in lines[start + 1 :]:
            if not line.startswith("\t"):
                break
            body.append(line.strip())
        return "\n".join(body)

    def test_test_and_radio_call_run_checks(self) -> None:
        for target in ("pycheck", "test", "test-radio"):
            with self.subTest(target=target):
                recipe = self._recipe(target)
                self.assertEqual(recipe, f"@$(PY) tools/run_checks.py {target}")


if __name__ == "__main__":
    unittest.main()
