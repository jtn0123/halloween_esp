"""The lock's hashes — the half that says which BYTES, not which version.

`name==version` names a release; it says nothing about the file that
arrives, so until now every CI job trusted the index for the contents of
113 packages (SonarCloud's githubactions:S8544, and it is right). Each pin
in requirements.lock therefore carries `--hash=sha256:` for every
distribution file of that version — every wheel, every platform, plus the
sdist, because the lock is installed on Linux CI and used on the operator's
Mac — and CI installs it with `--require-hashes`.

Split from test_lock_deps.py on that seam: that file is about which
versions the lock holds, this one about the digests under them, the CLI
that refreshes them without resolving anything (`make lock-hashes`), and
the one function that talks to PyPI. The fetcher is a parameter everywhere,
so nothing here opens a socket — `fake_fetch` is imported beside the
fixture lock it answers for.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import lock_deps as ld
from test_lock_deps import PREVIOUS_TEXT, fake_fetch


class TestWithHashes(unittest.TestCase):
    """The digests, and the shape pip reads them in.

    A version is not a lock — `name==version` says which release to take and
    nothing about the bytes that arrive — so every pin carries the sha256 of
    every file of that version and CI installs `--require-hashes`. The
    fetcher is injected: nothing here opens a socket.
    """

    def test_a_pin_becomes_the_conventional_pip_block(self) -> None:
        got = ld.with_hashes(["numpy==2.5.1"], fake_fetch({"numpy==2.5.1": ["b", "a"]}))
        self.assertEqual(
            got,
            ["numpy==2.5.1 \\\n    --hash=sha256:a \\\n    --hash=sha256:b"],
        )

    def test_the_marker_stays_on_the_pin_line_above_the_hashes(self) -> None:
        """pip reads the marker from the requirement, not from a hash line —
        putting the continuation anywhere else makes the darwin pins
        unconditional and the lock uninstallable on Linux."""
        (got,) = ld.with_hashes(
            ['pyobjc-core==11.2 ; sys_platform == "darwin"'], fake_fetch()
        )
        self.assertEqual(
            got.splitlines()[0], 'pyobjc-core==11.2 ; sys_platform == "darwin" \\'
        )
        self.assertEqual(ld.hashes(got), ["pyobjc-core-11.2-s", "pyobjc-core-11.2-w"])

    def test_digests_are_sorted_and_deduplicated(self) -> None:
        """Determinism is the whole point: the same pins and the same index
        must produce the same bytes, or every regeneration is a full diff."""
        (got,) = ld.with_hashes(["x==1"], fake_fetch({"x==1": ["c", "a", "c", "b"]}))
        self.assertEqual(ld.hashes(got), ["a", "b", "c"])

    def test_re_hashing_an_already_hashed_lock_is_a_fixed_point(self) -> None:
        once = ld.with_hashes(
            ["numpy==2.5.1", "x==1 ; python_version > '3'"], fake_fetch()
        )
        twice = ld.with_hashes([ld.pin_line(e) for e in once], fake_fetch())
        self.assertEqual(twice, once)

    def test_a_version_pypi_has_no_files_for_is_a_hard_error(self) -> None:
        """Not an entry without hashes: `--require-hashes` is all-or-nothing,
        so one silently unhashed line turns the checking off at the next CI
        run instead of failing here, where somebody is looking."""
        with self.assertRaises(ld.LockError) as raised:
            ld.with_hashes(["ghost==9.9"], fake_fetch({"ghost==9.9": []}))
        self.assertIn("ghost==9.9", str(raised.exception))

    def test_a_line_that_is_not_a_pin_is_a_hard_error_too(self) -> None:
        for bad in ("numpy>=2", "# comment", ""):
            with self.assertRaises(ld.LockError):
                ld.with_hashes([bad], fake_fetch())


class TestPypiHashes(unittest.TestCase):
    """The one function that talks to the index, with the socket doubled out.

    What it has to get right is small and easy to get wrong: ask for the
    right URL, take every file's digest rather than this machine's wheel,
    and turn a network or 404 answer into a LockError the caller stops on.
    """

    @contextlib.contextmanager
    def answering(self, payload: object) -> Iterator[list[str]]:
        asked: list[str] = []

        @contextlib.contextmanager
        def fake_urlopen(url: str, timeout: float = 0) -> Iterator[io.StringIO]:
            asked.append(url)
            if isinstance(payload, Exception):
                raise payload
            yield io.StringIO(json.dumps(payload))

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            yield asked

    def test_every_files_digest_comes_back_sorted(self) -> None:
        urls = [
            {"digests": {"sha256": "bbb"}},
            {"digests": {"sha256": "aaa"}},
            {"digests": {"md5": "nope"}},
        ]
        with self.answering({"urls": urls}) as asked:
            got = ld.pypi_hashes("PyYAML", "6.0.3")
        self.assertEqual(list(got), ["aaa", "bbb"])
        self.assertEqual(asked, ["https://pypi.org/pypi/PyYAML/6.0.3/json"])

    def test_a_yanked_or_missing_release_is_a_lock_error(self) -> None:
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with self.answering(err), self.assertRaises(ld.LockError) as raised:
            ld.pypi_hashes("ghost", "9.9")
        self.assertIn("404", str(raised.exception))

    def test_an_unreachable_index_is_a_lock_error_not_a_traceback(self) -> None:
        with (
            self.answering(TimeoutError("timed out")),
            self.assertRaises(ld.LockError) as raised,
        ):
            ld.pypi_hashes("numpy", "2.5.1")
        self.assertIn("cannot reach PyPI", str(raised.exception))


class TestHashesOnlyCli(unittest.TestCase):
    """`make lock-hashes`: same pins, fresh digests, no resolve.

    The point of the flag is that re-hashing must never be a reason to move
    a version — the throwaway venv would resolve whatever is current on the
    day, and "I only wanted the hashes" is how a lock quietly drifts.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lock-deps-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "requirements.lock"
        self.out.write_text(PREVIOUS_TEXT)

    def run_main(self, *argv: str) -> tuple[int, str]:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = ld.main(["--out", str(self.out), *argv], fake_fetch())
        return code, out.getvalue()

    def test_every_version_survives_and_every_pin_is_rehashed(self) -> None:
        before = {k: ld.version(v) for k, v in ld.read_lock(self.out).items()}
        code, out = self.run_main("--hashes-only")
        self.assertEqual(code, 0)
        after = ld.read_lock(self.out)
        self.assertEqual({k: ld.version(v) for k, v in after.items()}, before)
        self.assertEqual(ld.hashes(after["numpy"]), ["numpy-2.5.0-s", "numpy-2.5.0-w"])
        self.assertIn("4 pins", out)

    def test_the_markers_are_still_there_afterwards(self) -> None:
        self.run_main("--hashes-only")
        text = self.out.read_text()
        self.assertIn('pyobjc-core==11.1 ; sys_platform == "darwin" \\', text)

    def test_running_it_twice_changes_nothing(self) -> None:
        self.run_main("--hashes-only")
        once = self.out.read_text()
        self.run_main("--hashes-only", "--quiet")
        self.assertEqual(self.out.read_text(), once)

    def test_an_empty_lock_is_refused_rather_than_silently_emptied(self) -> None:
        self.out.write_text("# nothing here\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = ld.main(["--out", str(self.out), "--hashes-only"], fake_fetch())
        self.assertEqual(code, 1)
        self.assertIn("no pins", err.getvalue())

    def test_a_pin_with_no_files_fails_without_writing(self) -> None:
        """The lock on disk stays the last good one — the same rule the
        previewer build follows when it cannot fit the page."""
        before = self.out.read_text()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = ld.main(
                ["--out", str(self.out), "--hashes-only", "--quiet"],
                fake_fetch({"numpy==2.5.0": []}),
            )
        self.assertEqual(code, 1)
        self.assertIn("numpy==2.5.0", err.getvalue())
        self.assertEqual(self.out.read_text(), before)


class TestRealLockIsHashed(unittest.TestCase):
    """The file in the tree, held to what CI now demands of it."""

    def setUp(self) -> None:
        self.lock = ld.read_lock(ROOT / "requirements.lock")

    def test_every_pin_carries_at_least_one_hash(self) -> None:
        for name, entry in self.lock.items():
            with self.subTest(package=name):
                self.assertTrue(
                    ld.hashes(entry),
                    f"{name} has no --hash line; `pip install --require-hashes` "
                    "refuses the whole file — run `make lock-hashes`",
                )

    def test_every_hash_is_a_sha256(self) -> None:
        for name, entry in self.lock.items():
            for h in ld.hashes(entry):
                with self.subTest(package=name, digest=h):
                    self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_the_sdist_only_pins_are_hashed_too(self) -> None:
        """The three CI installs with --no-binary: their sdist is the file
        pip will download, so its digest has to be in the list."""
        for name in ("crcmod", "esptool", "paho-mqtt"):
            with self.subTest(package=name):
                self.assertTrue(ld.hashes(self.lock[ld.norm(name)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
