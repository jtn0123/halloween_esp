"""SVG drawing kit for the wiring schematic.

Split from gen_wiring_diagram.py at the 500-line cap along the seam that
was already there: these are the drawing primitives — wires with hop-overs,
ground symbols, packages, pixel dots — and the accumulator they write into.
What connects to what stays next door in gen_wiring_diagram.py.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

S: list[str] = []
A = S.append


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def net(name: str, body: str) -> None:
    A(f'<g class="net" data-net="{name}">{body}</g>')


def wire(
    pts: Sequence[tuple[float, float]],
    cls: str,
    hops: Sequence[float] = (),
    width: float = 3.2,
) -> str:
    d: list[str] = []
    for i, (x, y) in enumerate(pts):
        if i == 0:
            d.append(f"M{x} {y}")
            continue
        px, py = pts[i - 1]
        if abs(py - y) < 0.5 and hops:
            for hx in sorted(
                [h for h in hops if min(px, x) + 8 < h < max(px, x) - 8],
                reverse=px > x,
            ):
                b = -1 if px > x else 1
                d.append(f"L{hx - 7 * b} {y}")
                d.append(f"A7 7 0 0 {1 if b > 0 else 0} {hx + 7 * b} {y}")
        d.append(f"L{x} {y}")
    return (
        f'<path class="w w--{cls}" d="{" ".join(d)}" fill="none" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def gnd_sym(x: float, y: float) -> str:
    """Ground symbol: three shortening bars. Every one of these is the same
    net — see the 'One ground' callout."""
    return (
        f'<g class="net" data-net="gnd">'
        f'<line class="w w--gnd" x1="{x}" y1="{y}" x2="{x}" y2="{y + 13}" stroke-width="3"/>'
        f'<line class="gb" x1="{x - 13}" y1="{y + 13}" x2="{x + 13}" y2="{y + 13}"/>'
        f'<line class="gb" x1="{x - 8}" y1="{y + 18}" x2="{x + 8}" y2="{y + 18}"/>'
        f'<line class="gb" x1="{x - 3}" y1="{y + 23}" x2="{x + 3}" y2="{y + 23}"/></g>'
    )


def box(
    x: float, y: float, w: float, h: float, title: str, sub: str = "", cls: str = "mod"
) -> str:
    return (
        f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="10"/>'
        f'<text class="modttl" x="{x + 16}" y="{y + 27}">{esc(title)}</text>'
        + (
            f'<text class="modsub" x="{x + 16}" y="{y + 45}">{esc(sub)}</text>'
            if sub
            else ""
        )
    )


def dots(cx: float, cy: float, kind: str) -> str:
    o: list[str] = []
    if kind == "jewel":
        o.append(f'<circle class="px" cx="{cx}" cy="{cy}" r="8"/>')
        o.extend(
            f'<circle class="px" cx="{cx + math.cos(a) * 29:.1f}" cy="{cy + math.sin(a) * 29:.1f}" r="8"/>'
            for a in (-math.pi / 2 + i * math.tau / 6 for i in range(6))
        )
    elif kind.startswith("ring"):
        n = int(kind[4:])
        rad = 41 if n >= 16 else 38
        for i in range(n):
            a = -math.pi / 2 + i * math.tau / n
            o.append(
                f'<circle class="px" cx="{cx + math.cos(a) * rad:.1f}" cy="{cy + math.sin(a) * rad:.1f}" r="6.5"/>'
            )
    else:
        o.extend(
            f'<circle class="px" cx="{cx - 77 + c * 22 + 11}" cy="{cy - 33 + r * 22 + 11}" r="7"/>'
            for r in range(4)
            for c in range(8)
        )
    return "".join(o)


def resistor(
    x: float,
    y: float,
    label: str = "",
    vert: bool = False,
    lx: float | None = None,
    ly: float | None = None,
) -> str:
    r = (
        f'<rect class="rpk" x="{x - 7}" y="{y - 15}" width="14" height="30" rx="3"/>'
        if vert
        else f'<rect class="rpk" x="{x - 15}" y="{y - 7}" width="30" height="14" rx="3"/>'
    )
    if label:
        anc = "start" if lx is not None else "middle"
        r += (
            f'<text class="rlbl" x="{lx if lx is not None else x}" '
            f'y="{ly if ly is not None else y - 13}" text-anchor="{anc}">{esc(label)}</text>'
        )
    return r


def cap(x: float, y0: float, y1: float) -> str:
    m = (y0 + y1) / 2
    return (
        f'<line class="cwire" x1="{x}" y1="{y0}" x2="{x}" y2="{m - 5}"/>'
        f'<line class="cpl" x1="{x - 13}" y1="{m - 5}" x2="{x + 13}" y2="{m - 5}"/>'
        f'<line class="cpl" x1="{x - 13}" y1="{m + 5}" x2="{x + 13}" y2="{m + 5}"/>'
        f'<line class="cwire" x1="{x}" y1="{m + 5}" x2="{x}" y2="{y1}"/>'
    )
