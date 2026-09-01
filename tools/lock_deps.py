#!/usr/bin/env python3
"""Regenerate requirements.lock from the requirement FILES, not from this venv.

`make lock` used to be a bare `pip freeze` of `.venv`, which meant the lock
described whatever the machine happened to have. On a developer box that is
never only the project: the optional stems pipeline (demucs, torch, and the
~30 packages behind them) lives in the same venv, and one `make lock` would
have written the whole stack into the file CI installs. Worse, freeze forgets
things — a fresh `pip freeze` cannot know that four pyobjc pins are reachable
only on macOS, so the markers that keep the lock installable on Linux would
be silently dropped.

So: build a throwaway venv from requirements.txt + requirements-dev.txt,
freeze THAT, and put back the two things freeze cannot say by itself —
the platform markers and the carry-over pins below. Nothing else is edited;
the resolver's answer is the lock.

Run it as `make lock`. It costs a real install (a minute or two) on purpose:
the lock is meant to be what a clean machine gets.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"
SOURCES = ("requirements.txt", "requirements-dev.txt")

#: Pins that only exist — and only CAN exist — on macOS. They arrive as
#: transitive deps of bleak (esphome's BLE half) and pip freeze prints them
#: bare, so a Linux `pip install -r requirements.lock` would fail on a wheel
#: that has no Linux build. The marker is the whole reason this file exists
#: rather than a one-line freeze.
PLATFORM_MARKERS = {
    "pyobjc-core": 'sys_platform == "darwin"',
    "pyobjc-framework-cocoa": 'sys_platform == "darwin"',
    "pyobjc-framework-corebluetooth": 'sys_platform == "darwin"',
    "pyobjc-framework-libdispatch": 'sys_platform == "darwin"',
}

#: Pinned here but named in no requirements file, because nothing IMPORTS
#: them — the importer runs yt-dlp as a subprocess, preferring the venv's
#: own copy when there is one (`tools/import_fetch.py yt_dlp_bin`). A clean
#: install therefore never has it, and a plain regeneration would drop the
#: pin that says which version the show was imported with. Kept at whatever
#: the existing lock says; add a line here only for another such tool.
CARRY_OVER = ("yt-dlp",)

_PIN = re.compile(r"^([A-Za-z0-9._-]+)==")


def package(line: str) -> str:
    """The distribution name a lock line pins, lower-cased, or "".

    Lower-cased and no further: `pip freeze` sorts on exactly this, so a
    regeneration reproduces the file's existing order rather than reshuffling
    `pip_audit` past `pip-requirements-parser` on every run.
    """
    m = _PIN.match(line.strip())
    return m.group(1).lower() if m else ""


def norm(name: str) -> str:
    """PEP 503 normalisation — for LOOKING a package up, never for sorting."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> dict[str, str]:
    """The current lock as {normalised name: whole line}."""
    if not path.exists():
        return {}
    out = {}
    for raw in path.read_text().splitlines():
        name = package(raw)
        if name:
            out[norm(name)] = raw.strip()
    return out


def freeze_clean(sources: list[Path], quiet: bool = False) -> list[str]:
    """Install the requirement files into a throwaway venv and freeze it."""
    with tempfile.TemporaryDirectory(prefix="castle-lock-") as tmp:
        venv = Path(tmp) / "venv"
        say = (lambda *_: None) if quiet else print
        say(f"lock: building a clean venv in {venv} …")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        pip = venv / "bin" / "pip"
        subprocess.run([str(pip), "install", "--quiet", "--upgrade", "pip"], check=True)
        args = [a for s in sources for a in ("-r", str(s))]
        say(f"lock: installing {', '.join(s.name for s in sources)} …")
        subprocess.run([str(pip), "install", "--quiet", *args], check=True)
        out = subprocess.run(
            [str(pip), "freeze", "--exclude-editable"],
            check=True,
            capture_output=True,
            text=True,
        )
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def compose(frozen: list[str], previous: dict[str, str]) -> tuple[list[str], list[str]]:
    """The lock text, plus the names carried over from the previous lock.

    Markers go back on; carry-over pins come back verbatim; everything is
    sorted case-insensitively by name, which is the order pip freeze itself
    produces and therefore the order the file is already in.
    """
    lines = []
    for raw in frozen:
        name = package(raw)
        marker = PLATFORM_MARKERS.get(norm(name))
        lines.append(f"{raw} ; {marker}" if marker else raw)
    have = {norm(package(ln)) for ln in lines}
    carried = []
    for name in CARRY_OVER:
        key = norm(name)
        if key in have:
            continue
        kept = previous.get(key)
        if kept:
            lines.append(kept)
            carried.append(name)
    lines.sort(key=package)
    return lines, carried


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=LOCK, help="lock file to write")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    sources = [ROOT / s for s in SOURCES]
    missing = [s for s in sources if not s.exists()]
    if missing:
        print(f"lock: missing {', '.join(s.name for s in missing)}", file=sys.stderr)
        return 1

    previous = read_lock(args.out)
    lines, carried = compose(freeze_clean(sources, args.quiet), previous)
    args.out.write_text("\n".join(lines) + "\n")
    if not args.quiet:
        marked = sum(1 for ln in lines if " ; " in ln)
        print(f"{args.out.name}: {len(lines)} pins, {marked} platform-marked")
        if carried:
            print(f"carried over from the previous lock: {', '.join(carried)}")
        for name in CARRY_OVER:
            if norm(name) not in previous and norm(name) not in {
                norm(package(ln)) for ln in lines
            }:
                print(f"note: {name} is in CARRY_OVER but nothing pins it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
