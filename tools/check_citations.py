#!/usr/bin/env python3
"""Every grade-report citation in the tree names the audit it came from.

    tools/check_citations.py            # report, exit 1 on an undated citation
    tools/check_citations.py --list     # every citation found, with its date
    tools/check_citations.py --exempt   # the exemption list with reasons

Item IDs are numbered per audit and RENUMBERED by the next one: `A1` was the
`/api/*` split on 2026-08-16, the missing publish stage on 2026-08-23, and the
crate's feature gates on 2026-09-01. Six reports now exist, so a comment that
says "grade report A1" points at all six and none of them. Dated, it points at
one:

    # …because the Python it asks counts the show first (grade report
    # 2026-08-31 A8).

So the rule this enforces is narrow: wherever the words "grade report" appear
in a tracked file, a `YYYY-MM-DD` follows them. The plural ("the grade
reports") is prose about the files rather than a citation and is left alone,
as is the hyphenated path form (`.claude/grade-report*.md`).

Writing a citation: `git blame` the line to find when it was written, read the
report of that era in `.claude/`, and confirm the ITEM matches the topic — the
date is corroboration, the topic is the proof. Not every report is in
`.claude/`: the 2026-08-21 one was overwritten rather than archived and lives
in git history (`git show 6dc0601:.claude/grade-report.md`), and the earliest
audits were never committed at all. When no item matches, describe the issue
in words instead — a wrong ID is worse than no ID.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_loc import ROOT, is_audit_output, is_binary, tracked_files

# Files allowed to say it without a date, each with WHY. The reports are the
# findings themselves; the test module has to write malformed citations to
# prove the check catches them. Nothing else belongs here — a comment that
# cannot name its audit should name the problem instead.
EXEMPT: dict[str, str] = {
    "tools/check_citations.py": "the rule itself — it has to spell out both shapes",
    "tests/test_check_citations.py": "fixtures — it plants bad citations to prove they fail",
}

#: The two words, however they are wrapped: a comment that runs long puts a
#: line break and a leader (`#`, `//`, `*`) between them, and a citation is no
#: less a citation for having been reflowed.
CITATION = re.compile(r"grade\s+report\b[^\S\n]*", re.IGNORECASE)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: Comment leaders, stripped so a wrapped citation reads as one string.
LEADER = re.compile(r"^\s*(?:@?#+|//+|/\*+|\*+|;+|--+|>+)\s?")

FIX = (
    'rewrite as "grade report YYYY-MM-DD A1", naming the audit that raised '
    "it.\n"
    "  Find it: `git blame` the line for when it was written, then read the "
    "report\n"
    "  of that era in .claude/ and confirm the ITEM matches the topic. Reports "
    "that\n"
    "  were overwritten rather than archived are in git history "
    "(`git show <rev>:.claude/grade-report.md`).\n"
    "  If no item matches, say what the problem was in words — a wrong ID is "
    "worse than no ID."
)


def strip_leader(line: str) -> str:
    """A source line with its comment leader removed."""
    return LEADER.sub("", line).rstrip()


def scan_text(text: str) -> list[tuple[int, bool, str]]:
    """(line number, dated, the citation and what follows it) for one file.

    Each line is joined with the one after it, so a citation split across a
    wrap is read whole; a match that starts in the joined half belongs to the
    next line's window and is skipped there rather than counted twice.
    """
    lines = text.splitlines()
    found = []
    for i, raw in enumerate(lines):
        head = strip_leader(raw)
        tail = strip_leader(lines[i + 1]) if i + 1 < len(lines) else ""
        window = f"{head} {tail}"
        for m in CITATION.finditer(window):
            if m.start() >= len(head):
                continue
            rest = window[m.end() :]
            found.append((i + 1, bool(DATE.match(rest)), window[m.start() :][:72]))
    return found


def scan(
    root: Path = ROOT, files: list[Path] | None = None
) -> list[tuple[str, int, bool, str]]:
    """Every citation in scope: (path, line, dated, excerpt)."""
    rows = []
    for p in tracked_files() if files is None else files:
        rel = p.relative_to(root).as_posix()
        if rel in EXEMPT or is_audit_output(rel) or not p.is_file() or is_binary(p):
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for line, dated, excerpt in scan_text(text):
            rows.append((rel, line, dated, excerpt))
    return rows


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    rows = scan()
    if "--exempt" in args:
        print(f"{len(EXEMPT)} exempt, plus the reports themselves:")
        for rel, why in EXEMPT.items():
            print(f"  {rel:<34} {why}")
        return 0
    if "--list" in args:
        for rel, line, dated, excerpt in rows:
            print(f"{'    ' if dated else 'BARE'}  {rel}:{line}  {excerpt}")
        return 0

    bare = [(rel, line, excerpt) for rel, line, dated, excerpt in rows if not dated]
    if not bare:
        print(f"citation check PASS — {len(rows)} grade-report citations, all dated")
        return 0
    print(f"citation check FAILED — {len(bare)} undated citation(s):\n")
    for rel, line, excerpt in bare:
        print(f"  {rel}:{line}   {excerpt}")
    print(
        "\nItem IDs are renumbered by every audit, so a bare one names six "
        f"reports at once —\n  {FIX}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
