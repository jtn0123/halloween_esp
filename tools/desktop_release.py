"""GitHub Releases for the desktop installer: which one, which asset, which bytes.

The repo is public, so this reads the Releases API with no token — 60 calls
an hour per address, which is why every caller asks AT MOST ONCE per run and
the launcher once a day (docs/PRODUCTION-TODO.md section 9). The asset names
are a contract with the release workflow (release tags `vMAJOR.MINOR.PATCH`;
`castle-core-<rust-target>-<tag>.zip`; `SHA256SUMS` in sha256sum format
covering every other asset) and are spelled once, here.

Everything that touches the network goes through a `Fetch` callable the
caller can replace, so the suite exercises the logic with a dict of canned
answers and never opens a socket.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO = "jtn0123/halloween_esp"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/{{tag}}"
SUMS = "SHA256SUMS"
CORE_BINS = ("analyze_track", "scene_render", "studio")
USER_AGENT = "castle-tools-installer"

#: (url) -> body bytes. urllib in production, a dict lookup under test.
Fetch = Callable[[str], bytes]

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class ReleaseError(RuntimeError):
    """A release, asset or checksum the installer cannot use — a hard stop."""


@dataclass(frozen=True)
class Release:
    tag: str
    assets: Mapping[str, str] = field(default_factory=dict)  # name -> download URL
    source_zip: str = ""


def parse_tag(tag: str) -> tuple[int, int, int] | None:
    """`v1.2.3` as a comparable tuple; anything else (a pre-release, a
    branch name, "dev") is None — not a version an update is measured by."""
    m = _TAG.match(tag.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(candidate: str, installed: str) -> bool:
    """Should `--update` move from `installed` to `candidate`?

    A candidate that is not a release tag never wins. An installed version
    that is not one (a from-source install, an unknown) loses to any real
    release — the owner asked for the update, and a tagged build is what
    the update path exists to deliver.
    """
    new = parse_tag(candidate)
    if new is None:
        return False
    old = parse_tag(installed)
    return old is None or new > old


def rust_target(system: str, machine: str) -> str | None:
    """The castle-core build for this machine, or None when the release
    ships none (Linux, Windows on ARM): the installer builds from source."""
    m = machine.lower()
    if system == "Darwin":
        return (
            "aarch64-apple-darwin"
            if m in ("arm64", "aarch64")
            else "x86_64-apple-darwin"
        )
    if system == "Windows" and m in ("amd64", "x86_64"):
        return "x86_64-pc-windows-msvc"
    return None


def core_asset(target: str, tag: str) -> str:
    return f"castle-core-{target}-{tag}.zip"


def http_fetch(url: str) -> bytes:
    """GET `url` — the production Fetch. GitHub's API refuses requests
    without a User-Agent; downloads follow redirects to the CDN."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body: bytes = resp.read()
    return body


def release_from_api(body: bytes) -> Release:
    """A Release from one Releases-API answer. Drafts and pre-releases are
    refused here as well as by the `latest` endpoint: the buyer's channel is
    stable only."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ReleaseError(
            f"GitHub answered something that is not JSON: {exc}"
        ) from exc
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise ReleaseError("GitHub answered without a release tag")
    if data.get("draft") or data.get("prerelease"):
        raise ReleaseError(f"{data['tag_name']} is a draft or pre-release")
    assets = {
        str(a["name"]): str(a["browser_download_url"])
        for a in data.get("assets", [])
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url")
    }
    tag = str(data["tag_name"])
    source = f"https://github.com/{REPO}/archive/refs/tags/{tag}.zip"
    return Release(tag=tag, assets=assets, source_zip=source)


def find_release(fetch: Fetch, tag: str | None = None) -> Release:
    """The named release, or the latest stable one. ONE API call."""
    url = API_TAG.format(tag=tag) if tag else API_LATEST
    try:
        body = fetch(url)
    except OSError as exc:
        raise ReleaseError(f"cannot reach GitHub ({exc})") from exc
    return release_from_api(body)


def parse_sums(text: str) -> dict[str, str]:
    """sha256sum output as {file name: hex digest}. Accepts the binary-mode
    `*name` marker and ignores blank and comment lines."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise ReleaseError(f"{SUMS}: not a checksum line: {line!r}")
        out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, expected: str) -> None:
    """Refuse `path` unless its sha256 is `expected`, deleting it first."""
    got = sha256(path)
    if got != expected.lower():
        path.unlink(missing_ok=True)
        raise ReleaseError(
            f"{path.name}: checksum mismatch (got {got}, want {expected})"
        )


def download(url: str, dest: Path, fetch: Fetch, expected: str | None = None) -> Path:
    """Fetch `url` to `dest`, verified against `expected` when given."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        body = fetch(url)
    except OSError as exc:
        raise ReleaseError(f"download failed: {url} ({exc})") from exc
    dest.write_bytes(body)
    if expected:
        verify(dest, expected)
    return dest


def fetch_verified_asset(rel: Release, name: str, dest_dir: Path, fetch: Fetch) -> Path:
    """One release asset, checked against the same release's SHA256SUMS."""
    if name not in rel.assets:
        raise ReleaseError(f"release {rel.tag} has no asset {name}")
    if SUMS not in rel.assets:
        raise ReleaseError(
            f"release {rel.tag} has no {SUMS} — refusing unverified files"
        )
    sums = parse_sums(fetch(rel.assets[SUMS]).decode("utf-8"))
    if name not in sums:
        raise ReleaseError(f"{SUMS} of {rel.tag} does not list {name}")
    return download(rel.assets[name], dest_dir / name, fetch, sums[name])


def safe_extract(archive: Path, dest: Path) -> list[Path]:
    """Unzip `archive` into `dest`, refusing any member that would land
    outside it (zip-slip). Returns the files written."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    written = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if root != target and root not in target.parents:
                raise ReleaseError(
                    f"{archive.name}: member escapes the archive: {info.filename}"
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111 and os.name != "nt":
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            written.append(target)
    return written


def single_root(extracted: Path) -> Path:
    """GitHub's source zips wrap the tree in one `<repo>-<tag>/` directory;
    return that directory, or `extracted` itself when there is no wrapper."""
    entries = [p for p in extracted.iterdir() if not p.name.startswith("__MACOSX")]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else extracted


def place_core_bins(files: list[Path], dest: Path, exe_suffix: str) -> list[Path]:
    """Copy the three castle-core binaries out of an extracted zip into
    `dest` — wherever in the zip they sit — executable, and newer than any
    source beside them, so tools/core_bins.py takes them as fresh."""
    dest.mkdir(parents=True, exist_ok=True)
    by_name = {p.name: p for p in files}
    placed = []
    for name in CORE_BINS:
        src = by_name.get(name + exe_suffix)
        if src is None:
            raise ReleaseError(f"the castle-core zip has no {name + exe_suffix}")
        out = dest / src.name
        shutil.copyfile(src, out)
        if os.name != "nt":
            out.chmod(0o755)
        placed.append(out)
    return placed
