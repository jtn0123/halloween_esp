#!/usr/bin/env python3
"""Manage the castle's SD card and firmware from the Mac — the card stays put.

    tools/sd_sync.py [ip|name] status        what firmware, card, volume, scene
    tools/sd_sync.py [ip|name] health        boot/crash counters, last reset
    tools/sd_sync.py [ip|name] ls            list the card
    tools/sd_sync.py [ip|name] purge         delete every file in the card root
    tools/sd_sync.py [ip|name] push [f...]   upload tracks (default: tracks/*)
    tools/sd_sync.py [ip|name] tones         upload the speaker-test tones the
                                             desk's 🏰 panel plays (audio/test -> /sd)
    tools/sd_sync.py [ip|name] cues          upload the card light shows
                                             (audio/card/cues/*.cue -> /sd)
    tools/sd_sync.py [ip|name] scenes        upload the scene tracks the SD
                                             build streams (audio/ -> /sd/scenes)
    tools/sd_sync.py [ip|name] site          push the Castle Radio page (gzipped)
    tools/sd_sync.py [ip|name] ota <bin>     flash firmware over plain HTTP
    tools/sd_sync.py [ip|name] rm <name>     delete one file (scenes/x, site/x too)
    tools/sd_sync.py [ip|name] play <name>   stream a file on the castle
    tools/sd_sync.py [ip|name] bootlog       the device's early-boot log ring
    tools/sd_sync.py [ip|name] logs [file]    fetch /sd/logs/castle.log(.1) and
                                             print the tail (default castle.log)

The device resolves via tools/hosts.py: explicit arg, then CASTLE_HOST, then
devices.toml. Tracks upload AS-IS: playback streams off the card now (see
firmware/sd_web_site.h), so there is no PSRAM ceiling to transcode under.
`purge` only touches FILES in the root — directories (site/, scenes/, logs/)
are left alone; purge means "clear the music", not "wipe the card".
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import build_paths as bp
import sd_ota
from hosts import maybe_host
from published import Published

ROOT = Path(__file__).resolve().parent.parent
SCENES_API = "/api/scenes"  # where the card's scenes/ directory is PUT


def api(
    ip: str, method: str, path: str, body: bytes | None = None, timeout: float = 60
) -> bytes:
    req = urllib.request.Request(f"http://{ip}{path}", data=body, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return bytes(r.read())


def listing(ip: str) -> list[dict]:
    return list(json.loads(api(ip, "GET", "/api/files")))


def upload(ip: str, route: str, name: str, data: bytes, timeout: float = 600) -> None:
    print(f"  uploading {name} ({len(data) // 1024} KB) ...", end="", flush=True)
    resp = json.loads(
        api(ip, "PUT", f"{route}/{urllib.parse.quote(name)}", data, timeout=timeout)
    )
    got = resp.get("bytes", -1)
    if got != len(data):
        raise SystemExit(f" FAILED ({got} of {len(data)} bytes)")
    # v5.42+ answers with a CRC32 of what actually hit the card — "bytes
    # matched" cannot see a bad SD sector (grade report 2026-08-23 B5). Older firmware
    # omits the field; nothing to compare then.
    said = resp.get("crc32")
    want = zlib.crc32(data)
    if said is not None and int(str(said), 16) != want:
        raise SystemExit(
            f" FAILED (crc mismatch: card wrote {said}, "
            f"sent {want:08x} — bad sector or corrupt transfer)"
        )
    print(" ok")


def card_dir(ip: str, d: str) -> dict[str, int]:
    """name -> size inside one card directory (v5.42 /api/files?d=), or {}
    when the firmware predates the parameter — then nothing is skippable."""
    try:
        rows = json.loads(api(ip, "GET", f"/api/files?d={urllib.parse.quote(d)}"))
    except OSError:
        return {}
    if not isinstance(rows, list):
        return {}
    return {
        f["name"]: int(f["size"])
        for f in rows
        if isinstance(f, dict) and "name" in f and not f.get("dir")
    }


def _card_bytes_match(ip: str, name: str, data: bytes, d: str = "") -> bool:
    """True when GET /sd/<d><name> is the same bytes we would PUT."""
    try:
        remote = api(ip, "GET", f"/sd/{d}{urllib.parse.quote(name)}")
    except OSError:
        return False
    return remote == data


def _scene_bytes_match(ip: str, name: str, data: bytes) -> bool:
    return _card_bytes_match(ip, name, data, "scenes/")


def _scene_unchanged(
    ip: str, name: str, data: bytes, have: dict[str, int], rec: Published
) -> bool:
    """Is /sd/scenes/<name> already exactly these bytes?

    Cheapest evidence first: a size that disagrees settles it, then the
    record of what we last PUT to THIS host (tools/published.py), and only
    then the byte compare that costs a whole file over Wi-Fi. Whatever the
    slow path learns is recorded, so the next publish is cheap."""
    if have.get(name) != len(data):
        return False
    key = f"scenes/{name}"
    if rec.matches(key, data):
        return True
    if _scene_bytes_match(ip, name, data):
        rec.record(key, data)
        return True
    rec.forget(key)
    return False


def cmd_ota(ip: str, args: list[str]) -> int:
    """The flash itself is tools/sd_ota.py; it gets this module's `api` and
    ROOT, so one mock (and one CASTLE redirect) still covers both files."""
    return sd_ota.flash(ip, args, api, ROOT)


def cmd_push(ip: str, args: list[str]) -> int:
    files = [Path(a) for a in args] if args else sorted(ROOT.glob("tracks/*.mp3"))
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
    """The whole show into /sd/scenes/: audio, cue files and the manifest.

    Since v5.67 a scene is card data — `<id>.cue` for its timeline and one row
    in `show.man` for the numbers a generated script used to carry — so this
    is the command that makes a scene edit a PUBLISH rather than a flash
    (tools/gen_scene_cards.py writes all three under audio/card/scenes/).

    ORDER MATTERS, and it is audio, then cues, then the manifest, because the
    manifest is what the castle reads to know a scene exists at all: a reboot
    landing halfway through this push finds a manifest naming only scenes
    whose files are already there, never one promising a cue file that has not
    arrived. A stale cue file is swept only after the new manifest has landed,
    for the mirror-image reason. The old firmware is safe either way — v5.66
    runs compiled scripts and simply ignores the three new file kinds.
    """
    # audio/card/ holds the card_bitrate copies when scenes.yaml asks for a
    # different one; audio/ itself is the smaller render — 32 kbps, sized for
    # the all-in-flash build that used to embed it (retired 2026-09-01,
    # PROJECT_NOTES §12.15) and kept since as what the desk page inlines.
    # Prefer the card copies either way: pushing a 32 kbps compromise onto a
    # 31 GB card would be paying a price nothing charges any more.
    card = sorted(bp.AUDIO.glob("card/[0-9][0-9]_*.mp3"))
    files = card or sorted(
        p for p in bp.AUDIO.glob("[0-9][0-9]_*.mp3") if not p.name.startswith("00_")
    )
    if not files:
        raise SystemExit("no audio/NN_*.mp3 — run `make audio` first")
    print(f"  source: {bp.rel(files[0].parent)}/")
    have = card_dir(ip, "scenes")
    rec = Published(ip)
    sent = 0
    for src in files:
        data = src.read_bytes()
        # Same name, same size, same hash as we last sent this host: the same
        # render — a full ten-scene push is minutes over porch WiFi, and
        # publish (the studio runs this after every scene save) must not pay
        # that every time, nor pull 8 MB back to prove it need not.
        if _scene_unchanged(ip, src.name, data, have, rec):
            print(f"  {src.name} unchanged, skipped")
            continue
        upload(ip, SCENES_API, src.name, data)
        rec.record(f"scenes/{src.name}", data)
        sent += 1
    print(
        f"  {len(files)} scene tracks in /sd/scenes/ ({sent} sent, "
        f"{len(files) - sent} already there)"
    )
    return _push_show(ip, have, rec)


def _push_show(ip: str, have: dict[str, int], rec: Published) -> int:
    """The show itself: every `<id>.cue`, then `show.man`, then the sweep.

    Stale cue files are DELETED rather than left: a renamed scene's old file
    is invisible to the runner (nothing names it) but it is in /api/files, on
    the card, and in the way of the next person reading the directory.
    gen_scene_cards.write already sweeps the publish directory the same way.

    The sweep goes AFTER the manifest, not before (grade report 2026-09-17 pm
    D4): while the OLD show.man is still on the card it still names `gone`,
    and a reboot or a PIR trip in that window would arm a scene whose cue
    file we had already deleted — audio and no lights. Deleting a file the
    new manifest does not name can strand nothing.
    """
    src_dir = bp.AUDIO / "card" / "scenes"
    cues = sorted(src_dir.glob("*.cue"))
    manifest = src_dir / "show.man"
    if not cues or not manifest.exists():
        print("  no show.man or .cue files — run `make generate` first")
        return 1
    sent = 0
    for src in cues:
        data = src.read_bytes()
        if _scene_unchanged(ip, src.name, data, have, rec):
            print(f"  {src.name} unchanged, skipped")
            continue
        upload(ip, SCENES_API, src.name, data)
        rec.record(f"scenes/{src.name}", data)
        sent += 1
    # Unconditionally, and before any delete: it is 16 + 96·n bytes, and it is
    # the file that decides what the castle believes about every one of the
    # others — so it is the file that makes a stale cue unreachable.
    upload(ip, SCENES_API, manifest.name, manifest.read_bytes())
    keep = {src.name for src in cues}
    for name in sorted(have):
        if name.endswith(".cue") and name not in keep:
            print(f"  {name}: no scene of that name any more — deleting")
            api(ip, "DELETE", f"{SCENES_API}/{urllib.parse.quote(name)}")
            rec.forget(f"scenes/{name}")
    rec.save()
    # No reboot line any more: since v5.69 the castle re-reads show.man
    # itself when this PUT lands (J1, grade report 2026-09-17 pm —
    # castle_web::g_scenes_dirty into `seed_scene_ids`), so a scene published
    # here is startable on the castle that is already running.
    print(
        f"  {len(cues)} cue files ({sent} sent) + show.man — the castle "
        "re-reads the ids on its own"
    )
    return 0


def cmd_cues(ip: str) -> int:
    """The card light shows tools/render_cues.py wrote, beside their songs.

    A cue file is only ever loaded for a song that is on the card under the
    same name, so one whose song is not there is said out loud rather than
    pushed: it would sit on the card lighting nothing."""
    files = sorted(bp.AUDIO.glob("card/cues/*.cue"))
    if not files:
        raise SystemExit(
            "no audio/card/cues/*.cue — tools/render_cues.py <track> first"
        )
    have = {f["name"]: int(f["size"]) for f in listing(ip) if not f.get("dir")}
    songs = {name.rsplit(".", 1)[0] for name in have if not name.endswith(".cue")}
    sent = 0
    for src in files:
        data = src.read_bytes()
        if src.stem not in songs:
            print(f"  {src.name}: no song called {src.stem} on the card — skipped")
            continue
        if have.get(src.name) == len(data) and _card_bytes_match(ip, src.name, data):
            print(f"  {src.name} unchanged, skipped")
            continue
        upload(ip, "/api/files", src.name, data)
        sent += 1
    print(f"  {len(files)} cue files, {sent} sent")
    return 0


def cmd_tones(ip: str) -> int:
    """The 🏰 panel's speaker test: five tones at the card ROOT, because
    /api/play takes one path component. `make audio` renders them."""
    files = sorted(bp.AUDIO.glob("test/test_*.mp3"))
    if not files:
        raise SystemExit("no audio/test/test_*.mp3 — run `make audio` first")
    for src in files:
        upload(ip, "/api/files", src.name, src.read_bytes())
    print(f"  {len(files)} test tones in /sd/ — the desk's speaker test is live")
    return 0


def build_site() -> bytes:
    """The one-file Castle Radio page, built by demo/castle-radio/device_site.py
    (loaded by path: the demo is its own program, not a tools/ module)."""
    source = ROOT / "demo" / "castle-radio" / "device_site.py"
    if not source.exists():
        raise SystemExit(
            f"{source} missing — the Castle Radio demo is the castle's page"
        )
    spec = importlib.util.spec_from_file_location("device_site", source)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(source.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(source.parent))
    return bytes(module.build())


def library_size(page: bytes) -> int:
    """How many imported songs the built page carries inline."""
    found = re.search(
        rb'<script id="radio-library"[^>]*>(.*?)</script>', page, re.DOTALL
    )
    return len(json.loads(found.group(1))) if found else 0


def cmd_site(ip: str) -> int:
    # ONE self-contained file — that constraint is what makes it servable by
    # a microcontroller. Pushed pre-gzipped: the firmware serves index.html.gz
    # with Content-Encoding, and the first load is one request.
    #
    # Since 2026-09-15 this is the Castle Radio control room (demo/castle-radio),
    # not the cue desk: the card keeps the desk's last build as index.old.html
    # for the day it is wanted back. Scene audio is not pushed beside the page;
    # the page streams it from /sd/scenes/, where `scenes` already put it.
    plain = build_site()
    songs = library_size(plain)
    if songs == 0:
        # Not an error — a castle with only scenes is a real castle — but it
        # was once a silent one: the page went out from a checkout with no
        # .radio-data and every imported song on the card lost its row.
        print(
            "  WARNING: this page lists NO imported songs — this checkout has "
            "no demo/castle-radio/.radio-data/catalog.json. Songs already on "
            "the card show up only if they have a .cue beside them."
        )
    packed = gzip.compress(plain, 9)
    upload(ip, "/api/site", "index.html.gz", packed)
    # The plain copy too, for any client that cannot take gzip — the firmware
    # prefers .gz but falls back, and a stale pair would be worse than bytes.
    upload(ip, "/api/site", "index.html", plain)
    print(
        f"  http://{ip}/ now serves Castle Radio "
        f"({len(packed) // 1024} KB gzipped, {len(plain) // 1024} KB plain)"
    )
    return 0


def cmd_ls(ip: str) -> int:
    for f in listing(ip):
        mark = "/" if f["dir"] else f"  {f['size'] // 1024} KB"
        print(f"  {f['name']}{mark}")
    return 0


def delete_route(name: str) -> str:
    """`rm scenes/x.mp3` reaches the scenes directory through its own route:
    v5.47 registers DELETE where PUT already was (grade report 2026-09-06
    J4), and the root route refuses a '/' in the name."""
    for sub in ("scenes", "site"):
        if name.startswith(sub + "/"):
            return f"/api/{sub}/{urllib.parse.quote(name[len(sub) + 1 :])}"
    return f"/api/files/{urllib.parse.quote(name)}"


#: L9 (v5.62): the card's own log, oldest rotation first. The castle has
#: written one line per boot since v5.44 and, since v5.62, the tail of the
#: previous life's event ring underneath it — and nothing ever fetched it,
#: so the one record that survives a crash was only readable by pulling the
#: card. It has been HTTP-readable the whole time (sd_web_site.h).
LOG_FILES = ("logs/castle.log.1", "logs/castle.log")
#: Lines printed after the save. The whole file goes to disk; this is the
#: part you read standing in the hall with a laptop.
TAIL_LINES = 40


def cmd_logs(ip: str, args: list[str]) -> int:
    out = Path(args[0]) if args else ROOT / "castle.log"
    text = ""
    for name in LOG_FILES:
        # A constant path, not one built from anything the castle said: the
        # rule for every URL in this file.
        try:
            text += api(ip, "GET", f"/sd/{name}").decode("utf-8", "replace")
        except OSError as e:
            # castle.log.1 only exists after the first rotation (~200 KB),
            # so its absence is the normal case and not a failure.
            print(f"  {name}: {e}")
    if not text.strip():
        print("no log on the card — has this castle booted with it in the slot?")
        return 1
    out.write_text(text)
    lines = text.splitlines()
    print(f"saved {len(lines)} lines to {out}\n")
    print("\n".join(lines[-TAIL_LINES:]))
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
    if cmd == "cues":
        return cmd_cues(ip)
    if cmd == "tones":
        return cmd_tones(ip)
    if cmd == "site":
        return cmd_site(ip)
    if cmd == "ota":
        return cmd_ota(ip, args)
    if cmd == "rm":
        api(ip, "DELETE", delete_route(args[0]))
        print(f"deleted {args[0]}")
        return 0
    if cmd == "play":
        api(ip, "POST", f"/api/play?f={urllib.parse.quote(args[0])}")
        print(f"queued {args[0]} — it streams off the card")
        return 0
    if cmd == "logs":
        return cmd_logs(ip, args)
    if cmd == "bootlog":
        print(api(ip, "GET", "/api/bootlog").decode())
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
