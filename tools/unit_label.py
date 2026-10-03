#!/usr/bin/env python3
"""The unit label — one printable sheet for one castle (PRODUCTION-TODO §3).

    unit_label.py --mac A4CF12B2C3D4 [--unit CASTLE-2026-001]
    unit_label.py --host castle-b2c3d4.local [--unit ...]

It writes castle-label-<name>.html in the current folder; run it where the
sheet should land.

One self-contained HTML file, nothing fetched when it opens: a 4 x 6 in card
for the box and a 2.25 x 1.25 in sticker for the castle's base, each its own
printed page (CSS named pages), so a label printer can take the sticker
alone. What they say is what the buyer build makes of this castle's MAC:

  * the setup hotspot, `Castle-` + the MAC's last two bytes in upper-case
    hex (firmware/castle_buyer.h name_the_ap) — and that it has NO
    password: castle_buyer.yaml's `ap:` names none, on purpose;
  * the castle's name, `castle-` + the MAC's last three bytes in lower-case
    hex, `.local` (castle_buyer.yaml `name: castle` + ESPHome's
    `name_add_mac_suffix`) — its page, and /owner, the page with settings;
  * the web flasher, for a castle that will not start or needs its Wi-Fi
    again over USB;
  * a QR code to the owner's guide on GitHub (tools/qr_code.py — no
    dependency, nothing for a buyer to install).

tests/test_unit_label.py reads the firmware's C and YAML and the installed
ESPHome's own source, so a change to either derivation fails there before a
label can disagree with the castle it is stuck to.

`--mac` takes the MAC as `esptool.py read_mac` prints it (any of `:` `-` `.`
or none between the digits). `--host` asks a castle on this network instead:
its /api/status must answer as a castle of the buyer build, and its name
comes from the host itself when that is `castle-xxxxxx[.local]`, else from a
one-shot mDNS browse (tools/castle_find.py) that names the address. The
lines for docs/SUPPORT.md's per-unit record are printed either way.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import castle_find
import qr_code

#: firmware/castle_buyer.h name_the_ap(): snprintf("Castle-%02X%02X", mac[4], mac[5]).
AP_PREFIX = "Castle-"
AP_BYTES = (4, 5)
#: castle_buyer.yaml's `name` substitution, and ESPHome's separator
#: (core/config.py _APP_NAME_MAC_SEP) before the MAC's last six hex digits.
NAME_STEM = "castle"
NAME_SEP = "-"
SUFFIX_BYTES = (3, 4, 5)
#: The web flasher (flasher/index.html, deployed by .github/workflows/pages.yml).
FLASHER_URL = "https://jtn0123.github.io/halloween_esp/"
#: Where the Castle Tools app (songs, updates) is downloaded.
APP_URL = "https://github.com/jtn0123/halloween_esp/releases/latest"
#: The owner's guide as GitHub renders it — what the QR code opens.
GUIDE_URL = "https://github.com/jtn0123/halloween_esp/blob/main/docs/OWNER-GUIDE.md"
#: What /api/status calls the build a buyer's castle runs (castle_buyer.yaml).
BUYER_VARIANT = "buyer"
#: Where --host's mDNS question goes; tests aim it at a fake on loopback.
MDNS_GROUP: tuple[str, int] | None = None

_NAMED = re.compile(rf"^{NAME_STEM}{NAME_SEP}([0-9a-f]{{6}})(?:\.local)?\.?(?::\d+)?$")


class LabelError(RuntimeError):
    """Why no label was made, in a sentence."""


@dataclass(frozen=True)
class Unit:
    """What a label is made from: the MAC's last three bytes (all the two
    names use), and what else is known — the whole MAC, the castle's own
    word on its firmware, the seller's serial."""

    suffix: bytes
    mac: bytes | None = None
    version: str = ""
    board: str = ""
    serial: str = ""

    @property
    def hotspot(self) -> str:
        mac = bytes(3) + self.suffix
        return AP_PREFIX + "".join(f"{mac[i]:02X}" for i in AP_BYTES)

    @property
    def name(self) -> str:
        return NAME_STEM + NAME_SEP + self.suffix.hex()

    @property
    def page(self) -> str:
        return f"http://{self.name}.local"  # NOSONAR — the castle serves no TLS


def parse_mac(text: str) -> bytes:
    """Six bytes from twelve hex digits, with or without separators."""
    digits = re.sub(r"[:\-.\s]", "", text.strip())
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", digits):
        raise LabelError(
            f"{text!r} is not a MAC address — twelve hex digits, as "
            "`esptool.py read_mac` prints them"
        )
    return bytes.fromhex(digits)


def from_mac(mac: bytes, serial: str = "") -> Unit:
    return Unit(suffix=bytes(mac[i] for i in SUFFIX_BYTES), mac=mac, serial=serial)


def _name_suffix(name: str) -> bytes | None:
    m = _NAMED.match(name.strip().lower())
    return bytes.fromhex(m.group(1)) if m else None


def _ip(address: str) -> str:
    return address.rsplit(":", 1)[0] if address.count(":") == 1 else address


def from_host(
    host: str,
    serial: str = "",
    ask: Callable[[str], dict[str, Any] | None] = castle_find.probe,
    group: tuple[str, int] | None = None,
    timeout: float = 2.0,
) -> Unit:
    """The castle at `host`, asked: its status, then its name."""
    state = ask(host)
    if state is None:
        raise LabelError(
            f"Nothing at {host} answered as a castle — is it on, and on this network?"
        )
    variant = str(state.get("fw_variant") or "")
    if variant != BUYER_VARIANT:
        raise LabelError(
            f"The castle at {host} runs the {variant or 'pre-v5.74'} build, which "
            "has no setup hotspot and no castle-xxxxxx name — flash the buyer "
            "build first (the web flasher, or `make build-buyer`)"
        )
    suffix = _name_suffix(host)
    if suffix is None:
        found = castle_find.candidates(castle_find.browse(timeout, group or MDNS_GROUP))
        names = [n for addr, n in found.items() if _ip(addr) == _ip(host)]
        suffix = next((s for s in map(_name_suffix, names) if s), None)
    if suffix is None:
        raise LabelError(
            f"The castle at {host} answered, but not with its castle-xxxxxx name "
            "over mDNS — give --mac (esptool.py read_mac prints it) or its .local name"
        )
    return Unit(
        suffix=suffix,
        version=str(state.get("version") or ""),
        board=str(state.get("board") or ""),
        serial=serial,
    )


def record(unit: Unit) -> str:
    """Lines for the per-unit record, in docs/SUPPORT.md's template's words —
    the ones this tool knows; the rest of the record is the seller's."""
    lines = []
    if unit.serial:
        lines.append(f"Unit:            {unit.serial}")
    if unit.mac:
        lines.append(f"MAC:             {':'.join(f'{b:02X}' for b in unit.mac)}")
    lines += [
        f"Setup hotspot:   {unit.hotspot}   (open — no password)",
        f"Castle name:     {unit.name}.local",
    ]
    if unit.board:
        lines.append(f"Board / carrier: {unit.board}")
    if unit.version:
        lines.append(f"Firmware:        castle v{unit.version}")
    return "\n".join(lines)


STYLE = """
@page { size: 4in 6in; margin: 0; }
@page sticker { size: 2.25in 1.25in; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; color: #111;
  font: 10pt/1.3 system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
.card { width: 4in; height: 6in; padding: .28in .3in; display: flex; flex-direction: column;
  gap: .1in; break-after: page; overflow: hidden; }
.sticker { page: sticker; width: 2.25in; height: 1.25in; padding: .08in .1in;
  display: flex; gap: .08in; align-items: center; overflow: hidden; }
h1 { font-size: 17pt; margin: 0; letter-spacing: .01em; }
.serial { font-size: 8pt; color: #555; }
h2 { font-size: 9pt; text-transform: uppercase; letter-spacing: .08em; margin: 0 0 .03in;
  color: #444; }
.big { font: 700 15pt/1.15 ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
.mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-weight: 600;
  overflow-wrap: anywhere; }
.box { border: 1.5pt solid #111; border-radius: 6pt; padding: .07in .1in; }
.small { font-size: 8pt; color: #333; }
.guide { display: flex; gap: .12in; align-items: center; margin-top: auto; }
.guide svg { flex: none; width: 1.45in; height: 1.45in; }
.sticker svg { flex: none; width: 1.05in; height: 1.05in; }
.sticker .big { font-size: 10pt; }
.sticker .mono { font-size: 7.5pt; word-break: normal; }
.sticker .small { font-size: 6.5pt; }
@media screen { body { background: #ddd; padding: 16px; }
  .card, .sticker { background: #fff; margin: 0 auto 16px; box-shadow: 0 1px 6px #0004; } }
"""


def page(unit: Unit, guide_url: str = GUIDE_URL) -> str:
    """The label sheet: the card, then the sticker."""
    e = html.escape
    qr_card = qr_code.svg(
        qr_code.encode(guide_url.encode(), "M"), label="Owner's guide"
    )
    serial = f'<div class="serial">{e(unit.serial)}</div>' if unit.serial else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Castle label — {e(unit.name)}</title>
<style>{STYLE}</style></head><body>
<section class="card">
  <div><h1>Halloween Castle</h1>{serial}</div>
  <div class="box">
    <h2>First time: its Wi-Fi setup</h2>
    <div class="big">{e(unit.hotspot)}</div>
    <div>No password — it is an open network.</div>
    <div class="small">Join it from a phone and a setup page opens; pick your home
      Wi-Fi there. It comes back by itself if your Wi-Fi ever changes.</div>
  </div>
  <div>
    <h2>Its page, on your Wi-Fi</h2>
    <div class="mono">{e(unit.page)}</div>
    <div class="small">Settings: <span class="mono">{e(unit.page)}/owner</span></div>
  </div>
  <div>
    <h2>Repair or set up over USB-C</h2>
    <div class="mono">{e(FLASHER_URL)}</div>
    <div class="small">In Chrome or Edge on a computer.</div>
  </div>
  <div>
    <h2>Songs and updates: Castle Tools</h2>
    <div class="mono">{e(APP_URL).replace("/releases", "<wbr>/releases")}</div>
    <div class="small">For a Windows PC, or a Mac with Apple silicon (M1 or newer).</div>
  </div>
  <div class="guide">{qr_card}
    <div><h2>Owner's guide</h2><div class="small">Scan for the guide: setting up,
      the castle's page, updates, and what to do when something is wrong.</div></div>
  </div>
</section>
<section class="sticker">{qr_card}
  <div><div class="big">{e(unit.hotspot)}</div><div class="small">setup Wi-Fi, no password</div>
    <div class="mono">{e(unit.name)}<wbr>.local</div><div class="small">guide: scan</div></div>
</section>
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--mac", help="the castle's MAC (esptool.py read_mac)")
    which.add_argument("--host", help="ask a castle on this network instead")
    ap.add_argument("--unit", default="", help="your serial for it, printed small")
    ap.add_argument("--guide-url", default=GUIDE_URL, help="what the QR code opens")
    args = ap.parse_args(argv)
    try:
        if args.mac:
            unit = from_mac(parse_mac(args.mac), args.unit)
        else:
            unit = from_host(args.host, args.unit)
    except (LabelError, OSError) as exc:
        print(f"unit_label: {exc}", file=sys.stderr)
        return 1
    # One component, never a path: the name is castle-<hex> by construction,
    # and basename keeps it so whatever --host was told.
    out = Path(os.path.basename(f"castle-label-{unit.name}.html"))
    out.write_text(page(unit, args.guide_url), encoding="utf-8")
    print(record(unit))
    print(
        f"label: {out} — print page 1 on 4x6 in card, page 2 on a 2.25x1.25 in sticker"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
