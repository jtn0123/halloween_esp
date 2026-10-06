#!/usr/bin/env python3
"""The htdemucs weights CI runs on, pinned, in the Hugging Face cache layout.

    model_pin.py fetch HUB     put the pinned files in HUB (only what is
                               missing or wrong), verify, print HUB
    model_pin.py verify HUB    verify only; print HUB, or what is wrong

Demucs 4.1 (requirements-desktop.lock) loads `htdemucs` through
huggingface_hub: htdemucs.yaml from adefossez/HTDemucs names the weights,
then <signature>.safetensors — 84 MB that the installer's model step and the
app's first launch fetch again on every clean runner. install-smoke.yml and
release.yml's desktop job keep them in the Actions cache instead, keyed on
CACHE_KEY, the way cross-platform.yml keeps its lame zip: restore, download
only on a miss, check every byte against the pin either way.

HUB is laid out as huggingface_hub's own cache (what HF_HUB_CACHE names):

    models--adefossez--HTDemucs/refs/main                    REVISION
    models--adefossez--HTDemucs/snapshots/REVISION/<file>    the file

with real files in snapshots/ — what the hub itself writes where it cannot
make symlinks (Windows without developer mode). With HF_HUB_OFFLINE=1,
hf_hub_download resolves `main` through refs/main and returns the snapshot
file without asking the network; castle_tools_status._model() reads the
same path. Stdlib only: the workflows run it on the runner's bare Python.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.request
from collections.abc import Callable
from http.client import HTTPMessage
from pathlib import Path
from typing import IO

REPO = "adefossez/HTDemucs"
#: The commit of REPO these bytes are (huggingface.co/api/models/REPO, "sha").
REVISION = "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003"
#: Each file Demucs reads, by name: its sha256 and size.
FILES: dict[str, tuple[str, int]] = {
    "htdemucs.yaml": (
        "239c445d0b14454d541ad8bd9bb271c9e536d267e8a4625208744cbb2e7bb66c",
        21,
    ),
    "955717e8.safetensors": (
        "d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd",
        84_025_440,
    ),
}
#: The Actions cache entry both workflows name: the model's signature and the
#: start of its weights' sha256, so a new pin is a new entry, never a stale hit.
CACHE_KEY = "htdemucs-955717e8-d9fa14133cfc"
URL = "https://huggingface.co/{repo}/resolve/{revision}/{name}"
TRIES = 5

Fetch = Callable[[str], bytes]


class PinError(RuntimeError):
    """The cache does not hold the pinned bytes, and could not be made to."""


def repo_dir(hub: Path) -> Path:
    return hub / ("models--" + REPO.replace("/", "--"))


def snapshot(hub: Path) -> Path:
    return repo_dir(hub) / "snapshots" / REVISION


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wrong(hub: Path, name: str) -> str | None:
    """Why the snapshot's `name` is not the pinned file, or None."""
    want, size = FILES[name]
    path = snapshot(hub) / name
    if not path.is_file():
        return f"{name}: missing"
    data = path.read_bytes()
    if len(data) != size or sha256(data) != want:
        return f"{name}: {len(data)} bytes, sha256 {sha256(data)}, not the pinned file"
    return None


def problems(hub: Path) -> list[str]:
    """Everything that keeps HUB from being the pinned cache."""
    found = [why for name in FILES if (why := wrong(hub, name))]
    ref = repo_dir(hub) / "refs" / "main"
    if not ref.is_file() or ref.read_text(encoding="utf-8").strip() != REVISION:
        found.append(f"refs/main does not name {REVISION}")
    return found


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """huggingface.co redirects the weights to its CDN — over https only."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith("https://"):
            raise PinError(f"refusing a redirect away from https: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_fetch(url: str) -> bytes:
    opener = urllib.request.build_opener(_HttpsOnly)
    with opener.open(url, timeout=60) as resp:
        body: bytes = resp.read()
    return body


def download(
    name: str, fetch: Fetch, sleep: Callable[[float], None] = time.sleep
) -> bytes:
    """The pinned `name`, tried TRIES times: a refused connection and a file
    that is not the pinned one are both worth another try, then fatal."""
    url = URL.format(repo=REPO, revision=REVISION, name=name)
    want, size = FILES[name]
    why = ""
    for attempt in range(1, TRIES + 1):
        try:
            data = fetch(url)
        except OSError as exc:
            why = f"{exc}"
        else:
            if len(data) == size and sha256(data) == want:
                return data
            why = f"{len(data)} bytes with sha256 {sha256(data)}, not the pinned file"
        print(f"{name}: try {attempt} of {TRIES} failed: {why}", file=sys.stderr)
        if attempt < TRIES:
            sleep(attempt * 20.0)
    raise PinError(f"{url}: {why}")


def fetch(
    hub: Path, get: Fetch = http_fetch, sleep: Callable[[float], None] = time.sleep
) -> None:
    """Make HUB the pinned cache, downloading only what it lacks."""
    for name in FILES:
        why = wrong(hub, name)
        if why is None:
            continue
        print(f"htdemucs: {why}; downloading it", file=sys.stderr)
        data = download(name, get, sleep)
        path = snapshot(hub) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(name + ".part")
        part.write_bytes(data)
        part.replace(path)
    ref = repo_dir(hub) / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(REVISION, encoding="utf-8")


def main(argv: list[str] | None = None, get: Fetch = http_fetch) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("command", choices=("fetch", "verify"))
    ap.add_argument("hub", type=Path)
    args = ap.parse_args(argv)
    hub = args.hub.expanduser().resolve()
    try:
        if args.command == "fetch":
            fetch(hub, get)
    except (PinError, OSError) as exc:
        print(f"htdemucs: {exc}", file=sys.stderr)
        return 1
    found = problems(hub)
    if found:
        print("htdemucs: " + "; ".join(found), file=sys.stderr)
        return 1
    # The only stdout: the workflow writes it to HF_HUB_CACHE.
    print(hub)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
