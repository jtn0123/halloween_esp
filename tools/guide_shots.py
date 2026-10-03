#!/usr/bin/env python3
"""The owner's guide's pictures, made by software: `make guide-shots`.

Every screen docs/OWNER-GUIDE.md shows that a program can draw is drawn
here, from the real thing, and nothing it draws comes from a real castle or
a real network:

- the castle's /owner page, served byte for byte by tools/castle_emu.py
  (three emulated castles: one with songs, one with an empty card, one with
  no card after a brownout);
- the Wi-Fi setup page the castle's hotspot opens on a phone — ESPHome's own
  captive portal page, taken out of the installed esphome (the pinned one
  that builds the firmware) with a made-up /config.json beside it;
- the web flasher, flasher/index.html, beside a stand-in manifest;
- Castle Radio (the Castle Tools window) through tools/guide_shots_radio.py,
  which keeps every connection on loopback and answers Find my castle and
  the firmware offer itself.

A browser (web/guide/shots.ts, Playwright from web/) takes the pictures
into a scratch folder; this module then cuts each one down to a palette PNG
in docs/guide/. `audit()` holds the folder to the guide — every picture the
guide shows exists, every one here is shown, the set is SHOTS and the whole
of it stays under BUDGET_KB — and tests/test_guide_shots.py runs it in
`make test`, so a regenerated or renamed picture cannot silently drop out.

What software cannot draw — a phone's own Wi-Fi list, Windows SmartScreen,
macOS's Privacy & Security pane, the board's BOOT and RESET buttons — is
named in an HTML comment in the guide for whoever has the hardware.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import castle_emu
import unit_label

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
GUIDE = ROOT / "docs" / "OWNER-GUIDE.md"
OUT = ROOT / "docs" / "guide"
SCRIPT = WEB / "guide" / "shots.ts"
BUNDLE = WEB / "dist" / "guide_shots.mjs"

#: Every picture, by file stem, and what it shows. web/guide/shots.ts
#: writes exactly these; audit() holds the guide to exactly these.
SHOTS: dict[str, str] = {
    "portal": "the hotspot's Wi-Fi page on a phone",
    "flasher": "the web flasher",
    "owner-show": "/owner: the show and the songs on the card",
    "owner-settings": "/owner: settings, key and factory reset",
    "owner-report": "/owner: Report a problem, after it saved",
    "owner-empty": "/owner: a card with no songs yet",
    "owner-trouble": "/owner: no card, after a brownout",
    "radio-first-run": "Castle Radio: Listen with no songs yet",
    "radio-import": "Castle Radio: Import music",
    "radio-find": "Castle Radio: Find my castle, one castle found",
    "radio-update": "Castle Radio: the firmware card offering an update",
    "radio-help": "Castle Radio: Help with your castle",
    "radio-key": "Castle Radio: the Castle key card",
}
#: The whole folder, compressed. The guide is read on GitHub, often on a
#: phone: a megabyte and a bit is plenty for thirteen screens.
BUDGET_KB = 1200
#: Palette size for the PNGs: these are flat UI screens, and 64 colours
#: keep text edges clean while cutting each file to a fraction.
COLOURS = 64

#: The castle in the pictures. A made-up station MAC whose name matches the
#: one guide_shots_radio.py's stand-in answers to (castle-a1b2c3).
MAC = bytes.fromhex("a4cf12a1b2c3")
SCENES = ["vigil", "storm", "seance", "ballroom", "crypt"]
SONGS = {
    "Haunted Waltz.mp3": 3_412_000,
    "Ghost Walk.mp3": 2_871_000,
    "Thunder and Rain.mp3": 4_096_000,
}
#: The Wi-Fi networks the phone's setup page lists: (name, signal, locked).
NETWORKS = (
    ("Home Wi-Fi", -48, 1),
    ("Home Wi-Fi Guest", -60, 1),
    ("Next Door 5G", -79, 1),
    ("Coffee Corner", -86, 0),
)
_IMG = re.compile(r"""(?:\]\(|src=["'])guide/([\w-]+)\.png""")


class ShotError(RuntimeError):
    """Why the pictures were not made — one line for the person running it."""


# -- what the stand-in servers serve -------------------------------------------


def esphome_dir() -> Path:
    import esphome

    return Path(esphome.__file__).resolve().parent


def portal_page(root: Path | None = None) -> bytes:
    """ESPHome's captive portal page, as the castle's hotspot serves it: the
    INDEX_GZ array in captive_index.h, unzipped."""
    header = (
        (root or esphome_dir()) / "components" / "captive_portal" / "captive_index.h"
    )
    text = header.read_text(encoding="utf-8")
    m = re.search(r"INDEX_GZ\[\] PROGMEM = \{(.*?)\};", text, re.DOTALL)
    if m is None:
        raise ShotError(f"{header} no longer holds INDEX_GZ — ESPHome moved its page")
    return gzip.decompress(
        bytes(int(b, 16) for b in re.findall(r"0x([0-9a-fA-F]{2})", m[1]))
    )


def portal_config(mac: bytes = MAC) -> dict[str, object]:
    """/config.json as ESPHome's captive portal answers it. The first entry
    of `aps` is skipped by the page (it is the castle's own)."""
    unit = unit_label.from_mac(mac)
    aps: list[dict[str, object]] = [{}]
    aps += [{"ssid": s, "rssi": r, "lock": lock} for s, r, lock in NETWORKS]
    return {"mac": ":".join(f"{b:02X}" for b in mac), "name": unit.name, "aps": aps}


def app_version() -> str:
    conf = json.loads(
        (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    return str(conf["version"])


def site_routes() -> dict[str, tuple[str, bytes]]:
    """path -> (content type, body) for the one static server."""
    manifest = {
        "name": "Halloween Castle",
        "version": f"v{app_version()}",
        "builds": [],
    }
    return {
        "/": ("text/html", portal_page()),
        "/config.json": ("application/json", json.dumps(portal_config()).encode()),
        "/flasher/": ("text/html", (ROOT / "flasher" / "index.html").read_bytes()),
        "/flasher/flasher-manifest.json": (
            "application/json",
            json.dumps(manifest).encode(),
        ),
    }


class Site(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, routes: dict[str, tuple[str, bytes]]) -> None:
        self.routes = routes
        super().__init__(("127.0.0.1", 0), _SiteHandler)


class _SiteHandler(BaseHTTPRequestHandler):
    server: Site

    def do_GET(self) -> None:
        hit = self.server.routes.get(self.path.split("?")[0])
        if hit is None:
            self.send_error(404)
            return
        kind, body = hit
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def seed_card(card: Path, songs: dict[str, int] = SONGS) -> None:
    """Songs of a believable size that take no disk: sparse files."""
    card.mkdir(parents=True, exist_ok=True)
    for name, size in songs.items():
        with (card / name).open("wb") as f:
            f.truncate(size)


# -- the pictures -----------------------------------------------------------------


def compress(raw: Path, dest: Path, colours: int = COLOURS) -> int:
    """A palette PNG of `raw` at `dest`; its size in bytes."""
    with Image.open(raw) as im:
        small = im.convert("RGB").quantize(colours, dither=Image.Dither.NONE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    small.save(dest, optimize=True)
    return dest.stat().st_size


def referenced(guide: Path = GUIDE) -> list[str]:
    """The stems of every docs/guide/ picture the guide shows, in order."""
    return _IMG.findall(guide.read_text(encoding="utf-8"))


def audit(guide: Path = GUIDE, folder: Path = OUT) -> list[str]:
    """What is wrong between the guide, the folder and SHOTS ([] when all is well)."""
    shown = set(referenced(guide))
    have = {p.stem for p in folder.glob("*.png")}
    where = folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder
    problems = [
        f"the guide shows guide/{s}.png, which is not in {where}"
        for s in sorted(shown - have)
    ]
    problems += [
        f"{where}/{s}.png is not shown by the guide" for s in sorted(have - shown)
    ]
    problems += [
        f"{s} is in SHOTS but was never made" for s in sorted(set(SHOTS) - have)
    ]
    problems += [f"{where}/{s}.png is not in SHOTS" for s in sorted(have - set(SHOTS))]
    total = sum(p.stat().st_size for p in folder.glob("*.png"))
    if total > BUDGET_KB * 1024:
        problems.append(
            f"{where} is {total // 1024} KB, over its {BUDGET_KB} KB budget"
        )
    return problems


def node() -> str:
    found = shutil.which("node")
    if found is None:
        raise ShotError(
            "node is not on PATH — install Node 22 or newer (make preflight)"
        )
    return found


def bundle() -> Path:
    esbuild = WEB / "node_modules" / ".bin" / "esbuild"
    if not esbuild.exists():
        raise ShotError("web/ has no node modules — run: cd web && npm ci")
    subprocess.run(
        [str(esbuild), str(SCRIPT.relative_to(WEB)), "--bundle", "--platform=node",
         "--format=esm", "--packages=external", f"--outfile={BUNDLE.relative_to(WEB)}",
         "--log-level=warning"],
        cwd=WEB, check=True,
    )  # fmt: skip
    return BUNDLE


@contextlib.contextmanager
def castles(scratch: Path) -> Iterator[dict[str, castle_emu.CastleEmu]]:
    """The three emulated buyer castles, on loopback, for as long as needed."""
    songs, empty = scratch / "card-songs", scratch / "card-empty"
    seed_card(songs)
    empty.mkdir()
    emus = {
        "songs": castle_emu.CastleEmu(sd_dir=songs, scenes=SCENES, fw_variant="buyer"),
        "empty": castle_emu.CastleEmu(sd_dir=empty, scenes=SCENES, fw_variant="buyer"),
        "trouble": castle_emu.CastleEmu(
            sd_dir=scratch / "card-out", scenes=SCENES, fw_variant="buyer",
            sd_mounted=False, reset_reason=9,
        ),
    }  # fmt: skip
    for emu in emus.values():
        emu.start()
    try:
        yield emus
    finally:
        for emu in emus.values():
            emu.shutdown()
            emu.server_close()


def radio_env(scratch: Path) -> dict[str, str]:
    """The child's environment: scratch data and castle list, no pins."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CASTLE_")}
    env.update(
        CASTLE_RADIO_DATA=str(scratch / "radio"),
        CASTLE_DEVICES=str(scratch / "devices.toml"),
    )
    return env


@contextlib.contextmanager
def radio(scratch: Path, emu_port: int) -> Iterator[str]:
    """Castle Radio on the pretend LAN (guide_shots_radio.py); its base URL."""
    child = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "guide_shots_radio.py"), str(emu_port)],
        env=radio_env(scratch), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )  # fmt: skip
    try:
        assert child.stdout is not None
        port = child.stdout.readline().strip()
        if not port.isdigit():
            raise ShotError(
                "Castle Radio did not start (run tools/guide_shots_radio.py by hand)"
            )
        yield f"http://127.0.0.1:{port}"
    finally:
        child.kill()
        child.wait()
        if child.stdout is not None:
            child.stdout.close()


@contextlib.contextmanager
def site() -> Iterator[str]:
    server = Site(site_routes())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def make(out: Path = OUT) -> dict[str, int]:
    """Every picture in SHOTS, taken and compressed into `out`: stem -> bytes."""
    script = bundle()
    with tempfile.TemporaryDirectory(prefix="guide-shots-") as tmp:
        scratch = Path(tmp)
        raw = scratch / "raw"
        raw.mkdir()
        with castles(scratch) as emus, radio(scratch, emus["songs"].port) as radio_url, \
                site() as site_url:  # fmt: skip
            plan = {
                "out": str(raw),
                "owner": {k: f"http://127.0.0.1:{e.port}" for k, e in emus.items()},
                "radio": radio_url,
                "portal": f"{site_url}/",
                "flasher": f"{site_url}/flasher/",
            }
            (scratch / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            subprocess.run(
                [node(), str(script), str(scratch / "plan.json")], check=True
            )
        missing = [s for s in SHOTS if not (raw / f"{s}.png").is_file()]
        if missing:
            raise ShotError(f"the browser did not make: {', '.join(missing)}")
        return {s: compress(raw / f"{s}.png", out / f"{s}.png") for s in SHOTS}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out", type=Path, default=OUT, help="where the PNGs go (default: docs/guide)"
    )
    ap.add_argument(
        "--check", action="store_true", help="only audit the folder against the guide"
    )
    args = ap.parse_args(argv)
    try:
        if not args.check:
            sizes = make(args.out)
            for stem, size in sizes.items():
                print(f"  {stem + '.png':24} {size // 1024:4d} KB  {SHOTS[stem]}")
            print(f"  {'total':24} {sum(sizes.values()) // 1024:4d} KB of {BUDGET_KB}")
    except (ShotError, subprocess.CalledProcessError, OSError) as exc:
        print(f"guide_shots: {exc}", file=sys.stderr)
        return 1
    problems = audit(GUIDE, args.out)
    for line in problems:
        print(f"guide_shots: {line}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
