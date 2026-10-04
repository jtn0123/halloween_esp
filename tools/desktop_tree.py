"""What an installed Castle Tools' `app/` tree is — one file list for both
ways a buyer gets one.

* The option-A installer (tools/desktop_install.py) copies it from a release
  zip or a clone into `<install>/app`.
* The desktop app's release build (tools/desktop_bundle.py) stages the same
  list as `castle/app/` inside the app, and on first launch the app runs that
  same installer from it (desktop/README.md "Where the servers come from").

Either way the list is `source_files()`: git's tracked files in a clone,
else the tree minus SKIP_DIRS (an extracted release zip, or the app's own
bundled copy, has nothing to skip) — and either way minus the seller's own
files (ship_guard.PERSONAL), which the release zip leaves out too. Stdlib
only, like the installer that imports it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import desktop_env as de
import ship_guard

#: Never copied into an app tree from a working tree: build output, venvs,
#: and anybody's library. A release zip has none of them anyway.
SKIP_DIRS = frozenset(
    {".git", ".venv", ".venv-desktop", "node_modules", "target", ".radio-data",
     "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".esphome",
     ".embuild", "_build"}
)  # fmt: skip

#: shutil.which, injectable.
Which = Callable[[str], str | None]


def source_files(src: Path, which: Which = shutil.which) -> list[Path]:
    """The tree's files, relative to `src`: git's tracked files in a clone,
    else a walk that skips SKIP_DIRS — minus ship_guard.PERSONAL."""
    git = which("git")
    if (src / ".git").exists() and git:
        out = subprocess.run(
            [git, "-C", str(src), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8")
        files = [Path(p) for p in out.split("\0") if p and (src / p).is_file()]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel_dir = Path(dirpath).relative_to(src)
            files.extend(rel_dir / f for f in filenames)
    personal = {Path(p) for p in ship_guard.PERSONAL}
    return sorted(f for f in files if f not in personal)


def copy_files(src: Path, files: Iterable[Path], dest: Path) -> None:
    """Copy each relative path in `files` from `src` into `dest`."""
    for relpath in files:
        out = dest / relpath
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / relpath, out)


def stage(src: Path, app: Path, which: Which = shutil.which) -> None:
    """Make `app` a copy of `src`'s file list, via `app.new` + rename, so a
    failed copy never leaves half a tree where a launcher looks.

    The castle-core build of the tree being replaced is kept (a repair or a
    from-source rebuild should not start from nothing), and Castle Radio's
    data link is unlinked first, so the old tree's removal never walks into
    the owner's library."""
    new = app.with_name("app.new")
    shutil.rmtree(new, ignore_errors=True)
    copy_files(src, source_files(src, which), new)
    built = app / "core" / "target"
    if built.is_dir():
        shutil.copytree(built, new / "core" / "target", dirs_exist_ok=True)
    old = app.with_name("app.old")
    if app.exists():
        link = app / de.RADIO_DATA
        if de.is_link(link):
            de.remove_link(link)
        shutil.rmtree(old, ignore_errors=True)
        app.rename(old)
    new.rename(app)
    shutil.rmtree(old, ignore_errors=True)
