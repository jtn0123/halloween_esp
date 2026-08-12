#!/usr/bin/env python3
"""Manage the castle's SD card from the Mac, over WiFi — the card stays in the slot.

    tools/sd_sync.py <ip> status              what firmware, is the card there
    tools/sd_sync.py <ip> ls                  list the card
    tools/sd_sync.py <ip> purge               delete every file in the card root
    tools/sd_sync.py <ip> push [files...]     transcode + upload (default: tracks/*)
    tools/sd_sync.py <ip> rm <name>           delete one file
    tools/sd_sync.py <ip> play <name>         play a file on the castle
    tools/sd_sync.py <ip> bootlog             the device's early-boot log ring
    tools/sd_sync.py <ip> site                push the cue desk page to the card;
                                              the device then serves it at /

WHY TRANSCODE. Playback is whole-file-into-PSRAM (see firmware/sd_audio.h), so
a track must fit ~1.5 MB. The library MP3s are 1.8–3 MB. Rather than reject
them, push re-encodes anything oversized to mono at whatever bitrate makes it
fit with headroom — computed per track from its duration, capped at 96 kbps.
A porch speaker through a MAX98357A does not reveal the difference.

`purge` only touches FILES in the root. Directories (config/, logs/, site/)
are left alone — purge means "clear the music", not "wipe the card".
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The turntable: PSRAM free at load time, minus headroom for everything else
# that allocates from it. Conservative on purpose — a track that fits with
# 300 KB spare always plays; one that fits with 30 KB spare plays until the
# day something else grows.
BUDGET_BYTES = 1_200_000
MAX_KBPS = 96


def api(ip: str, method: str, path: str, body: bytes | None = None,
        timeout: float = 60) -> bytes:
    req = urllib.request.Request(f"http://{ip}{path}", data=body, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def listing(ip: str) -> list[dict]:
    return json.loads(api(ip, "GET", "/api/files"))


def duration_s(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def transcode(src: Path, dst: Path, kbps: int) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ac", "1", "-codec:a", "libmp3lame", "-b:a", f"{kbps}k", str(dst)],
        check=True)


def fit_for_card(src: Path, tmp: Path) -> Path:
    """Return a file that fits the PSRAM budget, transcoding only if needed."""
    size = src.stat().st_size
    if size <= BUDGET_BYTES:
        return src
    dur = duration_s(src)
    # bitrate that lands the file just under budget, floored to something
    # that still sounds like music. The 0.92 covers LAME's habit of landing a
    # few percent over the asked-for rate (frame padding, ID3) — measured, not
    # guessed: 38 kbps asked, 39.9 kbps delivered on a 246 s track.
    kbps = min(MAX_KBPS, max(32, int(BUDGET_BYTES * 8 * 0.92 / dur / 1000)))
    dst = tmp / src.name
    transcode(src, dst, kbps)
    print(f"  transcoded {src.name}: {size//1024} KB -> "
          f"{dst.stat().st_size//1024} KB (mono {kbps} kbps, {dur:.0f}s)")
    if dst.stat().st_size > BUDGET_BYTES:
        raise SystemExit(f"  {src.name}: still {dst.stat().st_size//1024} KB "
                         f"after transcode — track too long for PSRAM")
    return dst


def cmd_push(ip: str, args: list[str]) -> int:
    files = ([Path(a) for a in args] if args
             else sorted(ROOT.glob("tracks/*.mp3")))
    if not files:
        print("nothing to push")
        return 1
    with tempfile.TemporaryDirectory() as td:
        for src in files:
            if not src.exists():
                print(f"  missing: {src}")
                return 1
            up = fit_for_card(src, Path(td))
            data = up.read_bytes()
            quoted = urllib.parse.quote(src.name)
            print(f"  uploading {src.name} ({len(data)//1024} KB) ...",
                  end="", flush=True)
            resp = json.loads(api(ip, "PUT", f"/api/files/{quoted}", data,
                                  timeout=300))
            got = resp.get("bytes", -1)
            if got != len(data):
                print(f" FAILED ({got} of {len(data)} bytes)")
                return 1
            print(" ok")
    print("\ncard now holds:")
    return cmd_ls(ip)


def cmd_ls(ip: str) -> int:
    for f in listing(ip):
        mark = "/" if f["dir"] else f"  {f['size']//1024} KB"
        print(f"  {f['name']}{mark}")
    return 0


def cmd_purge(ip: str) -> int:
    victims = [f["name"] for f in listing(ip) if not f["dir"]]
    if not victims:
        print("card root has no files")
        return 0
    for name in victims:
        api(ip, "DELETE", f"/api/files/{urllib.parse.quote(name)}")
        print(f"  deleted {name}")
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ip, cmd, *args = sys.argv[1:]
    if cmd == "status":
        print(api(ip, "GET", "/api/status").decode())
        return 0
    if cmd == "ls":
        return cmd_ls(ip)
    if cmd == "purge":
        return cmd_purge(ip)
    if cmd == "push":
        return cmd_push(ip, args)
    if cmd == "rm":
        api(ip, "DELETE", f"/api/files/{urllib.parse.quote(args[0])}")
        print(f"deleted {args[0]}")
        return 0
    if cmd == "play":
        api(ip, "POST", f"/api/play?f={urllib.parse.quote(args[0])}")
        print(f"queued {args[0]} — watch logs for the load")
        return 0
    if cmd == "site":
        # The previewer build is ONE self-contained file (see gen_previewer.py)
        # — that constraint, chosen for email-ability, is also exactly what
        # makes it servable by a microcontroller: no asset graph to mirror.
        src = ROOT / "previewer" / "castle-cue-desk.html"
        if not src.exists():
            raise SystemExit("previewer/castle-cue-desk.html missing — run `make preview`")
        data = src.read_bytes()
        print(f"  uploading site ({len(data)//1024} KB) ...", end="", flush=True)
        api(ip, "PUT", "/api/site/index.html", data, timeout=600)
        print(" ok — http://%s/ now serves the cue desk" % ip)
        return 0
    if cmd == "bootlog":
        print(api(ip, "GET", "/api/bootlog").decode())
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
