"""Where a fuzzed upload may land: the card half of the protocol fuzz's oracle.

Split from tools/castle_fuzz.py at the 500-line cap along a seam that file
already had. It judges the castle's ANSWERS; this judges the CARD. A PUT
the castle accepted lands directly in the card directory, under its safe
name, and nowhere else, and after the storm nothing sits anywhere a PUT
cannot put it. Only an in-process emulator has a card this side can see,
so against a real castle (--host) none of this runs.
"""

from __future__ import annotations

from pathlib import Path, PurePath, PureWindowsPath

#: Windows's extended-length prefixes, and the plain spelling each one
#: stands for: the rewrite ntpath.realpath makes itself whenever its second
#: look at the file lets it (CPython 3.13 Lib/ntpath.py, realpath's
#: "strip off that prefix" block).
LONG_UNC, UNC = "\\\\?\\UNC\\", "\\\\"
LONG = "\\\\?\\"

#: The card's own folders: the desk page, the show, the boot log. A file
#: in one of these was put there by a route that names the folder.
FOLDERS = ("site", "scenes", "logs")


def plain(p: PurePath) -> PurePath:
    r"""`p` without the extended-length prefix: `\\?\C:\x` is `C:\x`, and
    `\\?\UNC\srv\share\x` is `\\srv\share\x`. These are one file spelt two
    ways. A POSIX path has no such prefix and comes back as it went in."""
    if not isinstance(p, PureWindowsPath):
        return p
    s = str(p)
    if s.startswith(LONG_UNC):
        return type(p)(UNC + s[len(LONG_UNC) :])
    if s.startswith(LONG):
        return type(p)(s[len(LONG) :])
    return p


class Card:
    """The emulator's card directory, as the oracle sees it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        #: Resolved ONCE. The card is made before the storm and nothing in
        #: the storm moves it, so this side of the comparison never races.
        self.real = plain(path.resolve())

    def escaped(self, f: Path) -> bool:
        r"""Whether `f`, a file the castle wrote and seen to exist, lies
        anywhere but directly in the card.

        resolve() stays: it is what sees `..`, a separator, a drive or an
        absolute name, and a link or junction out. Its answer is compared
        without the `\\?\` prefix, because ntpath.realpath looks at the file
        twice (its final name, then the plain spelling) and keeps the prefix
        when the file is gone by the second look. Six workers PUT and DELETE
        the same short names, so that happens (windows-latest, 2026-10-03:
        b'0' "escaped the card"). The race picks the spelling, never the
        directory. Dropping the prefix rules the false alarm out where a
        retry would only make it rarer, and a real escape still resolves
        somewhere else however it is spelt."""
        return plain(f.resolve()).parent != self.real

    def strays(self) -> list[Path]:
        """Files below the card that no route could have put there: not in
        the card itself, and not in one of its own folders."""
        return [
            p
            for p in self.path.rglob("*")
            if p.is_file() and p.parent != self.path and p.parent.name not in FOLDERS
        ]
