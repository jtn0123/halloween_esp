#!/usr/bin/env python3
"""Record the PYTHON studio's answers into tests/golden/ — run while it is
still the reference.

`tools/studio.py` is retired off-season (after Halloween 2026). The five
live-twin parity suites go with it, and with them the only thing that has
ever said what the desk's error strings are supposed to be. This tool boots
the Python studio over a throwaway sandbox, walks the deterministic surface
defined in `tests/golden_case.py`, and writes the answers down as JSON. From
then on `tests/test_studio_golden.py` replays the same script against the
Rust studio alone and diffs — no Python studio required, and none imported.

    .venv/bin/python tools/gen_golden.py            # rewrite the goldens
    .venv/bin/python tools/gen_golden.py --check    # fail if they'd change

Run it deliberately, and read the diff. A golden that changed because the
implementation changed is a UX decision; a golden that changed because the
machine changed is a bug in the corpus (see golden_case._tracks_shape).

Read by: whoever adds a case to golden_case.py, and CI never — the goldens
are committed artifacts, not a build step.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# tests/ on the path the way the test suites put tools/ on theirs: the
# corpus has exactly one definition and both sides read it from there.
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import golden_case as gc  # noqa: E402
from check_loc import SCENE_LIMIT  # noqa: E402
from helpers import SANDBOX_ENV  # noqa: E402


def capture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Boot the Python studio in a sandbox and walk the corpus."""
    tmp = Path(tempfile.mkdtemp(prefix="studio-golden-"))
    proc = None
    try:
        box = gc.Sandbox(tmp)
        box.seed()
        # box.env() sets the four CASTLE_* knobs itself; the operator's own
        # exported ones must not survive into the child (CLAUDE.md's
        # sandboxing note — an emulator shell is the usual way this goes
        # wrong), so they are stripped here rather than merely overwritten.
        env = {k: v for k, v in os.environ.items() if k not in SANDBOX_ENV}
        proc, port = gc.launch(
            [sys.executable, str(ROOT / "tools" / "studio.py")], box, env
        )
        read = gc.capture_read(port)
        scenes = gc.capture_scene_errors(port, box, SCENE_LIMIT)
        return read, scenes
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the goldens would change",
    )
    args = ap.parse_args(argv)
    read, scenes = capture()
    pairs = ((gc.READ_FILE, read), (gc.SCENE_FILE, scenes))
    if args.check:
        stale = []
        for path, data in pairs:
            want = gc.serialize(data)
            if not path.exists() or path.read_text() != want:
                stale.append(path)
        for path in stale:
            print(f"STALE: {path}")
        if stale:
            print("run `.venv/bin/python tools/gen_golden.py` and review the diff")
            return 1
        print("goldens are current")
        return 0
    for path, data in pairs:
        gc.dump(path, data)
        print(f"wrote {path.relative_to(ROOT)} ({len(data)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
