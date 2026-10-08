"""The vocabulary of the third-party notices: a component, a licence
expression, which licence texts it needs, and the policy a redistributed
component is held to.

tools/third_party_notices.py is the front door; this module is the part
every artifact's table (notices_firmware, notices_desktop) is written in.

Licence texts live in licenses/, each one pinned here by sha256 with where
it came from, so a text cannot be edited by hand without a test noticing:

  licenses/texts/<SPDX-ID>.txt     the generic text, from SPDX's
                                   license-list-data v3.27.0 — used with the
                                   component's own copyright lines
  licenses/components/<name>.txt   a component's OWN licence or NOTICE file,
                                   verbatim, where it carries one that the
                                   generic text would paraphrase

Stdlib only: the release jobs run the CLI on a bare runner python.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSES = ROOT / "licenses"

SPDX = "https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/"
IDF = "ESP-IDF v5.5.5, components/"
XTENSA_TC = "xtensa-esp-elf esp-14.2.0_20260121 toolchain, "
#: The one text both GPL-3.0 ids print (GENERIC).
GPL3_TEXT = "texts/GPL-3.0.txt"
#: The Mozilla Public License: the file-level copyleft the notices allow.
MPL = "MPL-2.0"
#: How an SPDX expression attaches an exception to its licence.
WITH = " WITH "

#: Every licence text in licenses/, relative to it: (where it came from,
#: sha256). tests/test_third_party_notices.py hashes each file against this
#: and refuses a file under licenses/texts or licenses/components that is
#: not listed — a text is copied, never written.
TEXTS: dict[str, tuple[str, str]] = {
    "texts/Apache-2.0.txt": (
        SPDX + "Apache-2.0.txt",
        "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff",
    ),
    "texts/BSD-2-Clause.txt": (
        SPDX + "BSD-2-Clause.txt",
        "f32fb3b417a194167cfad068223fc975ba96c5960513a10f66a3c28720aec1df",
    ),
    "texts/BSD-3-Clause.txt": (
        SPDX + "BSD-3-Clause.txt",
        "5a93d5831e1297ab10fe643e1a631e83be392896da14ee2951285a79012df69d",
    ),
    "texts/GCC-exception-3.1.txt": (
        SPDX + "GCC-exception-3.1.txt",
        "7103d4f7f7e2f8ce10d282a05e0689637f8d6d9ef7b399d808d1da313e69b960",
    ),
    GPL3_TEXT: (
        SPDX + "GPL-3.0-or-later.txt",
        "fb981668c18a279e285fc4d83fba1e836cc84dd4daa73c9697d3cfd2d8aca6e0",
    ),
    "texts/ISC.txt": (
        SPDX + "ISC.txt",
        "f2ec607f67bb0dd3053b49835b02110d5cd0f8eb6da3aac4dc0b142a6b299be9",
    ),
    "texts/LLVM-exception.txt": (
        SPDX + "LLVM-exception.txt",
        "e34c58338bd89d43e709e226610d8f32b3e3c47f4ad9a99a8dc1d4ac7842488e",
    ),
    "texts/MIT.txt": (
        SPDX + "MIT.txt",
        "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
    ),
    "texts/MPL-2.0.txt": (
        SPDX + "MPL-2.0.txt",
        "66a3107d5ad6a058aab753eaac2047ccb2ed0e39465dd0fe5844da3e300d5172",
    ),
    "texts/Unicode-3.0.txt": (
        SPDX + "Unicode-3.0.txt",
        "f7db81051789b729fea528a63ec4c938fdcb93d9d61d97dc8cc2e9df6d47f2a1",
    ),
    "texts/Zlib.txt": (
        SPDX + "Zlib.txt",
        "bfb1112d49db5b1daecdfef24bd7e2f3ea0bafb33aa67aa0ab51e2bf8407c03d",
    ),
    "components/esphome-LICENSE-preamble.txt": (
        (
            "esphome/esp-audio-libs 3.2.1 LICENSE, lines 1-10 (the ESPHome "
            "License preamble; the two texts it attaches are texts/MIT.txt and "
            "texts/GPL-3.0.txt)"
        ),
        "303c8acbf9c56a1902586c2f40917aaf9da489a52b50ea2d02015fac19b65d03",
    ),
    "components/fatfs-ff.h-header.txt": (
        IDF + "fatfs/src/ff.h, lines 1-18",
        "51213755042869203f7e8fe24d18693d9998b42fc34afefe7e5113a7ce5c08b6",
    ),
    "components/freertos-LICENSE.txt": (
        IDF + "freertos/FreeRTOS-Kernel/LICENSE.md",
        "508a77d2e7b51d98adeed32648ad124b7b30241a8e70b2e72c99f92d8e5874d1",
    ),
    "components/http_parser-LICENSE.txt": (
        IDF + "http_parser/LICENSE.txt",
        "3aced2d086d3f59cafb6589770c30c1e45a5bdf50c1d4375060b81a615db604c",
    ),
    "components/libstdcxx-hp-sgi.txt": (
        XTENSA_TC + "include/c++/14.2.0/bits/stl_algo.h, lines 25-48",
        "7c4ba0488d3c82880848ebe8b1f9b30f33bb718141d52d4cd196dd47919eb6c6",
    ),
    "components/lwip-COPYING.txt": (
        IDF + "lwip/lwip/COPYING",
        "ef4aac92e05e87cd1cdc140870ed52206ba03d4a7fe46c1e11d7ffa6c87d252b",
    ),
    "components/micro-mp3-NOTICE.txt": (
        "esphome/micro-mp3 0.4.0 NOTICE",
        "24869b60c1e816ca78d2ace98c62eff82231ea57073652560930d4c4ec0b888f",
    ),
    # 1,293 lines: kept whole by splitting it between sections (23)/(24)
    # and (40)/(41), the only way a verbatim text fits the 500-line rule.
    # licence_text() puts the three back together; the joined bytes are the
    # toolchain file's (sha256 422aa402…57e9).
    "components/newlib-COPYING.NEWLIB.1.txt": (
        XTENSA_TC + "share/licenses/newlib/COPYING.NEWLIB, lines 1-479",
        "a07999aa94a4ba9accc9ccc8b0dd6e81db6654a103b051efada3f139206ba2f0",
    ),
    "components/newlib-COPYING.NEWLIB.2.txt": (
        XTENSA_TC + "share/licenses/newlib/COPYING.NEWLIB, lines 480-929",
        "eb23d2a36acb01a2f30b9ee64b89574e36a77400501bdc9ac0628b111f0dab34",
    ),
    "components/newlib-COPYING.NEWLIB.3.txt": (
        XTENSA_TC + "share/licenses/newlib/COPYING.NEWLIB, lines 930-1293",
        "364920fb031b29d658f527e9dab34454ec9d1a5a73577beb6d53d7e58449fa69",
    ),
    "components/nsis-COPYING.txt": (
        "https://raw.githubusercontent.com/kichik/nsis/v311/COPYING",
        "dc0f74a312c08ffc900548a67ae9a3670ed28ad25a3afda1fe0504da16f89361",
    ),
    "components/opus-COPYING.txt": (
        "esphome/micro-opus 0.4.1 lib/opus/COPYING",
        "01e1167d54a096d123cf6dfbbeb19587278845c6481d2d66d545669846079551",
    ),
    "components/webview2-LICENSE.txt": (
        (
            "Microsoft.Web.WebView2 1.0.3650.58 nupkg, LICENSE.txt "
            "(https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3650.58)"
        ),
        "0af8f1b807512aae39c2ac1aa4d0cae65cabecb6fd554b8439a5162a0d6eca55",
    ),
}

#: A text too long for one file -> its parts, in order (see newlib above).
SPLIT: dict[str, tuple[str, ...]] = {
    "components/newlib-COPYING.NEWLIB.txt": tuple(
        f"components/newlib-COPYING.NEWLIB.{n}.txt" for n in (1, 2, 3)
    ),
}

#: SPDX id -> its generic text. Both GPL-3.0 ids share one text: the
#: licence is the same document, the suffix is only the grant's wording.
GENERIC: dict[str, str] = {
    "Apache-2.0": "texts/Apache-2.0.txt",
    "BSD-2-Clause": "texts/BSD-2-Clause.txt",
    "BSD-3-Clause": "texts/BSD-3-Clause.txt",
    "GCC-exception-3.1": "texts/GCC-exception-3.1.txt",
    "GPL-3.0-only": GPL3_TEXT,
    "GPL-3.0-or-later": GPL3_TEXT,
    "ISC": "texts/ISC.txt",
    "LLVM-exception": "texts/LLVM-exception.txt",
    "MIT": "texts/MIT.txt",
    MPL: "texts/MPL-2.0.txt",
    "Unicode-3.0": "texts/Unicode-3.0.txt",
    "Zlib": "texts/Zlib.txt",
}

# ── Policy ─────────────────────────────────────────────────────────────
#: Which licence, of an `A OR B` choice, the notices comply with — first
#: listed wins. A licence not listed is never chosen while a listed one is
#: on offer.
PREFERENCE = (
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "ISC",
    "Zlib",
    "Unicode-3.0",
    MPL,
)
#: Permissive: a copyright line and the text are the whole obligation.
PERMISSIVE = frozenset(PREFERENCE[:7])
#: File-level copyleft: ships fine, but the recipient must be told where
#: the source is — a component under one of these needs `source`.
WEAK_COPYLEFT = frozenset({MPL, "LicenseRef-NSIS"})
#: Whole-work copyleft. Never shipped silently: a component under one of
#: these needs an `override` saying why, which the notices print.
COPYLEFT = re.compile(r"^[AL]?GPL-")
#: Terms that forbid commercial use. The castle is sold; nothing under
#: one of these ships, override or not.
NONCOMMERCIAL = re.compile(r"(?:^|-)NC(?:-|$)|noncommercial", re.IGNORECASE)
#: A component's own licence that has no SPDX id: it ships with its own
#: text (licenses/components/), so the policy reads its category here.
CUSTOM = {
    "LicenseRef-FatFs": "permissive",
    "LicenseRef-newlib": "permissive",
    "LicenseRef-NSIS": "weak",
}


@dataclass(frozen=True)
class Component:
    """One thing a shipped artifact carries that somebody else wrote.

    `license` is the expression as the package states it (a crate's
    `MIT/Apache-2.0` stays as written; `chosen()` normalises it). `texts`
    are licenses/components/ files printed verbatim after the component.
    `override` is the reason a copyleft licence is shipped knowingly; the
    notices print it, so it is written for the recipient to read."""

    name: str
    version: str
    license: str
    copyright: tuple[str, ...] = ()
    source: str = ""
    texts: tuple[str, ...] = ()
    note: str = ""
    override: str = ""


class SpdxError(ValueError):
    """An expression this parser cannot read: fix the table, not the parser."""


_TOKEN = re.compile(r"\s*([()]|[A-Za-z0-9.+:-]+)")


def _tokens(expr: str) -> list[str]:
    text = expr.replace("/", " OR ")
    out: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if m is None:
            raise SpdxError(f"cannot read licence expression {expr!r}")
        word = m.group(1)
        out.append(word.upper() if word.upper() in {"AND", "OR", "WITH"} else word)
        pos = m.end()
    return out


class _Parser:
    """Recursive descent over `_tokens(expr)`: or := and (OR and)*,
    and := atom (AND atom)*, atom := ( or ) | id [WITH id]."""

    def __init__(self, expr: str) -> None:
        self.expr = expr
        self.toks = _tokens(expr)
        self.pos = 0

    def peek(self) -> str | None:
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def take(self) -> str:
        if self.pos >= len(self.toks):
            raise SpdxError(f"licence expression ends early: {self.expr!r}")
        self.pos += 1
        return self.toks[self.pos - 1]

    def atom(self) -> list[tuple[str, ...]]:
        word = self.take()
        if word == "(":
            inner = self.or_expr()
            if self.take() != ")":
                raise SpdxError(f"unbalanced parenthesis in {self.expr!r}")
            return inner
        if word in {")", "AND", "OR", "WITH"}:
            raise SpdxError(f"unexpected {word!r} in {self.expr!r}")
        if self.peek() != "WITH":
            return [(word,)]
        self.take()
        return [(f"{word}{WITH}{self.take()}",)]

    def and_expr(self) -> list[tuple[str, ...]]:
        result = self.atom()
        while self.peek() == "AND":
            self.take()
            rhs = self.atom()
            result = [a + b for a in result for b in rhs]
        return result

    def or_expr(self) -> list[tuple[str, ...]]:
        result = self.and_expr()
        while self.peek() == "OR":
            self.take()
            result += self.and_expr()
        return result


def alternatives(expr: str) -> list[tuple[str, ...]]:
    """`expr` in disjunctive form: each alternative is the licences that
    must ALL be complied with if that branch is chosen. `X WITH Y` stays one
    term, because the exception only exists attached to its licence."""
    parser = _Parser(expr)
    if not parser.toks:
        raise SpdxError("empty licence expression")
    result = parser.or_expr()
    if parser.pos != len(parser.toks):
        raise SpdxError(f"trailing tokens in licence expression {expr!r}")
    return result


def _rank(term: str) -> int:
    base = term.split(WITH)[0]
    return PREFERENCE.index(base) if base in PREFERENCE else len(PREFERENCE) + 10


def chosen(expr: str) -> tuple[str, ...]:
    """The licences the notices comply with: the cheapest alternative by
    PREFERENCE, its terms sorted and de-duplicated."""
    best = min(alternatives(expr), key=lambda alt: (sum(_rank(t) for t in alt), alt))
    return tuple(sorted(set(best)))


def category(term: str) -> str:
    """permissive | weak | copyleft | noncommercial | unknown, for one term."""
    base, _, exception = term.partition(WITH)
    if NONCOMMERCIAL.search(base):
        return "noncommercial"
    if COPYLEFT.match(base):
        return "copyleft"
    if base in PERMISSIVE:
        return "permissive"
    if base in WEAK_COPYLEFT:
        return "weak"
    return CUSTOM.get(base, "unknown") if not exception else "unknown"


def texts_for(term: str) -> list[str]:
    """The generic texts one chosen term needs (none for a LicenseRef — its
    own text travels with the component)."""
    return [GENERIC[p] for p in term.split(WITH) if p in GENERIC]


def _term_errors(who: str, term: str, c: Component) -> list[str]:
    """Why one chosen term of a component could not ship."""
    kind = category(term)
    base = term.split(WITH)[0]
    errors = []
    if kind == "noncommercial":
        errors.append(f"{who}: {term} forbids commercial use — it cannot ship")
    elif kind == "unknown":
        errors.append(f"{who}: {term} is not a licence the policy knows")
    elif kind == "copyleft" and not c.override:
        errors.append(f"{who}: {term} is copyleft and has no recorded override")
    elif kind == "weak" and not c.source:
        errors.append(f"{who}: {term} needs a source location in the notices")
    if not base.startswith("LicenseRef-"):
        errors.extend(
            f"{who}: no licence text for {part}"
            for part in term.split(WITH)
            if part not in GENERIC
        )
    elif not c.texts:
        errors.append(f"{who}: {term} has no licence text of its own")
    return errors


def policy_errors(components: list[Component]) -> list[str]:
    """Why these components could not ship, one line each — empty when they
    can. The rules are the module's PERMISSIVE / WEAK_COPYLEFT / COPYLEFT /
    NONCOMMERCIAL / CUSTOM tables."""
    errors: list[str] = []
    for c in components:
        who = f"{c.name} {c.version}"
        try:
            terms = chosen(c.license)
        except SpdxError as exc:
            errors.append(f"{who}: {exc}")
            continue
        for term in terms:
            errors.extend(_term_errors(who, term, c))
        if not c.copyright:
            errors.append(f"{who}: no copyright line (or a stated reason for none)")
        errors.extend(
            f"{who}: unknown licence text {t}"
            for t in c.texts
            if t not in TEXTS and t not in SPLIT
        )
    return errors


def text_errors(root: Path = LICENSES) -> list[str]:
    """Every pinned text present with its pinned bytes, and nothing under
    texts/ or components/ that the pin table does not know."""
    errors: list[str] = []
    for rel, (_, digest) in TEXTS.items():
        path = root / rel
        if not path.is_file():
            errors.append(f"licenses/{rel} is missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(
                f"licenses/{rel} differs from the copy pinned in notices_model.TEXTS"
            )
    for sub in ("texts", "components"):
        for path in sorted((root / sub).glob("*")):
            rel = f"{sub}/{path.name}"
            if rel not in TEXTS:
                errors.append(f"licenses/{rel} is not pinned in notices_model.TEXTS")
    return errors


def shown(path: Path) -> str:
    """Every path the notices tools print, printed one way: forward slashes
    on every OS, so a message — and the test that compares it — reads the
    same on Windows as on the Mac."""
    return path.as_posix()


def licence_text(rel: str, root: Path = LICENSES) -> str:
    """A pinned text (a SPLIT one joined back together), newline-terminated."""
    parts = SPLIT.get(rel, (rel,))
    body = "".join((root / p).read_text(encoding="utf-8") for p in parts)
    return body if body.endswith("\n") else body + "\n"
