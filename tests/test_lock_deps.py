"""tools/lock_deps.py, and the question the lock exists to answer.

Two halves, one file, because they are the same subject from both ends
(grade report 2026-09-01 D4 and F1):

TestCompose exercises the pure functions `make lock` is built out of —
`package`, `norm`, `read_lock`, `compose` — over fixture text. No venv is
built here and no network is touched: `freeze_clean` is the one impure
function and it is exactly the part a test would only be re-implementing.
What can silently go wrong is composition: a dropped darwin marker makes
the lock uninstallable on Linux, a dropped carry-over loses the yt-dlp pin
the show was imported with, and a reshuffle turns every `make lock` into a
113-line diff.

TestLockSatisfiesRequirements answers the other question: does the file CI
installs actually agree with the files that specify what we depend on?
Nothing in CI resolves requirements.txt — every job installs the lock — so
a constraint change that the resolver would reject can merge green. That
is not hypothetical: Dependabot PR #13 raised aioesphomeapi's spec while
its own run log shows the job installing the old pin out of the lock. This
holds every specifier in requirements*.txt against the lock's pin for the
same package, which catches both a Dependabot bump nobody re-locked and a
hand edit to either side.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import lock_deps as ld

#: A freeze as pip prints one: bare pins, sorted case-insensitively, with
#: the four macOS-only pyobjc distributions present and unmarked (freeze
#: cannot know they are unreachable on Linux) and no yt-dlp (nothing
#: imports it, so a clean venv never installs it).
FROZEN = [
    "aioesphomeapi==45.10.3",
    "esphome==2026.8.1",
    "numpy==2.5.1",
    "pyobjc-core==11.2",
    "pyobjc-framework-Cocoa==11.2",
    "PyYAML==6.0.3",
]

#: The lock as it stood before: markers already applied, and the carry-over
#: pin present with a comment-free line of its own.
PREVIOUS_TEXT = """\
aioesphomeapi==45.10.2
numpy==2.5.0
pyobjc-core==11.1 ; sys_platform == "darwin"
yt-dlp==2026.8.20
"""


class TestPackageAndNorm(unittest.TestCase):
    def test_package_reads_the_pinned_name_lowercased(self) -> None:
        self.assertEqual(ld.package("PyYAML==6.0.3"), "pyyaml")
        self.assertEqual(ld.package("  numpy==2.5.1  "), "numpy")
        self.assertEqual(ld.package("pip_audit==2.10.0"), "pip_audit")

    def test_package_is_empty_for_anything_that_is_not_a_pin(self) -> None:
        for line in ("", "# a comment", "numpy~=2.5", "-e .", "numpy>=2"):
            self.assertEqual(ld.package(line), "", line)

    def test_norm_is_pep503_and_package_is_deliberately_not(self) -> None:
        """The two differ on purpose: norm() looks a package up, package()
        sorts. pip freeze sorts on the lower-cased *original* spelling, so
        normalising in package() would reshuffle `pip_audit` past
        `pip-requirements-parser` on every regeneration."""
        self.assertEqual(ld.norm("pyobjc_framework.Cocoa"), "pyobjc-framework-cocoa")
        self.assertEqual(ld.package("pip_audit==2.10.0"), "pip_audit")
        self.assertNotEqual(ld.package("pip_audit==2.10.0"), ld.norm("pip_audit"))


class TestReadLock(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lock-deps-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_reads_whole_lines_keyed_by_normalised_name(self) -> None:
        p = self.tmp / "requirements.lock"
        p.write_text(PREVIOUS_TEXT)
        got = ld.read_lock(p)
        self.assertEqual(got["numpy"], "numpy==2.5.0")
        self.assertEqual(got["yt-dlp"], "yt-dlp==2026.8.20")
        # the marker is part of the line, and the key is normalised
        self.assertEqual(
            got["pyobjc-core"], 'pyobjc-core==11.1 ; sys_platform == "darwin"'
        )

    def test_a_missing_lock_is_an_empty_dict_not_an_error(self) -> None:
        """First run on a fresh tree, and the reason compose() must cope
        with an empty `previous`."""
        self.assertEqual(ld.read_lock(self.tmp / "nope.lock"), {})


class TestCompose(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {
            ld.norm(ld.package(ln)): ln
            for ln in PREVIOUS_TEXT.splitlines()
            if ld.package(ln)
        }

    def test_platform_markers_are_reapplied_to_every_pyobjc_pin(self) -> None:
        lines, _ = ld.compose(FROZEN, self.previous)
        marked = {ln for ln in lines if " ; " in ln}
        self.assertEqual(
            marked,
            {
                'pyobjc-core==11.2 ; sys_platform == "darwin"',
                'pyobjc-framework-Cocoa==11.2 ; sys_platform == "darwin"',
            },
        )

    def test_the_marker_lookup_survives_freezes_odd_spelling(self) -> None:
        """pip freeze prints the distribution's own capitalisation, and
        PLATFORM_MARKERS is keyed PEP 503 — a case-sensitive lookup would
        drop the Cocoa marker and nothing else would notice until a Linux
        install failed."""
        lines, _ = ld.compose(["pyobjc-framework-Cocoa==11.2"], {})
        self.assertEqual(
            lines, ['pyobjc-framework-Cocoa==11.2 ; sys_platform == "darwin"']
        )

    def test_unmarked_packages_pass_through_verbatim(self) -> None:
        lines, _ = ld.compose(FROZEN, self.previous)
        self.assertIn("numpy==2.5.1", lines)
        self.assertIn("esphome==2026.8.1", lines)

    def test_carry_over_pins_come_back_from_the_previous_lock(self) -> None:
        lines, carried = ld.compose(FROZEN, self.previous)
        self.assertEqual(carried, ["yt-dlp"])
        self.assertIn("yt-dlp==2026.8.20", lines)

    def test_carry_over_is_silently_skipped_when_no_previous_lock_has_it(self) -> None:
        """A first-ever lock has nothing to carry; that is not an error."""
        lines, carried = ld.compose(FROZEN, {})
        self.assertEqual(carried, [])
        self.assertFalse([ln for ln in lines if ln.startswith("yt-dlp")])

    def test_a_freshly_frozen_carry_over_is_not_duplicated(self) -> None:
        """If the venv ever does grow yt-dlp, the resolver's answer wins
        and the stale previous pin must not be appended beside it."""
        lines, carried = ld.compose([*FROZEN, "yt-dlp==2026.9.1"], self.previous)
        self.assertEqual(carried, [])
        self.assertEqual(
            [ln for ln in lines if ln.startswith("yt-dlp")], ["yt-dlp==2026.9.1"]
        )

    def test_order_is_pip_freezes_and_composing_twice_is_a_fixed_point(self) -> None:
        """The regeneration diff should be the pins that moved, nothing
        else — so the output must already be in the order it will be read
        back in, and recomposing it must not shuffle anything."""
        lines, _ = ld.compose(FROZEN, self.previous)
        self.assertEqual(lines, sorted(lines, key=ld.package))
        again, _ = ld.compose(
            [ln.split(" ; ")[0] for ln in lines],
            {ld.norm(ld.package(ln)): ln for ln in lines},
        )
        self.assertEqual(again, lines)

    def test_the_real_lock_round_trips_through_compose_unchanged(self) -> None:
        """The fixture proves the rules; this proves they describe the file
        actually in the tree. Strip the markers off requirements.lock to
        make it look like a freeze, recompose, and the file must come back
        byte for byte — anything else means the next `make lock` would
        rewrite lines nobody changed."""
        lock = ld.read_lock(ROOT / "requirements.lock")
        self.assertTrue(lock, "requirements.lock is missing or has no pins")
        frozen = [
            ln.split(" ; ")[0]
            for ln in lock.values()
            if ld.norm(ld.package(ln)) not in {ld.norm(n) for n in ld.CARRY_OVER}
        ]
        lines, carried = ld.compose(frozen, lock)
        self.assertEqual(carried, list(ld.CARRY_OVER))
        self.assertEqual(
            "\n".join(lines) + "\n", (ROOT / "requirements.lock").read_text()
        )


class TestLockSatisfiesRequirements(unittest.TestCase):
    """Every specifier in requirements*.txt, against the lock's pin.

    The gap this closes is structural, not stylistic: no CI job resolves
    requirements.txt, so nothing else would notice a constraint the lock
    contradicts (grade report 2026-09-01 F1).
    """

    def setUp(self) -> None:
        self.lock = {
            ld.norm(ld.package(ln)): ln.split(" ; ")[0].split("==", 1)[1]
            for ln in ld.read_lock(ROOT / "requirements.lock").values()
        }

    def requirements(self) -> list[tuple[str, Requirement]]:
        out = []
        for name in ld.SOURCES:
            for raw in (ROOT / name).read_text().splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    out.append((name, Requirement(line)))
        return out

    def test_there_are_requirements_to_check(self) -> None:
        """A parser that quietly reads nothing would make the next test
        vacuously green."""
        self.assertGreaterEqual(len(self.requirements()), 10)

    def test_every_specifier_is_satisfied_by_the_locked_pin(self) -> None:
        for src, req in self.requirements():
            with self.subTest(source=src, requirement=str(req)):
                pin = self.lock.get(ld.norm(req.name))
                self.assertIsNotNone(
                    pin,
                    f"{req.name} is required by {src} but pinned by nothing in "
                    "requirements.lock — run `make lock`",
                )
                assert pin is not None
                self.assertTrue(
                    req.specifier.contains(Version(pin), prereleases=True),
                    f"{src} asks for {req} but requirements.lock pins "
                    f"{req.name}=={pin}. The lock is what every CI job "
                    "installs, so this constraint is currently unenforced — "
                    "run `make lock` (or revert the constraint).",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
