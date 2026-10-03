#!/usr/bin/env python3
"""The Python test suites, spelled once, runnable where there is no `make`.

    python tools/run_checks.py [--print] STEP [STEP ...]

    STEP is one of: pycheck, test, test-radio

The Makefile's `test` and `test-radio` targets call this, and so does the
cross-platform CI job (.github/workflows/cross-platform.yml), which runs on a
Windows runner that ships no `make` (docs/PRODUCTION-TODO.md 4.3). One
definition of "the suite": a test directory or a node file added here is
added everywhere, and a Windows run cannot quietly test less than a Mac run.

Every step runs under THIS interpreter (`sys.executable`) — the Makefile
invokes it as `$(PY) tools/run_checks.py`, so `PY=` still chooses the venv —
from the repo root, and the first failure stops the run with that step's exit
code. `--print` names the commands and runs nothing.

Deliberately stdlib-only and free of 3.10+ runtime syntax: `pycheck` exists
to tell someone on an old interpreter that it IS old, and that message has to
survive being parsed by the interpreter it is complaining about.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: pyproject.toml's `requires-python`, as a tuple.
MIN_PYTHON = (3, 13)

RADIO = "demo/castle-radio"

#: Castle Radio's browser sources, run under node:test (node 22, no npm
#: install). The order is the order the Makefile always listed them in.
RADIO_NODE_TESTS = (
    "test_castle_radio.test.mjs",
    "test_castle_fuzz.test.mjs",
    "test_castle_honesty.test.mjs",
    "test_desktop_tools.test.mjs",
    "test_companion.test.mjs",
    "test_device_helper.test.mjs",
    "test_card_cues.test.mjs",
    "test_rich_preview.test.mjs",
    "test_lab_leds.test.mjs",
    "test_castle_key.test.mjs",
    "test_castle_find.test.mjs",
    "test_first_run.test.mjs",
    "test_castle_help.test.mjs",
    "test_castle_update.test.mjs",
    "test_downloader.test.mjs",
)


class StepError(Exception):
    """A step that cannot even start — an unknown name, a missing tool."""


def _python_too_old(version: tuple[int, ...]) -> str | None:
    if tuple(version[:2]) >= MIN_PYTHON:
        return None
    have = ".".join(str(p) for p in version[:3])
    want = ".".join(str(p) for p in MIN_PYTHON)
    return f"python {have} is too old — this repo needs {want}+ (make setup)"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise StepError(
            "node is not on PATH — test-radio runs its browser half under node 22"
        )
    return node


def commands(step: str) -> list[list[str]]:
    """The argv lists one step runs, in order. Paths are repo-relative and
    use `/`, which every platform's Python and node accept."""
    py = sys.executable
    if step == "pycheck":
        return []
    if step == "test":
        return [[py, "-m", "unittest", "discover", "-s", "tests", "-q"]]
    if step == "test-radio":
        unit = [py, "-m", "unittest", "discover", "-s", RADIO, "-t", RADIO]
        unit += ["-p", "test_*.py", "-q"]
        node = [_node(), "--test", *(f"{RADIO}/{t}" for t in RADIO_NODE_TESTS)]
        return [unit, node]
    raise StepError(f"unknown step {step!r} — expected one of: {', '.join(STEPS)}")


#: What `make test` means is `pycheck` first; the same holds here.
STEPS = ("pycheck", "test", "test-radio")
IMPLIES = {"test": ("pycheck",)}


def plan(steps: list[str]) -> list[str]:
    """The steps in run order: each one's prerequisites first, none twice."""
    out: list[str] = []
    for step in steps:
        for s in (*IMPLIES.get(step, ()), step):
            if s not in out:
                out.append(s)
    return out


def run(steps: list[str], dry: bool = False) -> int:
    for step in plan(steps):
        if step == "pycheck":
            msg = _python_too_old(tuple(sys.version_info[:3]))
            if msg:
                print(msg, file=sys.stderr)
                return 1
        for argv in commands(step):
            if dry:
                print(" ".join(argv))
                continue
            code = subprocess.run(argv, cwd=ROOT, check=False).returncode
            if code:
                print(f"run_checks: {step} failed (exit {code})", file=sys.stderr)
                return code
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry = "--print" in args
    steps = [a for a in args if a != "--print"]
    if not steps:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return run(steps, dry=dry)
    except StepError as exc:
        print(f"run_checks: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
