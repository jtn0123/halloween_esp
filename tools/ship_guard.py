#!/usr/bin/env python3
"""Nothing personal ships — the guard over what a buyer's copy carries.

docs/PRODUCTION-TODO.md §8. The repo is public, and a buyer's tools are
built from it three ways:

  * the GitHub source zip of a release tag — `git archive` output, so every
    tracked file NOT marked `export-ignore` in .gitattributes. The desktop
    app and the installer download it (tools/desktop_release.py);
  * the installer run from a clone, which copies `git ls-files` minus
    PERSONAL (tools/desktop_install.py) — the same list as the zip;
  * the release assets, tools/release_assets.py's contract: firmware
    images, castle-core zips, the desktop bundles and their manifests.

None of them may carry:

  * Wi-Fi secrets — firmware/secrets.yaml is never tracked at all;
  * the seller's own inventory and library — devices.toml and
    tracks/tracks.json are tracked for the seller's tools and marked
    export-ignore, so they never ship (PERSONAL, checked both ways);
  * a castle key in a tracked devices.toml (tools/castle_keys.py
    holds_key — the pre-commit hook's own rule, not a copy of it);
  * a real home directory: /Users/<name>/, /home/<name>/, C:\\Users\\<name>\\
    with a name that is not a placeholder (PLACEHOLDER_USERS);
  * a MAC address (MAC_ALLOWED says where one is accepted, and why);
  * a private-LAN address that is not a documented example (EXAMPLE_IPS).

The seller's own LAN (SELLER_NET) is in the design record — which castle
ran what, measured where — and is accepted in HISTORY only. Anywhere a
buyer's copy would ACT on it (a default host, a string on a page) is a
finding.

What the guard lets through — the examples, the seller's LAN and where it is
history, the MAC allowance, the placeholder names — is data, not code:
tools/ship_guard_allow.txt, each entry with its reason, read at start-up.

    ship_guard.py                 scan the tracked tree: exit 1 with findings
    ship_guard.py --release DIR   and a staged release directory

tests/test_ship_guard.py runs both against this repo and against planted
leaks; .github/workflows/release.yml runs --release on the staged assets.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import castle_keys

ROOT = Path(__file__).resolve().parent.parent
#: What the guard lets through, with the reason for each entry.
ALLOW = ROOT / "tools" / "ship_guard_allow.txt"
#: The guard names what it guards against, its allow-list names the seller's
#: LAN, and its test plants both: the files the tree scan does not read.
UNREAD = (
    "tools/ship_guard.py",
    "tools/ship_guard_allow.txt",
    "tests/test_ship_guard.py",
)

#: The seller's castle inventory: tracked, never shipped, and the one file a
#: castle key could be committed in.
DEVICES = "devices.toml"
#: Tracked for the seller's own tools, never shipped: export-ignore in
#: .gitattributes (the source zip) and skipped by the installer (a clone).
PERSONAL = (DEVICES, "tracks/tracks.json")
#: Names no tracked file and no release asset (or member of one) may have.
FORBIDDEN_NAMES = frozenset({"secrets.yaml", DEVICES, "tracks.json"})


def read_allow(path: Path = ALLOW) -> dict[str, list[str]]:
    """The allow file's sections: a `[name]` line opens one, every other line
    is an entry, and `#` starts a comment."""
    sections: dict[str, list[str]] = {}
    entries: list[str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            entries = sections.setdefault(line[1:-1], [])
        elif line and entries is None:
            raise ValueError(f"{path.name}: an entry before any [section]: {line}")
        elif line and entries is not None:
            entries.append(line)
    return sections


_ALLOWED = read_allow()
#: The documented stand-ins for a castle's or a router's address.
EXAMPLE_IPS = frozenset(_ALLOWED["example-ips"])
#: The seller's home LAN, where the porch castle and the bench boards live.
SELLER_NET = ipaddress.ip_network(_ALLOWED["seller-net"][0])
#: Where the seller's addresses are history, not configuration (prefixes).
HISTORY = tuple(_ALLOWED["history"])
#: How many MACs a path may carry.
MAC_ALLOWED = {p: int(n) for p, n in (e.split() for e in _ALLOWED["mac-allowed"])}
#: Home-directory names that are nobody's: examples and CI runners.
PLACEHOLDER_USERS = frozenset(_ALLOWED["placeholder-users"])

_IP = re.compile(
    r"(?<![\d.])(10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2})(?!\d|\.\d)"
)
_MAC = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])"
)
_HOME = re.compile(
    r"(?:/Users/|/home/|\b[A-Za-z]:\\{1,2}Users\\{1,2})([A-Za-z0-9._-]+)"
)
#: The seller's LAN in what is not text: an image or a program that compiled
#: one of its addresses in. Text is read line by line instead (scan_text).
_SELLER_BYTES = re.compile(
    rb"(?<![\d.])"
    + re.escape(".".join(str(SELLER_NET.network_address).split(".")[:3]).encode())
    + rb"\.\d{1,3}(?!\d)"
)
#: Release assets and their members are read whole up to this size; the
#: biggest today is a desktop bundle of a few tens of MB.
BLOB_MAX = 96_000_000


def scan_text(path: str, text: str) -> list[str]:
    """Every finding in one file's text, as `path:line: what`."""
    found: list[str] = []
    macs = 0
    history = path.startswith(HISTORY)
    for n, line in enumerate(text.splitlines(), 1):
        found.extend(
            f"{path}:{n}: {what}"
            for what in (_address(m.group(1), history) for m in _IP.finditer(line))
            if what
        )
        for m in _MAC.finditer(line):
            macs += 1
            if macs > MAC_ALLOWED.get(path, 0):
                found.append(f"{path}:{n}: a MAC address {m.group(0)}")
        found.extend(
            f"{path}:{n}: a home directory ({m.group(1)})"
            for m in _HOME.finditer(line)
            if m.group(1) not in PLACEHOLDER_USERS
        )
    return found


def _address(ip: str, history: bool) -> str:
    """What is wrong with one private-looking address ("" when nothing)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""  # 10.300.1.1 is a version number, not an address
    if ip in EXAMPLE_IPS or (history and addr in SELLER_NET):
        return ""
    if addr in SELLER_NET:
        return f"the seller's LAN address {ip}"
    return f"a private-LAN address that is not a documented example {ip}"


def _text(data: bytes) -> str | None:
    """The bytes as text, or None for a binary file."""
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def tracked(root: Path = ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def export_ignored(paths: list[str], root: Path = ROOT) -> set[str]:
    """Which of `paths` git archive leaves out (.gitattributes export-ignore)."""
    if not paths:
        return set()
    out = subprocess.run(
        ["git", "-C", str(root), "check-attr", "-z", "export-ignore", "--", *paths],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    fields = out.split("\0")
    return {fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] == "set"}


def keyed(paths: list[str], root: Path = ROOT) -> list[str]:
    """Tracked devices.toml files whose INDEX copy holds a castle key — the
    pre-commit hook's own rule (castle_keys.holds_key), asked of what git
    would ship. The working copy is the seller's to key; it never ships."""
    found = []
    for p in (p for p in paths if Path(p).name == DEVICES):
        text = subprocess.run(
            ["git", "-C", str(root), "show", f":{p}"], capture_output=True, check=True
        ).stdout.decode("utf-8", errors="replace")
        if castle_keys.holds_key(text):
            found.append(f"{p}: tracked with a castle key in it (castle_keys.py)")
    return found


def scan_tree(root: Path = ROOT) -> list[str]:
    files = tracked(root)
    found = [f"{p}: tracked — Wi-Fi secrets are never committed"
             for p in files if Path(p).name == "secrets.yaml"]  # fmt: skip
    found += keyed(files, root)
    present = [p for p in PERSONAL if p in files]
    ignored = export_ignored(present, root)
    found += [f"{p}: tracked but not export-ignore — it would ship in the "
              "release source zip (.gitattributes)" for p in present if p not in ignored]  # fmt: skip
    for p in files:
        if p in UNREAD or not (root / p).is_file():
            continue
        text = _text((root / p).read_bytes())
        if text is not None:
            found += scan_text(p, text)
    return found


def scan_blob(where: str, data: bytes) -> list[str]:
    """One file's bytes: scanned as text when it is text, else searched for
    the seller's LAN (a binary has no lines and no examples)."""
    text = _text(data)
    if text is not None:
        return scan_text(where, text)
    return [
        f"{where}: the seller's LAN address {m.group(0).decode()} in a binary"
        for m in _SELLER_BYTES.finditer(data)
    ]


def scan_release(dist: Path) -> list[str]:
    """A staged release directory: every name, and every file and archive
    member's bytes (zip and tar.gz are opened; a .dmg or an installer .exe
    is compressed, so only its name and the binary search apply)."""
    found: list[str] = []
    for f in sorted(p for p in dist.iterdir() if p.is_file()):
        found += _name(f.name, f.name)
        if f.suffix == ".zip":
            found += _scan_zip(f)
        elif f.name.endswith(".tar.gz"):
            found += _scan_tar(f)
        elif f.stat().st_size <= BLOB_MAX:
            found += scan_blob(f.name, f.read_bytes())
    return found


def _scan_zip(f: Path) -> list[str]:
    found: list[str] = []
    with zipfile.ZipFile(f) as z:
        for info in z.infolist():
            where = f"{f.name}!{info.filename}"
            found += _name(where, info.filename)
            if not info.is_dir() and info.file_size <= BLOB_MAX:
                found += scan_blob(where, z.read(info))
    return found


def _scan_tar(f: Path) -> list[str]:
    found: list[str] = []
    with tarfile.open(f) as t:
        for m in t.getmembers():
            where = f"{f.name}!{m.name}"
            found += _name(where, m.name)
            fh = t.extractfile(m) if m.isfile() and m.size <= BLOB_MAX else None
            if fh is not None:
                found += scan_blob(where, fh.read())
    return found


def _name(where: str, name: str) -> list[str]:
    base = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return (
        [f"{where}: a personal file ({base}) in a release"]
        if base in FORBIDDEN_NAMES
        else []
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--release", type=Path, help="also scan this staged release dir")
    args = ap.parse_args(argv)
    found = scan_tree()
    if args.release is not None:
        found += scan_release(args.release)
    for line in found:
        print(line)
    print(f"ship guard: {'FAIL' if found else 'PASS'} — {len(found)} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
