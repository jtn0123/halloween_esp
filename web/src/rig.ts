/**
 * The rig — which physical fixture is in which of the three spots.
 *
 * Until now "a zone" meant "a NeoPixel Jewel": seven pixels, one in the
 * middle and six around it, three times over, hardcoded from scenes.yaml down
 * to the render loop. That was true of the castle and false of the parts box,
 * which also holds Sticks, Rings, a 4x8 FeatherWing and a bag of single mini
 * PCBs. Trying one of those in a window meant editing four files and
 * reflashing to find out it looked wrong.
 *
 * So the fixture becomes data. Everything downstream — the pixel view, the
 * overlays, the channel strip, the generated YAML — asks this module what is
 * actually in the spot rather than assuming. Changing a fixture is then a
 * click, and the reflash only happens once you like what you see.
 *
 * The two constraints that come from the hardware and cannot be softened:
 *
 *   - RGBW pixels are 32 bits and RGB pixels are 24, so a chain can only ever
 *     be all one or all the other. Per-zone data pins are what let a
 *     RGBW Jewel and an RGB ring coexist. See docs/WIRING.md §1.
 *   - The 5 V supply has to survive a lightning cue, which drives every zone
 *     to full white at once. `rigPower()` is that number, not the average.
 */

import type { ZoneId } from "./types.js";

export const ZONE_ORDER: readonly ZoneId[] = ["towerL", "door", "towerR"];

/**
 * The data pin each zone is wired to, one per zone rather than one chain.
 *
 * towerL keeps GPIO18 because that is what is already soldered; the other two
 * are the pins left free once the eInk FeatherWing has taken D5/D6/D9/D10 and
 * SPI. Separate pins are what make a mixed rig possible at all — see
 * docs/WIRING.md §1 and §2 for the full budget and why these three.
 */
export const ZONE_PIN: Record<ZoneId, number> = { towerL: 18, door: 16, towerR: 14 };

/** Declaration order, which is what the generators and the firmware index by.
 *  Not the same as ZONE_ORDER, which is the order they stand on the porch. */
export const ZONE_DECL: readonly ZoneId[] = ["towerL", "towerR", "door"];

/** How a fixture's pixels sit in space, which is what the overlays need to
 *  know before they can travel across one. */
export type LayoutKind = "hub" | "ring" | "line" | "grid" | "scatter";

export interface Fixture {
  id: string;
  /** Short label for the picker. */
  name: string;
  /** What it is, for the tooltip and the generated comment. */
  full: string;
  count: number;
  layout: LayoutKind;
  /** Columns × rows, `grid` only. */
  cols?: number;
  rows?: number;
  /** true when Adafruit only ever made it RGB — the choice is not yours. */
  rgbOnly?: boolean;
  /** How many you own. Assigning more than this is flagged, not blocked. */
  own: number;
  /** Scatter fixtures take a count; this is the ceiling. */
  maxCount?: number;
}

/**
 * The parts actually in the box. Counts and RGB/RGBW facts are from the
 * Adafruit product pages — the FeatherWing (#2945) and the mini PCBs are
 * 24-bit only, everything else ships in both variants, which is why
 * `rgbOnly` is the exception rather than a field on every row.
 */
export const FIXTURES: readonly Fixture[] = [
  { id: "jewel7", name: "Jewel 7", full: "NeoPixel Jewel — 7 × 5050, centre + 6 ring",
    count: 7, layout: "hub", own: 2 },
  { id: "stick8", name: "Stick 8", full: "NeoPixel Stick — 8 × 5050 in a row",
    count: 8, layout: "line", own: 1 },
  { id: "ring12", name: "Ring 12", full: "NeoPixel Ring — 12 × 5050, no centre",
    count: 12, layout: "ring", own: 1 },
  { id: "ring16", name: "Ring 16", full: "NeoPixel Ring — 16 × 5050, no centre",
    count: 16, layout: "ring", own: 1 },
  { id: "wing32", name: "Wing 4×8", full: "NeoPixel FeatherWing — 4×8 matrix, RGB only",
    count: 32, layout: "grid", cols: 8, rows: 4, rgbOnly: true, own: 1 },
  { id: "mini", name: "Mini PCB", full: "NeoPixel mini PCB singles, RGB only",
    count: 3, layout: "scatter", rgbOnly: true, own: 5, maxCount: 5 },
  { id: "none", name: "(empty)", full: "Nothing wired to this channel yet",
    count: 0, layout: "scatter", own: 3 },
];

export const fixture = (id: string): Fixture =>
  FIXTURES.find((f) => f.id === id) ?? FIXTURES[FIXTURES.length - 1]!;

/**
 * Where every pixel of a fixture sits, and how it moves.
 *
 * `walk` and `fall` are the whole point. Rather than each overlay knowing
 * about rings and grids, every layout answers two questions in the same
 * units: where is this pixel along the loop a chase travels (0..1, wrapping),
 * and where is it along the path a meteor falls (0 at the top, 1 at the far
 * end). A ring answers with its angle, a grid with its row, a stick with its
 * index — and `applyOverlay` never learns the difference.
 */
export interface Layout {
  n: number;
  /** The pixel that plays the centre role, or null where there is no middle.
   *  A Ring 12 genuinely has no centre, and pretending index 0 is one puts
   *  the ember core in a random spot on the circle. */
  center: number | null;
  /** 0..1 around the chase loop. */
  walk: readonly number[];
  /** 0..1 down the meteor's fall. */
  fall: readonly number[];
  /** How many distinct heights `fall` resolves to — a 4×8 grid has 4, a stick
   *  of 8 has 8. It is what sets how tall a falling head should be, so the
   *  drip looks the same size on every fixture instead of a smear on one and
   *  a single dot on another. */
  fallSteps: number;
  /** The pixels a "centre" strike lands on: the middle pixel where there is
   *  one, otherwise the innermost seventh — the middle 2×2 of the FeatherWing,
   *  the top pixel of a ring. Never empty on a fixture with pixels, because a
   *  strike mode that lights nothing reads as a broken cue. */
  core: readonly boolean[];
  /** Draw positions in a unit square, y down, already inset from the edges. */
  pos: ReadonlyArray<readonly [number, number]>;
}

type Pt = readonly [number, number];

const ring = (n: number, out: Pt[], w: number[], f: number[]): void => {
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + (i / n) * Math.PI * 2;   // pixel 0 at 12 o'clock
    out.push([0.5 + Math.cos(a) * 0.42, 0.5 + Math.sin(a) * 0.42]);
    w.push(i / n);
    // Fall runs top to bottom, so a drip down a ring reads as gravity rather
    // than as a lap of the circle.
    f.push((Math.sin(a) + 1) / 2);
  }
};

const layouts = new Map<string, Layout>();

/** The geometry of a fixture, computed once and kept. */
export function layoutOf(fx: Fixture, count?: number): Layout {
  const n = fx.maxCount ? Math.max(1, Math.min(fx.maxCount, count ?? fx.count)) : fx.count;
  const key = `${fx.id}:${n}`;
  const hit = layouts.get(key);
  if (hit) return hit;

  const pos: Pt[] = [];
  const walk: number[] = [];
  const fall: number[] = [];
  let center: number | null = null;

  if (n === 0) {
    // Nothing wired. A zero-pixel layout still has to be a valid Layout so
    // every consumer can stay branch-free.
  } else if (fx.layout === "hub") {
    center = 0;
    pos.push([0.5, 0.5]);
    walk.push(0);
    fall.push(0);
    ring(n - 1, pos, walk, fall);
  } else if (fx.layout === "ring") {
    ring(n, pos, walk, fall);
  } else if (fx.layout === "grid") {
    const cols = fx.cols ?? 8;
    const rows = fx.rows ?? Math.ceil(n / cols);
    for (let i = 0; i < n; i++) {
      const cx = i % cols;
      const cy = Math.floor(i / cols);
      pos.push([(cx + 0.5) / cols, (cy + 0.5) / rows]);
      // Serpentine by column so a chase sweeps across the matrix instead of
      // jumping back to the left edge every row.
      const up = cx % 2 === 1;
      walk.push((cx + (up ? rows - 1 - cy : cy) / rows) / cols);
      fall.push(rows === 1 ? 0 : cy / (rows - 1));
    }
  } else if (fx.layout === "line") {
    for (let i = 0; i < n; i++) {
      pos.push([n === 1 ? 0.5 : 0.06 + (i / (n - 1)) * 0.88, 0.5]);
      walk.push(i / n);
      fall.push(n === 1 ? 0 : i / (n - 1));
    }
  } else {
    // Scatter: singles you place by hand. Drawn in a row because the desk
    // cannot know where you glued them, spaced wider than a stick to say so.
    for (let i = 0; i < n; i++) {
      pos.push([n === 1 ? 0.5 : 0.12 + (i / (n - 1)) * 0.76, 0.5]);
      walk.push(i / n);
      fall.push(n === 1 ? 0 : i / (n - 1));
    }
  }

  // Distinct heights, rounded so floating-point noise in the ring's sine does
  // not report sixteen levels where the eye sees nine.
  const fallSteps = new Set(fall.map((v) => v.toFixed(3))).size;

  const core = new Array<boolean>(n).fill(false);
  if (center !== null) {
    core[center] = true;
  } else if (n > 0) {
    const byMiddle = pos
      // Rounded before sorting, and that is load-bearing. Every pixel on a
      // ring is the same distance from the middle in principle, but not to
      // the last bit of a double — and the survivors of an unrounded sort
      // came out different in JavaScript and in Python, which the geometry
      // parity test caught. Rounding turns near-ties into real ties so the
      // index tie-break below actually decides them.
      .map((xy, i) => ({
        i,
        d: Math.round(Math.hypot((xy[0] ?? 0) - 0.5, (xy[1] ?? 0) - 0.5) * 1e6) / 1e6,
      }))
      .sort((a, b) => a.d - b.d || a.i - b.i);
    for (let j = 0; j < Math.max(1, Math.round(n / 7)); j++) {
      const hit = byMiddle[j];
      if (hit) core[hit.i] = true;
    }
  }

  const out: Layout = { n, center, walk, fall, fallSteps, core, pos };
  layouts.set(key, out);
  return out;
}

/** What is in one spot. */
export interface Slot {
  fixture: string;
  /** Scatter fixtures only — how many singles are in this spot. */
  count?: number;
}

export interface RigState {
  zones: Record<ZoneId, Slot>;
  /** Per fixture id: is the one you own the RGBW variant? Fixtures that only
   *  ever shipped RGB ignore this. */
  rgbw: Record<string, boolean>;
}

const KEY = "castle.rig";

/** The rig as built today: three RGBW Jewels, one chain. Matches scenes.yaml
 *  so a desk with no saved rig previews the castle that exists. */
export const DEFAULT_RIG: RigState = {
  zones: {
    towerL: { fixture: "jewel7" },
    door: { fixture: "ring12" },
    towerR: { fixture: "jewel7" },
  },
  rgbw: { jewel7: true, stick8: true, ring12: true, ring16: true },
};

const isSlot = (v: unknown): v is Slot =>
  typeof v === "object" && v !== null
  && typeof (v as Slot).fixture === "string"
  && FIXTURES.some((f) => f.id === (v as Slot).fixture);

/** Restore the saved rig, falling back to the built castle. Anything the
 *  parser does not recognise is dropped rather than trusted — a stale key
 *  from an older build must not be able to produce a zone with no fixture. */
export function loadRig(): RigState {
  const rig: RigState = {
    zones: { ...DEFAULT_RIG.zones },
    rgbw: { ...DEFAULT_RIG.rgbw },
  };
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return rig;
    const saved = JSON.parse(raw) as Partial<RigState>;
    for (const z of ZONE_ORDER) {
      const s = saved.zones?.[z];
      if (isSlot(s)) rig.zones[z] = { fixture: s.fixture, ...(s.count ? { count: s.count } : {}) };
    }
    for (const [k, v] of Object.entries(saved.rgbw ?? {})) {
      if (typeof v === "boolean") rig.rgbw[k] = v;
    }
  } catch { /* corrupt or private mode: the default castle is a fine answer */ }
  return rig;
}

export function saveRig(rig: RigState): void {
  try { localStorage.setItem(KEY, JSON.stringify(rig)); } catch { /* session only */ }
}

/** Is this zone's fixture RGBW? Fixtures that only ship RGB always answer no. */
export function zoneRgbw(rig: RigState, z: ZoneId): boolean {
  const fx = fixture(rig.zones[z].fixture);
  return fx.rgbOnly ? false : (rig.rgbw[fx.id] ?? true);
}

export function zoneLayout(rig: RigState, z: ZoneId): Layout {
  const slot = rig.zones[z];
  return layoutOf(fixture(slot.fixture), slot.count);
}

export const zonePixels = (rig: RigState, z: ZoneId): number => zoneLayout(rig, z).n;

/**
 * Peak current, in amps, with every pixel at full white.
 *
 * 20 mA per die is the NeoPixel figure, so an RGB pixel is 60 mA and an RGBW
 * one 80 mA. This is deliberately the worst case and not an average: the
 * average tells you about heat, but the number that reboots the board is what
 * a lightning cue draws for 150 ms. See docs/WIRING.md §4.
 */
export function rigPower(rig: RigState): { amps: number; pixels: number } {
  let amps = 0;
  let pixels = 0;
  for (const z of ZONE_ORDER) {
    const n = zonePixels(rig, z);
    pixels += n;
    amps += n * (zoneRgbw(rig, z) ? 0.08 : 0.06);
  }
  return { amps, pixels };
}

/** Two amplifiers at their datasheet headroom, which the same supply carries. */
export const AUDIO_AMPS = 1.6;

export interface RigProblem {
  level: "warn" | "info";
  text: string;
}

/**
 * Everything about this rig worth saying out loud.
 *
 * Deliberately warnings and not refusals. Every one of these describes a real
 * build someone might be part-way through — parts on order, a chain being
 * rewired — and a desk that refuses to preview an unfinished rig is a desk
 * you stop using while you are building.
 */
export function rigProblems(rig: RigState): RigProblem[] {
  const out: RigProblem[] = [];

  // Over-allocation: two Jewels cannot fill three windows.
  const used = new Map<string, number>();
  for (const z of ZONE_ORDER) {
    const id = rig.zones[z].fixture;
    if (id !== "none") used.set(id, (used.get(id) ?? 0) + 1);
  }
  for (const [id, n] of used) {
    const fx = fixture(id);
    if (n > fx.own) {
      out.push({ level: "warn",
        text: `${n} zones want a ${fx.name} and you own ${fx.own}.` });
    }
  }

  // The mixing rule. Only bites if you are on one chain, so it is phrased as
  // the condition rather than as a prohibition.
  const live = ZONE_ORDER.filter((z) => zonePixels(rig, z) > 0);
  const kinds = new Set(live.map((z) => zoneRgbw(rig, z)));
  if (kinds.size > 1) {
    out.push({ level: "info",
      text: "Mixes RGBW and RGB, so these cannot share one data line — "
          + "wire a pin per zone (docs/WIRING.md §1)." });
  }

  const { amps, pixels } = rigPower(rig);
  const total = amps + AUDIO_AMPS;
  if (total > 4) {
    out.push({ level: "warn",
      text: `${pixels} pixels peak at ${amps.toFixed(1)} A, ${total.toFixed(1)} A `
          + "with both amps — needs a supply of 8 A or more." });
  }

  if (live.length === 0) {
    out.push({ level: "warn", text: "Every zone is empty; nothing will light." });
  }
  return out;
}

/** First and last index of each zone if the three were on ONE chain. Mirrors
 *  `zone_pixels()` in tools/gen_esphome.py, which walks the zones in declared
 *  order — not the left-to-right order the desk shows them in. */
export function chainRanges(rig: RigState): Record<ZoneId, [number, number]> {
  const out = {} as Record<ZoneId, [number, number]>;
  let at = 0;
  for (const z of ZONE_DECL) {
    const n = zonePixels(rig, z);
    out[z] = [at, at + Math.max(0, n - 1)];
    at += n;
  }
  return out;
}
