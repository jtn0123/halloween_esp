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

A version is not a lock, though. `name==version` says which release to take
and nothing about the bytes that arrive, so every CI install trusted the
index for the contents of 113 packages (SonarCloud's githubactions:S8544
says so, and it is right). Each pin therefore carries `--hash=sha256:…` for
EVERY distribution file of that version — every wheel, for every platform,
plus the sdist — because the lock is installed on Linux CI and used on this
Mac, and three packages have no wheel at all. The digests come from PyPI's
JSON API, stdlib only, through an injectable fetcher so the tests never go
near the network. CI installs with `--require-hashes`.

Re-hashing does NOT need the throwaway venv: `--hashes-only` re-reads the
pins already in the lock and refreshes their digests, which is what to run
when a hash line is missing but no version should move.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
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
#:
#: dbus-fast is here for the mirror image of the pyobjc reason above: it is
#: bleak's LINUX half, so the macOS venv this file freezes never holds it and
#: the lock never named it. CI installed it anyway, unpinned, every run —
#: invisible until `--require-hashes` refused a requirement with no digest
#: (2026-09-18). Its line carries `sys_platform == "linux"` in the lock and is
#: carried with it; bump it by hand when bleak asks for a newer one.
CARRY_OVER = ("yt-dlp", "dbus-fast")

_PIN = re.compile(r"^([A-Za-z0-9._-]+)==")

#: How a package's digests are found. Injected everywhere so a test can hand
#: in a dict instead of an index, and so nothing under `make test` opens a
#: socket. Takes (name, version) and returns the sha256 of every file.
Fetcher = Callable[[str, str], Sequence[str]]


class LockError(RuntimeError):
    """A pin the lock cannot be written for — a hard stop, never a warning."""


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


def pin_line(entry: str) -> str:
    """A lock entry's head — `name==version[ ; marker]`, hashes stripped.

    An entry is several lines now, and everything that reasons about the pin
    itself (sorting, markers, carry-over, the version) wants the first one
    without its trailing continuation backslash.
    """
    head = entry.splitlines()[0] if entry else ""
    return head.strip().removesuffix("\\").strip()


def version(entry: str) -> str:
    """The version a lock entry pins, or "" when it pins nothing."""
    head = pin_line(entry).split(" ; ", 1)[0]
    return head.split("==", 1)[1].strip() if "==" in head else ""


def hashes(entry: str) -> list[str]:
    """The sha256 digests a lock entry carries, bare — no prefix, no
    continuation backslash."""
    out = []
    for line in entry.splitlines()[1:]:
        body = line.strip().removesuffix("\\").strip()
        if body.startswith("--hash=sha256:"):
            out.append(body.removeprefix("--hash=sha256:"))
    return out


def read_lock(path: Path) -> dict[str, str]:
    """The current lock as {normalised name: whole entry, hashes and all}."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    key = ""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        name = package(line)
        if name:
            key = norm(name)
            out[key] = line
        elif key and line.startswith("--hash="):
            out[key] += "\n" + raw.rstrip()
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
            # The head only: the digests are re-fetched for every pin below,
            # so carrying the old hash lines here would just duplicate them.
            lines.append(pin_line(kept))
            carried.append(name)
    lines.sort(key=package)
    return lines, carried


def pypi_hashes(name: str, ver: str, timeout: float = 30.0) -> Sequence[str]:
    """Every sha256 PyPI publishes for one release, sorted.

    All of them, deliberately: the lock is installed on Linux CI and used on
    macOS, so restricting the digests to this machine's wheel would make the
    file uninstallable everywhere else. pip is happy with a superset — it
    checks the file it actually downloaded against the list.
    """
    url = f"https://pypi.org/pypi/{name}/{ver}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise LockError(f"{name}=={ver}: PyPI answered {exc.code} for {url}") from exc
    except OSError as exc:  # DNS, TLS, timeout — all the same to the caller
        raise LockError(f"{name}=={ver}: cannot reach PyPI ({exc})") from exc
    digests = {
        u["digests"]["sha256"]
        for u in data.get("urls", [])
        if u.get("digests", {}).get("sha256")
    }
    return sorted(digests)


def with_hashes(
    lines: Iterable[str], fetch: Fetcher, say: Callable[[str], None] = lambda _m: None
) -> list[str]:
    """Each pin line, plus a `--hash=sha256:…` continuation per released file.

    The conventional pip shape — the pin, a trailing backslash, and the
    digests indented under it — so `pip install --require-hashes` reads it
    and a human can still see which version moved in a diff. A pin PyPI has
    no files for is a LockError and not an entry without hashes: a silently
    unhashed line would turn the whole install off (`--require-hashes` is
    all-or-nothing) at the next CI run rather than here.
    """
    out = []
    for line in lines:
        pin = pin_line(line)
        name, ver = package(pin), version(pin)
        if not name or not ver:
            raise LockError(f"not a pin: {line!r}")
        digests = list(fetch(name, ver))
        if not digests:
            raise LockError(f"{name}=={ver}: PyPI publishes no files for this version")
        say(f"  {name}=={ver}: {len(digests)} file(s)")
        body = [f"    --hash=sha256:{d}" for d in sorted(set(digests))]
        out.append(" \\\n".join([pin, *body]))
    return out


def main(argv: list[str] | None = None, fetch: Fetcher = pypi_hashes) -> int:
    """`fetch` is a parameter for the same reason `with_hashes` takes one:
    the CLI path — read the lock, re-hash it, write it back — is the half
    worth a test, and a test must not reach the index to get one."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=LOCK, help="lock file to write")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--hashes-only",
        action="store_true",
        help="refresh the digests of the pins already in the lock, "
        "without resolving anything — no throwaway venv, no version moves",
    )
    args = ap.parse_args(argv)

    say = (lambda _m: None) if args.quiet else print
    previous = read_lock(args.out)
    if args.hashes_only:
        if not previous:
            print(f"lock: {args.out} has no pins to re-hash", file=sys.stderr)
            return 1
        lines = sorted((pin_line(e) for e in previous.values()), key=package)
        carried: list[str] = []
    else:
        sources = [ROOT / s for s in SOURCES]
        missing = [s for s in sources if not s.exists()]
        if missing:
            print(
                f"lock: missing {', '.join(s.name for s in missing)}", file=sys.stderr
            )
            return 1
        lines, carried = compose(freeze_clean(sources, args.quiet), previous)

    say(f"lock: fetching digests for {len(lines)} pins from PyPI …")
    try:
        entries = with_hashes(lines, fetch, say)
    except LockError as exc:
        print(f"lock: {exc}", file=sys.stderr)
        return 1
    args.out.write_text("\n".join(entries) + "\n")
    if not args.quiet:
        marked = sum(1 for ln in lines if " ; " in ln)
        hashes = sum(e.count("--hash=sha256:") for e in entries)
        print(
            f"{args.out.name}: {len(lines)} pins, {marked} platform-marked, "
            f"{hashes} hashes"
        )
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
