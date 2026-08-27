#!/usr/bin/env python3
"""Fixture geometry — the Python half of web/src/rig.ts.

The desk decides what a chase looks like on a Ring 16 by asking a `Layout`
where each pixel sits. The firmware has to answer the same question the same
way or the preview stops predicting the castle, which is the one thing the
preview is for.

Rather than write the maths a third time in C++, this module computes it once
and `gen_esphome.py` bakes the answers into `firmware/generated/rig.h` as
plain tables. The device then does no layout arithmetic at all — it indexes.
That leaves exactly two implementations to keep in step, TypeScript and this
one, and `tests/test_rig_layout.py` checks them against each other.

Kept deliberately dependency-free so the parity test can import it directly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Pixel counts and shapes of the fixtures in the box. Mirrors FIXTURES in
#: web/src/rig.ts; `rgb_only` is a purchasing fact, not a geometric one, so it
#: lives there rather than here.
FIXTURES: dict[str, tuple[int, str, int, int, int]] = {
    # id      -> (pixels, kind, cols, rows, max_count)
    #
    # max_count is 0 for everything the part itself decides. A Ring 16 has
    # sixteen pixels and no `pixels:` in scenes.yaml can make it have twenty;
    # only the loose mini PCBs take a count, because only they are a pile of
    # singles you choose how many of. Mirrors `maxCount` in web/src/rig.ts,
    # and the two must agree or a zone renders one length in the desk and
    # another on the castle.
    "jewel7": (7, "hub", 0, 0, 0),
    "stick8": (8, "line", 0, 0, 0),
    "ring12": (12, "ring", 0, 0, 0),
    "ring16": (16, "ring", 0, 0, 0),
    "wing32": (32, "grid", 8, 4, 0),
    "mini": (3, "scatter", 0, 0, 5),
    "none": (0, "scatter", 0, 0, 0),
}


@dataclass(frozen=True)
class Layout:
    """Where a fixture's pixels are, and how motion crosses them.

    `walk` and `fall` are normalised to 0..1 so an overlay never has to know
    whether it is travelling six pixels or thirty-two: see the `Layout` doc
    comment in web/src/rig.ts for the full argument.
    """

    n: int
    center: int | None
    walk: tuple[float, ...]
    fall: tuple[float, ...]
    fall_steps: int
    core: tuple[bool, ...]
    pos: tuple[tuple[float, float], ...]


def _ring(n: int) -> tuple[list[tuple[float, float]], list[float], list[float]]:
    pos, walk, fall = [], [], []
    for i in range(n):
        a = -math.pi / 2 + (i / n) * math.tau  # pixel 0 at 12 o'clock
        pos.append((0.5 + math.cos(a) * 0.42, 0.5 + math.sin(a) * 0.42))
        walk.append(i / n)
        # Top to bottom, so a drip reads as gravity and not as a lap.
        fall.append((math.sin(a) + 1) / 2)
    return pos, walk, fall


def layout_of(fixture_id: str, count: int | None = None) -> Layout:
    """The geometry of one fixture. `count` applies to scatter fixtures only."""
    if fixture_id not in FIXTURES:
        raise SystemExit(f"unknown fixture {fixture_id!r}")
    base, kind, cols, rows, max_count = FIXTURES[fixture_id]
    n = base
    if max_count:
        n = max(1, min(max_count, base if count is None else count))
    elif count is not None and count != base:
        raise SystemExit(
            f"fixture {fixture_id!r} has {base} pixels, not {count} — remove the "
            "`pixels:` override or name a different fixture"
        )
    return build(n, kind, cols, rows)


def build(n: int, kind: str, cols: int = 0, rows: int = 0) -> Layout:
    """Geometry from a raw shape, for fixtures the catalogue does not name —
    the legacy `pixels_per_zone` build being the one that matters."""
    pos: list[tuple[float, float]] = []
    walk: list[float] = []
    fall: list[float] = []
    center: int | None = None

    if n == 0:
        pass
    elif kind == "hub":
        center = 0
        pos.append((0.5, 0.5))
        walk.append(0.0)
        fall.append(0.0)
        rp, rw, rf = _ring(n - 1)
        pos += rp
        walk += rw
        fall += rf
    elif kind == "ring":
        pos, walk, fall = _ring(n)
    elif kind == "grid":
        grid_rows = rows or math.ceil(n / cols)
        for i in range(n):
            cx, cy = i % cols, i // cols
            pos.append(((cx + 0.5) / cols, (cy + 0.5) / grid_rows))
            # Serpentine by column, so a chase sweeps rather than snapping
            # back to the left edge at the end of every row.
            up = cx % 2 == 1
            walk.append((cx + (grid_rows - 1 - cy if up else cy) / grid_rows) / cols)
            fall.append(0.0 if grid_rows == 1 else cy / (grid_rows - 1))
    else:  # line, scatter
        spread = 0.88 if kind == "line" else 0.76
        edge = 0.06 if kind == "line" else 0.12
        for i in range(n):
            pos.append((0.5 if n == 1 else edge + (i / (n - 1)) * spread, 0.5))
            walk.append(i / n)
            fall.append(0.0 if n == 1 else i / (n - 1))

    # Rounded before counting, so floating-point noise in the ring's sine does
    # not report sixteen heights where the eye sees nine.
    fall_steps = len({f"{v:.3f}" for v in fall})

    core = [False] * n
    if center is not None:
        core[center] = True
    elif n > 0:
        # Rounded before sorting so that a ring's notionally-equal distances
        # really are equal and the index breaks the tie. Unrounded, the last
        # bits of the two languages' hypot disagreed and they picked different
        # pixels — see web/test/rig_parity.ts, which is what found it.
        order = sorted(
            range(n),
            key=lambda i: (round(math.hypot(pos[i][0] - 0.5, pos[i][1] - 0.5), 6), i),
        )
        for i in order[: max(1, round(n / 7))]:
            core[i] = True

    return Layout(
        n=n,
        center=center,
        walk=tuple(walk),
        fall=tuple(fall),
        fall_steps=fall_steps,
        core=tuple(core),
        pos=tuple(pos),
    )


def zone_layouts(zones: Sequence[Mapping[str, Any]], per: int) -> dict[str, Layout]:
    """Each zone's geometry, from its own `fixture:` or the legacy fallback.

    A zone that names no fixture is the pre-rig castle: `pixels_per_zone`
    identical Jewels. Keeping that path is what lets an unmodified scenes.yaml
    still build.
    """
    out: dict[str, Layout] = {}
    for z in zones:
        fx = z.get("fixture")
        if fx:
            out[z["id"]] = layout_of(fx, z.get("pixels"))
        else:
            out[z["id"]] = _uniform(per)
    return out


def _uniform(per: int) -> Layout:
    """The legacy shape. Seven pixels is a Jewel — centre plus a ring of six,
    which is what every scene was written against. Any other count is the
    8 mm through-hole build, which is just a row of lamps."""
    return layout_of("jewel7") if per == 7 else build(per, "line")


def _dump() -> str:
    """Every catalogue layout as JSON, for the cross-language parity test.

    Floats are rounded to 6 places on both sides: this is geometry feeding a
    visual, so agreeing to the last bit of a double is not the property worth
    testing — agreeing on where the pixels are is.
    """
    import json

    def r6(v: float) -> float:
        return round(v, 6)

    out = {}
    for fid in FIXTURES:
        lay = layout_of(fid)
        out[fid] = {
            "n": lay.n,
            "center": lay.center,
            "walk": [r6(v) for v in lay.walk],
            "fall": [r6(v) for v in lay.fall],
            "fallSteps": lay.fall_steps,
            "core": list(lay.core),
            "pos": [[r6(x), r6(y)] for x, y in lay.pos],
        }
    return json.dumps(out, sort_keys=True)


if __name__ == "__main__":
    print(_dump())
