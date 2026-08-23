#!/usr/bin/env python3
"""Manage the castle's SD card and firmware from the Mac — the card stays put.

    tools/sd_sync.py [ip|name] status        what firmware, card, volume, scene
    tools/sd_sync.py [ip|name] health        boot/crash counters, last reset
    tools/sd_sync.py [ip|name] ls            list the card
    tools/sd_sync.py [ip|name] purge         delete every file in the card root
    tools/sd_sync.py [ip|name] push [f...]   upload tracks (default: tracks/*)
    tools/sd_sync.py [ip|name] tones         upload the speaker-test tones the
                                             desk's 🏰 panel plays (audio/test -> /sd)
    tools/sd_sync.py [ip|name] scenes        upload the 8 scene tracks the SD
                                             build streams (audio/ -> /sd/scenes)
    tools/sd_sync.py [ip|name] site          push the cue desk page (gzipped)
    tools/sd_sync.py [ip|name] ota <bin>     flash firmware over plain HTTP
    tools/sd_sync.py [ip|name] rm <name>     delete one file
    tools/sd_sync.py [ip|name] play <name>   stream a file on the castle
    tools/sd_sync.py [ip|name] bootlog       the device's early-boot log ring

The device resolves via tools/hosts.py: explicit arg, then CASTLE_HOST, then
devices.toml. Tracks upload AS-IS: playback streams off the card now (see
firmware/sd_web_site.h), so there is no PSRAM ceiling to transcode under.
`purge` only touches FILES in the root — directories (site/, scenes/, logs/)
are left alone; purge means "clear the music", not "wipe the card".
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from hosts import maybe_host

ROOT = Path(__file__).resolve().parent.parent


def api(ip: str, method: str, path: str, body: bytes | None = None,
        timeout: float = 60) -> bytes:
    req = urllib.request.Request(f"http://{ip}{path}", data=body, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return bytes(r.read())


def listing(ip: str) -> list[dict]:
    return list(json.loads(api(ip, "GET", "/api/files")))


def upload(ip: str, route: str, name: str, data: bytes, timeout: float = 600) -> None:
    print(f"  uploading {name} ({len(data)//1024} KB) ...", end="", flush=True)
    resp = json.loads(api(ip, "PUT", f"{route}/{urllib.parse.quote(name)}",
                          data, timeout=timeout))
    got = resp.get("bytes", -1)
    if got != len(data):
        raise SystemExit(f" FAILED ({got} of {len(data)} bytes)")
    print(" ok")


def cmd_push(ip: str, args: list[str]) -> int:
    files = ([Path(a) for a in args] if args
             else sorted(ROOT.glob("tracks/*.mp3")))
    if not files:
        print("nothing to push")
        return 1
    for src in files:
        if not src.exists():
            print(f"  missing: {src}")
            return 1
        upload(ip, "/api/files", src.name, src.read_bytes())
    print("\ncard now holds:")
    return cmd_ls(ip)


def cmd_scenes(ip: str) -> int:
    """The show's own audio, to where the streaming sfx expects it."""
    # audio/card/ holds the card_bitrate copies when scenes.yaml asks for a
    # different one; audio/ itself is what the FLASH build embeds and is
    # deliberately smaller. Prefer the card copies — pushing the flash ones
    # would put a 32 kbps compromise onto a 31 GB card.
    card = sorted(ROOT.glob("audio/card/[0-9][0-9]_*.mp3"))
    files = card or sorted(p for p in ROOT.glob("audio/[0-9][0-9]_*.mp3")
                           if not p.name.startswith("00_"))
    if not files:
        raise SystemExit("no audio/NN_*.mp3 — run `make audio` first")
    print(f"  source: {files[0].parent.relative_to(ROOT)}/")
    for src in files:
        upload(ip, "/api/scenes", src.name, src.read_bytes())
    print(f"  {len(files)} scene tracks in /sd/scenes/")
    return 0


def cmd_tones(ip: str) -> int:
    """The 🏰 panel's speaker test: five tones at the card ROOT, because
    /api/play takes one path component. `make audio` renders them."""
    files = sorted(ROOT.glob("audio/test/test_*.mp3"))
    if not files:
        raise SystemExit("no audio/test/test_*.mp3 — run `make audio` first")
    for src in files:
        upload(ip, "/api/files", src.name, src.read_bytes())
    print(f"  {len(files)} test tones in /sd/ — the desk's speaker test is live")
    return 0


def cmd_site(ip: str) -> int:
    # ONE self-contained file (see gen_previewer.py) — that constraint is
    # what makes it servable by a microcontroller. Pushed pre-gzipped: the
    # firmware serves index.html.gz with Content-Encoding and the first load
    # drops from ~8 s to ~3 s over the porch WiFi.
    src = ROOT / "previewer" / "castle-cue-desk.html"
    if not src.exists():
        raise SystemExit("previewer/castle-cue-desk.html missing — run `make preview`")
    plain = src.read_bytes()
    packed = gzip.compress(plain, 9)
    upload(ip, "/api/site", "index.html.gz", packed)
    # The plain copy too, for any client that cannot take gzip — the firmware
    # prefers .gz but falls back, and a stale pair would be worse than bytes.
    upload(ip, "/api/site", "index.html", plain)
    print(f"  http://{ip}/ now serves the cue desk "
          f"({len(packed)//1024} KB gzipped, {len(plain)//1024} KB plain)")
    return 0


def cmd_ota(ip: str, args: list[str]) -> int:
    if not args:
        # The compiled image lives wherever the build put it; find the newest.
        cands = sorted(ROOT.glob("firmware/.esphome/build/**/firmware.bin"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            raise SystemExit("no firmware.bin found — pass a path or build first")
        args = [str(cands[0])]
    bin_path = Path(args[0])
    data = bin_path.read_bytes()
    if data[:1] != b"\xe9":
        raise SystemExit(f"{bin_path} does not look like an app image (no 0xE9 magic)")
    print(f"  flashing {bin_path.name} ({len(data)//1024} KB) over HTTP ...",
          end="", flush=True)
    try:
        resp = json.loads(api(ip, "PUT", "/api/ota", data, timeout=180))
        print(" ok" if resp.get("flashed") else f" UNEXPECTED: {resp}")
    except OSError:
        # The device reboots moments after the last byte lands; losing the
        # response race is normal, not failure. The status poll below is the
        # real verdict.
        print(" (no reply — device likely rebooting)")
    print("  waiting for the device to come back ...", end="", flush=True)
    import time
    for _ in range(30):
        time.sleep(3)
        try:
            st = json.loads(api(ip, "GET", "/api/status", timeout=3))
            print(f" up — v{st.get('version')} compiled {st.get('compiled')}")
            print("  now CONFIRM it (connect once with tools/device.py or HA) —"
                  " an unconfirmed image rolls back on its next reboot")
            return 0
        except OSError:
            print(".", end="", flush=True)
    print(" no answer after 90 s — if it stays down, the bootloader will"
          " roll back to the previous image on the next power cycle")
    return 1


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
    ip, rest = maybe_host(sys.argv[1:])
    if not rest:
        print(__doc__)
        return 2
    cmd, *args = rest
    if cmd in ("status", "health"):
        print(api(ip, "GET", f"/api/{cmd}").decode())
        return 0
    if cmd == "ls":
        return cmd_ls(ip)
    if cmd == "purge":
        return cmd_purge(ip)
    if cmd == "push":
        return cmd_push(ip, args)
    if cmd == "scenes":
        return cmd_scenes(ip)
    if cmd == "tones":
        return cmd_tones(ip)
    if cmd == "site":
        return cmd_site(ip)
    if cmd == "ota":
        return cmd_ota(ip, args)
    if cmd == "rm":
        api(ip, "DELETE", f"/api/files/{urllib.parse.quote(args[0])}")
        print(f"deleted {args[0]}")
        return 0
    if cmd == "play":
        api(ip, "POST", f"/api/play?f={urllib.parse.quote(args[0])}")
        print(f"queued {args[0]} — it streams off the card")
        return 0
    if cmd == "bootlog":
        print(api(ip, "GET", "/api/bootlog").decode())
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
