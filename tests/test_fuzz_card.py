r"""The protocol fuzz's card oracle (tools/fuzz_card.py), off the network.

The storm holds that every file a PUT wrote lies directly in the card. On
2026-10-03 a windows-latest run reported b'0' as "escaped the card" when it
had not. Another worker took the file away between the two looks
ntpath.realpath takes at it, and realpath kept the `\\?\` prefix it would
otherwise have dropped. These replay that race on any OS, with CPython's own
ntpath.realpath read from this interpreter's stdlib and run against a fake
`nt` whose _getfinalpathname loses the file on cue. They also hold the
check's teeth: a name that really lands outside the card is still caught, on
the real filesystem.
"""

from __future__ import annotations

import builtins
import errno
import importlib.util
import ntpath
import os
import shutil
import sys
import tempfile
import types
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from castle_fuzz import Fuzzer
from fuzz_card import LONG, Card, plain
from fuzz_http import Violation

#: A look that finds the file, and the Windows errors a look can get instead.
FOUND = 0
FILE_NOT_FOUND, ACCESS_DENIED, NOT_A_REPARSE_POINT = 2, 5, 4390
#: Where the jail sits on the pretend runner.
WIN_JAIL = "C:\\t\\fuzz-jail"


class WinError(OSError):
    """What nt raises: an OSError with the Windows code realpath reads."""

    def __init__(self, winerror: int, path: str) -> None:
        code = {FILE_NOT_FOUND: errno.ENOENT, ACCESS_DENIED: errno.EACCES}
        super().__init__(code.get(winerror, errno.EINVAL), f"winerror {winerror}", path)
        self.winerror = winerror


def _gone(path: str) -> str:  # nt._findfirstfile: nothing by that name
    raise WinError(FILE_NOT_FOUND, path)


def _not_a_link(path: str) -> str:  # nt.readlink: a plain file or folder
    raise WinError(NOT_A_REPARSE_POINT, path)


def windows_realpath(look: Callable[[str], str]) -> Callable[[str], str]:
    """CPython's own ntpath.realpath, with `look` as nt._getfinalpathname.

    ntpath imports its Windows half from `nt` and falls back to pure Python
    for each name it cannot find, so a module holding three names gives the
    Windows realpath on any host. It is handed in through the copy's own
    __import__, so the real `nt` (on Windows) is never touched."""
    nt = types.ModuleType("nt")
    nt.__dict__.update(
        _getfinalpathname=look, _findfirstfile=_gone, readlink=_not_a_link
    )

    def imp(name: str, *args: Any, **kw: Any) -> types.ModuleType:
        return nt if name == "nt" else builtins.__import__(name, *args, **kw)

    spec = importlib.util.spec_from_file_location("ntpath_replay", ntpath.__file__)
    assert spec is not None and spec.loader is not None
    replay = importlib.util.module_from_spec(spec)
    replay.__dict__["__builtins__"] = {**vars(builtins), "__import__": imp}
    spec.loader.exec_module(replay)
    realpath: Callable[[str], str] = replay.realpath
    return realpath


def runner(leaf: str, looks: Sequence[int]) -> Callable[[str], str]:
    """nt._getfinalpathname on a runner where every path is where it is
    asked for, except `leaf`. Its looks go by `looks` (FOUND, or the error
    that look gets), and the last one holds from then on."""
    seen = 0

    def look(path: str) -> str:
        nonlocal seen
        if path == leaf:
            step = looks[min(seen, len(looks) - 1)]
            seen += 1
            if step != FOUND:
                raise WinError(step, path)
        return LONG + path

    return look


def on_runner(jail: Path, p: PurePath) -> str:
    """`p`, a path under the host's jail, as the same path on the runner."""
    return str(PureWindowsPath(WIN_JAIL, *p.relative_to(jail).parts))


def racing(jail: Path, name: str, looks: Sequence[int]) -> tuple[type[Path], list[str]]:
    """A Path whose resolve() is the runner's answer while another worker
    takes `name` away from realpath on the looks `looks` says, and the list
    every answer is written to (a race that never happened proves nothing)."""
    leaf = ntpath.normpath(on_runner(jail, jail / "card" / name))
    realpath = windows_realpath(runner(leaf, looks))
    answers: list[str] = []

    class OnTheRunner(Path):
        def resolve(self, strict: bool = False) -> Any:
            answers.append(realpath(on_runner(jail, self)))
            return PureWindowsPath(answers[-1])

    return OnTheRunner, answers


class JailCase(unittest.TestCase):
    def setUp(self) -> None:
        self.jail = Path(tempfile.mkdtemp(prefix="fuzz-card-"))
        self.addCleanup(shutil.rmtree, self.jail, ignore_errors=True)
        (self.jail / "card").mkdir()

    def verdict(self, card: Path, decoded: bytes) -> None:
        """The storm's own check of a 200 to PUT `decoded`, six workers deep
        (so containment is all it asks, as in the concurrent storm)."""
        fz = Fuzzer("127.0.0.1", 0, 1, card)
        fz.threads = 6
        fz.expect_name_verdict(200, b"{}", True, decoded, b"x")


class TestTheRaceIsNotAnEscape(JailCase):
    def test_realpath_keeps_the_prefix_when_the_file_goes_mid_look(self) -> None:
        """The cause, pinned to the interpreter's ntpath: whichever way the
        looks fall, the answer names the card, and only the spelling moves."""
        leaf = WIN_JAIL + "\\card\\0"
        orders = {
            "seen, then deleted": [FOUND, FILE_NOT_FOUND],
            "seen, then delete-pending": [FOUND, ACCESS_DENIED],
            "pending, then deleted": [ACCESS_DENIED, ACCESS_DENIED, FILE_NOT_FOUND],
            "deleted, then pending": [FILE_NOT_FOUND, FILE_NOT_FOUND, ACCESS_DENIED],
        }
        for label, looks in orders.items():
            with self.subTest(label):
                got = windows_realpath(runner(leaf, looks))(leaf)
                self.assertEqual(got, LONG + leaf)
                self.assertEqual(plain(PureWindowsPath(got)), PureWindowsPath(leaf))
        for looks in ([FOUND], [FILE_NOT_FOUND]):  # no race: the plain name
            self.assertEqual(windows_realpath(runner(leaf, looks))(leaf), leaf)

    def test_a_file_deleted_between_the_looks_did_not_escape(self) -> None:
        """The 2026-10-03 failure, replayed: b'0' was in the card all along."""
        (self.jail / "card" / "0").write_bytes(b"x")
        runner_path, answers = racing(self.jail, "0", [FOUND, FILE_NOT_FOUND])
        self.verdict(runner_path(self.jail / "card"), b"0")
        self.assertIn(LONG + WIN_JAIL + "\\card\\0", answers)

    def test_an_escape_that_loses_the_same_race_is_still_caught(self) -> None:
        (self.jail / "0").write_bytes(b"x")
        runner_path, answers = racing(self.jail, "../0", [FOUND, FILE_NOT_FOUND])
        with self.assertRaisesRegex(Violation, "escaped the card"):
            self.verdict(runner_path(self.jail / "card"), b"../0")
        self.assertIn(LONG + WIN_JAIL + "\\0", answers)


class TestARealEscapeIsStillCaught(JailCase):
    """The real filesystem, nothing patched: each way out of the card."""

    def test_every_way_out_is_caught(self) -> None:
        card, jail = self.jail / "card", self.jail
        (jail / "out.bin").write_bytes(b"x")
        (card / "sub").mkdir()
        (card / "sub" / "out.bin").write_bytes(b"x")
        ways = {
            "dot-dot": b"../out.bin",
            "separator": b"sub/out.bin",
            "absolute": os.fsencode(jail / "out.bin"),
            "link out": b"link",
        }
        if sys.platform == "win32":  # a junction needs no privilege
            import _winapi

            (jail / "elsewhere").mkdir()
            _winapi.CreateJunction(str(jail / "elsewhere"), str(card / "link"))
        else:
            (card / "link").symlink_to(jail / "out.bin")
        for label, decoded in ways.items():
            with self.subTest(label), self.assertRaisesRegex(Violation, "escaped"):
                self.verdict(card, decoded)

    def test_a_file_in_the_card_is_not_an_escape(self) -> None:
        (self.jail / "card" / "in.bin").write_bytes(b"x")
        self.verdict(self.jail / "card", b"in.bin")


class TestCard(JailCase):
    def test_plain_drops_only_the_extended_length_prefix(self) -> None:
        cases = {
            "\\\\?\\C:\\t\\card": "C:\\t\\card",
            "\\\\?\\UNC\\srv\\share\\card": "\\\\srv\\share\\card",
            "C:\\t\\card": "C:\\t\\card",
        }
        for spelt, want in cases.items():
            self.assertEqual(plain(PureWindowsPath(spelt)), PureWindowsPath(want))
        self.assertEqual(plain(PurePosixPath("/t/card")), PurePosixPath("/t/card"))

    def test_strays_are_files_no_route_could_have_put_there(self) -> None:
        card = self.jail / "card"
        for rel in ("track.mp3", "site/index.html", "scenes/show.man", "x/y.bin"):
            (card / rel).parent.mkdir(exist_ok=True)
            (card / rel).write_bytes(b"x")
        self.assertEqual(Card(card).strays(), [card / "x" / "y.bin"])


if __name__ == "__main__":
    unittest.main()
