"""What a failed import says to the castle's owner — tools/import_reason.py.

The sentences exist twice: here, for Castle Radio and the importer, and in
castle-core (`core/src/studio_reason_words.rs` + `studio_reason.rs`), for
the desk's jobs. One corpus, `tests/import_reasons.json`, is run through
both — this file for the Python, the crate's own `cargo test` for the Rust —
and this file also reads the Rust source, so a sentence or a table row
edited in one copy and not the other goes red here (docs/PARITY.md).
"""

from __future__ import annotations

import errno
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (the sandbox scrub)
import import_reason as ir

CORPUS = json.loads((ROOT / "tests" / "import_reasons.json").read_text("utf-8"))
WORDS_RS = (ROOT / "core" / "src" / "studio_reason_words.rs").read_text("utf-8")
#: Module constants that are not sentences.
NOT_SENTENCES = {"KNOWN", "UPDATE_DOWNLOADER"}


def sentences() -> dict[str, str]:
    """Every sentence constant the Python copy defines, by name."""
    return {
        n: v
        for n, v in vars(ir).items()
        if n.isupper()
        and not n.startswith("_")
        and isinstance(v, str)
        and n not in NOT_SENTENCES
    }


def rust_consts() -> dict[str, str]:
    """`pub const NAME: &str = "…";` from the Rust words, escapes undone."""
    found = re.findall(r'pub const ([A-Z_]+): &str =\s*"((?:[^"\\]|\\.)*)";', WORDS_RS)
    return {n: v.replace('\\"', '"').replace("\\\\", "\\") for n, v in found}


def rust_known() -> list[tuple[str, str]]:
    block = WORDS_RS[WORDS_RS.index("pub const KNOWN") :]
    block = block[: block.index("];")]
    return re.findall(r'\(\s*"((?:[^"\\]|\\.)*)",\s*([A-Z_]+),?\s*\)', block)


def expected(case: dict[str, str]) -> str:
    if "want" in case:
        return sentences()[case["want"]]
    if "tool" in case:
        return ir.TOOL_FAILED.format(prog=case["tool"])
    return case["say"]


class TestCorpus(unittest.TestCase):
    """The same cases the crate's tests read, through the Python copy."""

    def test_explain(self) -> None:
        for case in CORPUS["explain"]:
            with self.subTest(log=case["log"]):
                self.assertEqual(ir.explain(case["log"]), expected(case))

    def test_reason(self) -> None:
        for case in CORPUS["reason"]:
            with self.subTest(text=case["text"]):
                self.assertEqual(ir.reason(case["text"]), expected(case))

    def test_recognised(self) -> None:
        for case in CORPUS["recognised"]:
            with self.subTest(text=case["text"]):
                self.assertEqual(ir.recognised(case["text"]), expected(case))

    def test_basenames(self) -> None:
        for raw, want in CORPUS["basenames"]:
            with self.subTest(raw=raw):
                self.assertEqual(ir.basenames(raw), want)

    def test_every_sentence_is_said_by_some_case(self) -> None:
        said = {c["want"] for c in CORPUS["explain"] + CORPUS["reason"] if "want" in c}
        # START_FAILED and STALLED are said by the callers, never the scan.
        unsaid = set(sentences()) - said - {"TOOL_FAILED", "START_FAILED", "STALLED"}
        self.assertEqual(unsaid, set())


class TestRustCopy(unittest.TestCase):
    """The words and the table, compared with castle-core's source."""

    def test_the_same_sentences_word_for_word(self) -> None:
        self.assertEqual(rust_consts(), sentences())

    def test_the_same_table_row_for_row(self) -> None:
        names = {v: n for n, v in sentences().items()}
        mine = [(needle, names[said]) for needle, said in ir.KNOWN]
        self.assertEqual(rust_known(), mine)

    def test_the_rust_corpus_reader_knows_every_name(self) -> None:
        # studio_reason_words::named() is the crate's lookup for "want".
        for name in sentences():
            if name != "TOOL_FAILED":
                self.assertIn(f'"{name}" => {name},', WORDS_RS, name)


class TestSentences(unittest.TestCase):
    """What the owner reads: one sentence, what happened — what to do."""

    def test_each_says_what_happened_then_what_to_do(self) -> None:
        for name, text in sentences().items():
            with self.subTest(name=name):
                self.assertEqual(text.count(" — "), 1, text)
                self.assertTrue(text.endswith("."), text)
                self.assertNotRegex(text, r"exit \d|Error\b|Traceback|errno")

    def test_the_downloader_failures_offer_the_update(self) -> None:
        for said in (ir.DOWNLOADER_OLD, ir.DOWNLOADER_MISSING, ir.DOWNLOAD_FAILED):
            self.assertEqual(ir.action(said), ir.UPDATE_DOWNLOADER)
            self.assertIn("Update the downloader", said)
        self.assertIsNone(ir.action(ir.PRIVATE))
        self.assertIsNone(ir.action("anything else"))

    def test_owner_failure_never_comes_back_empty(self) -> None:
        self.assertEqual(ir.owner_failure(""), (ir.GENERIC, None))
        self.assertEqual(
            ir.owner_failure("ERROR: [youtube] x: Unable to extract nsig"),
            (ir.DOWNLOADER_OLD, ir.UPDATE_DOWNLOADER),
        )

    def test_recognised_never_passes_a_line_through(self) -> None:
        self.assertEqual(ir.recognised("some line the tool printed"), "")
        self.assertEqual(ir.recognised("ERROR: [x] y: z"), ir.DOWNLOADER_OLD)
        self.assertEqual(
            ir.reason("some line the tool printed"), "some line the tool printed"
        )


class TestOsErrors(unittest.TestCase):
    """A write that failed in-process, said in the table's words."""

    def test_errno_and_winerror(self) -> None:
        self.assertEqual(ir.for_os_error(OSError(errno.ENOSPC, "x")), ir.DISK_FULL)
        self.assertEqual(ir.for_os_error(OSError(errno.EACCES, "x")), ir.NO_ACCESS)
        self.assertEqual(ir.for_os_error(OSError(errno.EPERM, "x")), ir.NO_ACCESS)
        self.assertEqual(ir.for_os_error(OSError(errno.ENOMEM, "x")), ir.OUT_OF_MEMORY)
        self.assertIsNone(ir.for_os_error(FileNotFoundError(errno.ENOENT, "x")))
        self.assertIsNone(ir.for_os_error(OSError("no errno at all")))
        disk = OSError(errno.ENOSPC, "There is not enough space on the disk")
        disk.winerror = 112  # type: ignore[attr-defined]
        self.assertEqual(ir.for_os_error(disk), ir.DISK_FULL)
        denied = OSError(errno.EACCES, "Access is denied")
        denied.winerror = 5  # type: ignore[attr-defined]
        self.assertEqual(ir.for_os_error(denied), ir.NO_ACCESS)


if __name__ == "__main__":
    unittest.main()
