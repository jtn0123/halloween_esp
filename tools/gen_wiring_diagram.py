#!/usr/bin/env python3
"""Castle wiring schematic, drawn to the real Feather header layout.

Pin order and GPIO numbers are from Adafruit's own pinout PDF for the
Feather ESP32-S2 (Adafruit-Feather-ESP32-S2-PCB). Both headers are drawn in
full and in physical order so a pin can be COUNTED on the board rather than
looked up: 16 positions on the left, 12 on the right, the right one starting
four positions down from the USB end, both ending flush at the far end.

Grounds use the ground symbol rather than a return rail. Routing every ground
to one bar meant eight long verticals crossing most of the drawing; the
symbol is the conventional answer and the "all grounds are common" point is
made in the callouts instead.

Regenerate after editing:  python3 tools/gen_wiring_diagram.py
Rewrites the <svg> body of docs/castle-wiring.html in place; the prose and
styles around it are hand-written in that file and survive the splice.
"""

from __future__ import annotations

import pathlib
import sys

from wiring_svg import A, S, box, cap, dots, esc, gnd_sym, net, resistor, wire

W, H = 1380, 1030
RAIL = 56  # +5 V bus
SPUR = 676  # 5 V spur feeding the amplifiers

# ── +5 V rail ────────────────────────────────────────────────────────────
net(
    "v5",
    f'<line class="w w--v5" x1="52" y1="{RAIL}" x2="{W - 46}" y2="{RAIL}" stroke-width="5"/>'
    f'<text class="raillbl raillbl--v5" x="56" y="{RAIL - 14}">+5 V BUS</text>',
)

# ── THE FEATHER, to the real header layout ───────────────────────────────
FX, FY, FW, FH = 740, 104, 244, 578
LH, RH = FX, FX + FW  # left / right header columns
P0, PITCH = 196, 30  # position 1 centre, 0.1in pitch


def py(pos: int) -> int:
    return P0 + (pos - 1) * PITCH  # header position 1..16


LEFT = [
    (1, "RST", ""),
    (2, "3.3V", ""),
    (3, "3.3V", ""),
    (4, "GND", ""),
    (5, "A0", "18"),
    (6, "A1", "17"),
    (7, "A2", "16"),
    (8, "A3", "15"),
    (9, "A4", "14"),
    (10, "A5", "8"),
    (11, "SCK", "36"),
    (12, "MOSI", "35"),
    (13, "MISO", "37"),
    (14, "RX", "38"),
    (15, "TX", "39"),
    (16, "DEBUG_TX", ""),
]
RIGHT = [
    (5, "BAT", ""),
    (6, "EN", ""),
    (7, "USB", ""),
    (8, "D13", "13"),
    (9, "D12", "12"),
    (10, "D11", "11"),
    (11, "D10", "10"),
    (12, "D9", "9"),
    (13, "D6", "6"),
    (14, "D5", "5"),
    (15, "SCL", "4"),
    (16, "SDA", "3"),
]
USE = {
    "A0": "dL",
    "A2": "dD",
    "A4": "dR",
    "A3": "i2s",
    "D11": "i2s",
    "D12": "i2s",
    "USB": "v5",
    "GND": "gnd",
}
CLAIM = {
    "D5": "SD CS",
    "D6": "SRAM CS",
    "D9": "eInk CS",
    "D10": "eInk D/C",
    "SCK": "wing",
    "MOSI": "wing",
    "MISO": "wing",
    "A1": "PIR",
    "D13": "red LED",
}

A(box(FX, FY, FW, FH, "ESP32-S2 Feather", "eInk + SD wing stacked"))
A(
    f'<rect class="usbc" x="{FX + FW / 2 - 26}" y="{FY - 13}" width="52" height="16" rx="5"/>'
    f'<text class="pinsub" x="{FX + FW / 2}" y="{FY - 19}" text-anchor="middle">USB-C</text>'
)
# Header strips, so the two columns read as headers rather than loose pads.
A(
    f'<rect class="hdr" x="{LH - 9}" y="{py(1) - 14}" width="18" height="{15 * PITCH + 28}" rx="6"/>'
)
A(
    f'<rect class="hdr" x="{RH - 9}" y="{py(5) - 14}" width="18" height="{11 * PITCH + 28}" rx="6"/>'
)


def header(items: list[tuple[int, str, str]], col: int, inward: str) -> None:
    dx, anch = (14, "start") if inward == "right" else (-14, "end")
    for pos, name, gpio in items:
        y = py(pos)
        n = USE.get(name)
        cls = f"pad--{n}" if n else ("pad--claim" if name in CLAIM else "pad--free")
        A(f'<circle class="pad {cls}" cx="{col}" cy="{y}" r="5"/>')
        A(
            f'<text class="hpin{" hpin--on" if n else ""}" x="{col + dx}" y="{y - 2}" '
            f'text-anchor="{anch}">{esc(name)}</text>'
        )
        note = f"GPIO{gpio}" if gpio else ""
        if name in CLAIM:
            note = (note + " · " if note else "") + CLAIM[name]
        if note:
            A(
                f'<text class="hgpio" x="{col + dx}" y="{y + 11}" text-anchor="{anch}">{esc(note)}</text>'
            )
        # Position number down the outside — this is what you count.
        A(
            f'<text class="hnum" x="{col - (20 if dx > 0 else -20)}" y="{y + 4}" '
            f'text-anchor="middle">{pos}</text>'
        )


header(LEFT, LH, "right")
header(RIGHT, RH, "left")
A(
    f'<text class="note" x="{FX + 16}" y="{FY + FH - 16}">Both headers end flush here</text>'
)

net("v5", wire([(RH, py(7)), (1040, py(7)), (1040, RAIL)], "v5"))
net("gnd", wire([(LH, py(4)), (LH - 44, py(4))], "gnd", width=3))
A(gnd_sym(LH - 44, py(4)))

# ── Level shifter — inputs face the Feather, outputs face the fixtures ───
SX, SY, SW, SH = 484, 288, 142, 186
A(box(SX, SY, SW, SH, "74AHCT125", "quad buffer"))
A(f'<path class="notch" d="M{SX + SW / 2 - 11} {SY} a11 11 0 0 0 22 0"/>')
A(
    f'<text class="note" x="{SX + SW / 2}" y="{SY + 86}" text-anchor="middle">3.3 V in → 5 V out</text>'
    f'<text class="note" x="{SX + SW / 2}" y="{SY + 102}" text-anchor="middle">pin map far right →</text>'
    f'<text class="note" x="{SX + 15}" y="{SY + 178}">4OE→5 V, 4A→GND</text>'
)
net(
    "v5",
    wire([(SX + SW / 2, SY), (SX + SW / 2, RAIL)], "v5")
    + f'<circle class="pad pad--v5" cx="{SX + SW / 2}" cy="{SY}" r="5"/>'
    + f'<text class="pinsub" x="{SX + SW / 2 + 11}" y="{SY - 11}">pin 14</text>',
)
A(gnd_sym(SX + SW / 2, SY + SH))
A(
    f'<text class="pinsub" x="{SX + SW / 2 + 15}" y="{SY + SH + 20}">pin 7 · 1OE 2OE 3OE</text>'
)

# ── The chip itself — what the schematic's 74AHCT125 box looks like in
# your hand. Pins 1-7 down the left, 14-8 back up the right, notch at top.
IX, IY, IW, IP = 1120, 128, 72, 24
DIPL = [
    ("1OE", "gnd"),
    ("1A", "dL"),
    ("1Y", "dL"),
    ("2OE", "gnd"),
    ("2A", "dD"),
    ("2Y", "dD"),
    ("GND", "gnd"),
]  # pins 1..7
DIPR = [
    ("VCC", "v5"),
    ("4OE", "v5"),
    ("4A", "gnd"),
    ("4Y", None),
    ("3OE", "gnd"),
    ("3A", "dR"),
    ("3Y", "dR"),
]  # pins 14..8
IH = 7 * IP + 20
A(
    f'<text class="modttl" x="{IX + IW / 2}" y="{IY - 32}" text-anchor="middle">THE CHIP ITSELF</text>'
)
A(
    f'<text class="modsub" x="{IX + IW / 2}" y="{IY - 16}" text-anchor="middle">74AHCT125 · top view</text>'
)
A(f'<rect class="mod" x="{IX}" y="{IY}" width="{IW}" height="{IH}" rx="8"/>')
A(f'<path class="notch" d="M{IX + IW / 2 - 9} {IY} a9 9 0 0 0 18 0"/>')
for i in range(7):
    yl = IY + 22 + i * IP
    for onright, (nm, netid), num in ((0, DIPL[i], i + 1), (1, DIPR[i], 14 - i)):
        bx = IX + IW if onright else IX - 9
        tx, anch = (IX + IW + 16, "start") if onright else (IX - 16, "end")
        nx = IX + IW + 42 if onright else IX - 42
        stub = f'<rect class="{"pad--" + netid if netid else "rpk"}" x="{bx}" y="{yl - 3}" width="9" height="6"/>'
        num_t = f'<text class="hnum" x="{tx}" y="{yl + 4}" text-anchor="{anch}">{num}</text>'
        name = (
            f'<text class="pinsub" x="{nx}" y="{yl + 4}" text-anchor="{anch}"'
            + (f' style="fill:var(--{netid})"' if netid else "")
            + f">{esc(nm)}{'' if netid else ' · n/c'}</text>"
        )
        A(
            f'<g class="net" data-net="{netid}">{stub}{num_t}{name}</g>'
            if netid
            else stub + num_t + name
        )
CAP14 = [
    "Hold it notch-up — the indent",
    "at the top: pin 1 is top-left;",
    "numbers run down the left side,",
    "1-7, back up the right, 8-14.",
    "Some chips add a dot at pin 1.",
]
for i, ln in enumerate(CAP14):
    A(
        f'<text class="note" x="{IX + IW / 2}" y="{IY + IH + 24 + i * 15}" text-anchor="middle">{esc(ln)}</text>'
    )

# ── Fixtures ─────────────────────────────────────────────────────────────
BX, BW, BH = 124, 300, 140
V5B = 28
# The rig as built for first light: two Jewels in the towers, the Ring 12
# in the doorway. scenes/scenes.yaml `zones:` is the source of truth.
# vx staggers the three verticals in the channel between the fixtures and
# the shifter; each 470 Ω pill then centres on the last horizontal run into
# DIN — the fixture end of the cable, where the guide says it belongs.
FIX = [
    (104, "Tower L", "dL", "jewel", "Jewel 7 · RGBW", 478, py(5)),
    (296, "Doorway", "dD", "ring12", "Ring 12 · RGBW", 470, py(7)),
    (488, "Tower R", "dR", "jewel", "Jewel 7 · RGBW", 462, py(9)),
]
for by, name, n, kind, capt, vx, sy in FIX:
    cy = by + BH / 2
    A(box(BX, by, BW, BH, name, capt))
    A(f'<g class="pxg">{dots(BX + 206, cy + 10, kind)}</g>')
    A(f'<circle class="pad pad--{n}" cx="{BX + BW}" cy="{cy}" r="5"/>')
    A(
        f'<text class="pinlbl" x="{BX + BW - 12}" y="{cy - 8}" text-anchor="end">DIN</text>'
    )
    # 5 V down the outside, ground to a symbol under each fixture.
    net(
        "v5",
        f'<circle class="pad pad--v5" cx="{BX}" cy="{by + 44}" r="5"/>'
        + wire([(BX, by + 44), (V5B, by + 44)], "v5")
        + f'<text class="pinlbl" x="{BX + 12}" y="{by + 36}">5 V</text>',
    )
    A(f'<circle class="pad pad--gnd" cx="{BX}" cy="{by + 96}" r="5"/>')
    A(f'<text class="pinlbl" x="{BX + 12}" y="{by + 88}">GND</text>')
    net("gnd", wire([(BX, by + 96), (V5B + 28, by + 96)], "gnd"))
    A(gnd_sym(V5B + 28, by + 96))
    # Reservoir cap straddling the pair, right where it has to be fitted.
    A(f'<g class="net" data-net="v5">{cap(V5B + 72, by + 44, by + 96)}</g>')
    # Feather → shifter → 470 Ω → DIN
    net(
        n,
        wire([(LH, sy), (SX + SW, sy)], n)
        + f'<circle class="pad pad--{n}" cx="{SX + SW}" cy="{sy}" r="5"/>'
        + wire([(SX, sy), (vx, sy), (vx, cy), (BX + BW, cy)], n)
        + f'<circle class="pad pad--{n}" cx="{SX}" cy="{sy}" r="5"/>'
        + f'<g class="res">{resistor((vx + BX + BW) / 2, cy, "470 Ω")}</g>',
    )
net("v5", wire([(V5B, RAIL), (V5B, py(9) + BH / 2 - 52)], "v5"))
for i, sy in enumerate((py(5), py(7), py(9))):
    A(
        f'<text class="pinsub" x="{SX + SW + 11}" y="{sy - 10}">{["2 · 1A", "5 · 2A", "9 · 3A"][i]}</text>'
    )
    A(
        f'<text class="pinsub" x="{SX - 11}" y="{sy - 10}" text-anchor="end">{["1Y · 3", "2Y · 6", "3Y · 8"][i]}</text>'
    )

# ── Amplifiers ───────────────────────────────────────────────────────────
# Each I2S signal is drawn as a BUS: one horizontal, a junction dot where it
# drops into an amplifier. That is what the wiring is — the second amp taps
# the same three wires — and it draws far cleaner than a point-to-point link
# between the two boards.
AY, AH, AW = 792, 128, 190
SPUR = 754
AMP_A, AMP_B = 470, 900
# Pads placed for clean routing; the breakout's own silkscreen order is
# printed on each module so nothing is ambiguous at the soldering iron.
PADS_A = {
    "DIN": 492,
    "LRC": 516,
    "BCLK": 540,
    "GAIN": 564,
    "SD": 588,
    "GND": 612,
    "VIN": 636,
}
PADS_B = {
    "DIN": 922,
    "LRC": 946,
    "BCLK": 970,
    "GAIN": 994,
    "SD": 1018,
    "GND": 1042,
    "VIN": 1066,
}

for ax, nm, pads in ((AMP_A, "Amp A", PADS_A), (AMP_B, "Amp B", PADS_B)):
    A(box(ax, AY, AW, AH, nm))
    A(
        f'<text class="modsub fb-only" x="{ax + 16}" y="{AY + 45}">MAX98357A · left · SD pinned</text>'
        f'<text class="modsub fl-only" x="{ax + 16}" y="{AY + 45}">MAX98357A · (L+R)/2 · full level</text>'
    )
    for p_, x_ in pads.items():
        c = {
            "LRC": "i2s",
            "BCLK": "i2s",
            "DIN": "i2s",
            "SD": "v5",
            "VIN": "v5",
            "GND": "gnd",
            "GAIN": "free",
        }[p_]
        A(f'<circle class="pad pad--{c}" cx="{x_}" cy="{AY}" r="4.5"/>')
        A(
            f'<text class="amppin" x="{x_}" y="{AY + 16}" text-anchor="middle">{p_}</text>'
        )
    net("v5", wire([(pads["VIN"], AY), (pads["VIN"], SPUR)], "v5"))
    net(
        "v5",
        '<g class="res">'
        + wire([(pads["SD"], AY), (pads["SD"], SPUR)], "v5", width=2.4)
        + resistor(
            pads["SD"],
            (AY + SPUR) / 2,
            "100 kΩ opt",
            True,
            pads["VIN"] + 8,
            (AY + SPUR) / 2 + 4,
        )
        + "</g>",
    )
    net("gnd", wire([(pads["GND"], AY + AH), (pads["GND"], AY + AH + 16)], "gnd"))
    A(gnd_sym(pads["GND"], AY + AH + 16))
    # Speaker, bridge-tied: both terminals swing, neither is ground.
    sx = ax + AW
    A(
        f'<path class="spk" d="M{sx + 70} {AY + 30} l0 68 -30 -20 -16 0 0 -28 16 0 z"/>'
        f'<path class="spkarc" d="M{sx + 80} {AY + 36} a26 26 0 0 1 0 56"/>'
        f'<text class="modsub" x="{sx + 18}" y="{AY + 122}">4 Ω · 3 W</text>'
    )
    net(
        "spk",
        wire([(sx, AY + 50), (sx + 40, AY + 50)], "spk", width=2.6)
        + wire([(sx, AY + 82), (sx + 40, AY + 82)], "spk", width=2.6)
        + f'<circle class="pad pad--spk" cx="{sx}" cy="{AY + 50}" r="4.5"/>'
        + f'<circle class="pad pad--spk" cx="{sx}" cy="{AY + 82}" r="4.5"/>'
        + f'<text class="pinsub" x="{sx - 10}" y="{AY + 46}" text-anchor="end">+</text>'
        + f'<text class="pinsub" x="{sx - 10}" y="{AY + 87}" text-anchor="end">\u2212</text>',
    )

A(
    f'<text class="note" x="{AMP_A}" y="{AY + AH + 58}">The breakout\u2019s own pin row reads LRC BCLK DIN GAIN SD GND VIN; pads are placed here for routing.</text>'
)
A(
    f'<text class="note fb-only" x="{AMP_A}" y="{AY + AH + 74}">GAIN left open = 9 dB. SD \u2192 5 V via 100 k\u03a9 pins the LEFT channel \u2014 optional insurance, not a fix; the default is already full level.</text>'
)
A(
    f'<text class="note fl-only" x="{AMP_A}" y="{AY + AH + 74}">GAIN left open = 9 dB. SD left EMPTY = (L+R)/2 \u2014 and ESPHome\u2019s mono duplicates both slots, so that IS full level.</text>'
)

# 5 V spur, hopping every signal drop that crosses it.
drops = sorted(
    [PADS_A[k] for k in ("DIN", "LRC", "BCLK")]
    + [PADS_B[k] for k in ("DIN", "LRC", "BCLK")]
)
net(
    "v5",
    wire([(1320, RAIL), (1320, SPUR), (AMP_A - 24, SPUR)], "v5", hops=tuple(drops)),
)
A(f'<text class="raillbl raillbl--v5" x="1322" y="{SPUR + 5}">5 V SPUR</text>')

# ── The I2S bus ──────────────────────────────────────────────────────────
# DIN comes off the LEFT header, BCLK and LRC off the RIGHT one. That split
# is the whole reason to draw this to the real pin locations: the bundle is
# three wires from two opposite edges of the board.
BUS = [
    ("DIN", 702, "left", py(8), 712),
    ("BCLK", 720, "right", py(10), 1150),
    ("LRC", 738, "right", py(9), 1180),
]
for sig, ch, side, sy, feed in BUS:
    xa, xb = PADS_A[sig], PADS_B[sig]
    head = LH if side == "left" else RH
    net("i2s", wire([(head, sy), (feed, sy), (feed, ch)], "i2s", width=2.6))
    A("")  # the old `or ""` appended an empty line here; the SVG keeps it
    lo, hi = min(xa, feed), max(xb, feed)
    others = [PADS_A[k] for k in ("DIN", "LRC", "BCLK") if k != sig] + [
        PADS_B[k] for k in ("DIN", "LRC", "BCLK") if k != sig
    ]
    hop = tuple(x for x in others if lo < x < hi)
    net(
        "i2s",
        wire([(lo, ch), (hi, ch)], "i2s", width=2.6, hops=hop)
        + wire([(xa, ch), (xa, AY)], "i2s", width=2.6)
        + wire([(xb, ch), (xb, AY)], "i2s", width=2.6)
        + f'<circle class="jn" cx="{xa}" cy="{ch}" r="4"/>'
        + f'<circle class="jn" cx="{xb}" cy="{ch}" r="4"/>'
        + f'<text class="buslbl" x="{lo - 8}" y="{ch + 4}" text-anchor="end">{sig}</text>',
    )

# ── Splice into the page ────────────────────────────────────────────────
# The SVG body is replaced in docs/castle-wiring.html in place; everything
# around it (styles, net list, warnings, build order) is hand-written and
# lives only in the HTML. Non-ASCII becomes numeric entities to match the
# rest of the file, which declares no charset when served bare.
OPEN_MARK = 'amplifiers connected between them.">'
svg = "\n".join(S).encode("ascii", "xmlcharrefreplace").decode()
# An argv path lets the smoke test splice into a COPY; without one this
# writes the real page, as `make`/hand runs always have.
page = (
    pathlib.Path(sys.argv[1])
    if len(sys.argv) > 1
    else pathlib.Path(__file__).resolve().parents[1] / "docs" / "castle-wiring.html"
)
html = page.read_text()
start = html.index(OPEN_MARK) + len(OPEN_MARK)
end = html.index("</svg>", start)
page.write_text(html[:start] + "\n" + svg + "\n" + html[end:])
print(f"castle-wiring.html: schematic replaced ({len(svg)} chars)")
