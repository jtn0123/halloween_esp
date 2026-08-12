/**
 * The effect vocabulary — a line-for-line port of firmware/castle_effects.h.
 *
 * These are the same formulas, with the same per-pixel seeds, that the device
 * runs. That is the whole point: the screen shows what the firmware will
 * compute, not a separate approximation of it. If you change one, change both.
 *
 * Every effect takes (seconds, seed) and returns linear RGBW in 0..1. The W is
 * a real 3000 K white emitter on the jewel, not a mix of the other three.
 */

import type { EffectName, Rgbw } from "./types.js";

/**
 * Live tuning knobs, driven by the sliders. Some effects read these, which is
 * why the previewer can be dialled in without a re-render — but note that only
 * `hue` and `soft` exist on the device (as `hue_balance` and `soften`). The
 * rest are preview-only conveniences for finding a look before it is baked
 * into scenes.yaml.
 */
export interface EffectParams {
  depth: number;
  speed: number;
  bright: number;
  hue: number;
  soft: boolean;
  stops: number;
  /** Palette index for the crossfade effects — set PER ZONE by the renderer
   *  before each call, not a user slider. Index into PALETTES. */
  pal: number;
}

export const defaultParams = (): EffectParams => ({
  depth: 0.55, speed: 1.4, bright: 0.85, hue: 0.5, soft: false, stops: 0.42,
  pal: 0,
});

/* ── Palettes — the pole pairs the crossfade effects sweep between ─────
   Same table, same order, as firmware/castle_effects.h. A scene names one
   per zone; index 0 is the classic violet/green the show was built on. */

export const PALETTE_NAMES = ["haunt", "ember", "moonlight", "toxic"] as const;
export type PaletteName = (typeof PALETTE_NAMES)[number];

type Pole = readonly [number, number, number];
export const PALETTES: ReadonlyArray<readonly [Pole, Pole]> = [
  [[0.66, 0.08, 1.00], [0.14, 1.00, 0.42]],   // haunt: violet <-> green
  [[0.72, 0.08, 0.00], [1.00, 0.55, 0.05]],   // ember: deep red <-> amber
  [[0.10, 0.22, 0.85], [0.72, 0.85, 1.00]],   // moonlight: indigo <-> pale blue
  [[0.05, 0.90, 0.10], [0.85, 1.00, 0.05]],   // toxic: green <-> acid yellow
];

export const paletteIndex = (name: string): number => {
  const i = (PALETTE_NAMES as readonly string[]).indexOf(name);
  return i < 0 ? 0 : i;
};

/* ── Smoothed value noise — the flame's whole personality ──────────────
   A flame flickers coherently; per-frame random reads as a loose connection,
   which is why this is interpolated rather than sampled. */

export function hash(n: number): number {
  const s = Math.sin(n * 127.1) * 43758.5453;
  return s - Math.floor(s);
}

export function vnoise(x: number): number {
  const i = Math.floor(x);
  const f = x - i;
  const u = f * f * (3 - 2 * f);
  return hash(i) * (1 - u) + hash(i + 1) * u;
}

export function fbm(x: number): number {
  return 0.55 * vnoise(x) + 0.30 * vnoise(x * 2.13 + 11.3) + 0.15 * vnoise(x * 4.31 + 27.7);
}

/** The zone's palette poles, chosen by P.pal (index 0 = classic haunt). */
const pole = (P: EffectParams, which: 0 | 1): Pole =>
  (PALETTES[P.pal] ?? PALETTES[0]!)[which];

/** What the jewel's white die actually looks like, for screen rendering. */
const WARM_W: readonly [number, number, number] = [1.00, 0.83, 0.62];

type Rgb = [number, number, number];

function mix(a: readonly number[], b: readonly number[], k: number): Rgb {
  return [
    (a[0] ?? 0) + ((b[0] ?? 0) - (a[0] ?? 0)) * k,
    (a[1] ?? 0) + ((b[1] ?? 0) - (a[1] ?? 0)) * k,
    (a[2] ?? 0) + ((b[2] ?? 0) - (a[2] ?? 0)) * k,
  ];
}

const scaled = (c: Rgb, k: number): Rgbw => [c[0] * k, c[1] * k, c[2] * k, 0];

export type EffectFn = (t: number, seed: number, P: EffectParams) => Rgbw;

export const EFFECTS: Record<EffectName, EffectFn> = {
  off: () => [0, 0, 0, 0],

  candle: (t, s, P) => {
    const n = fbm(t * P.speed + s * 3.7);
    const l = Math.max(0, 1 - P.depth * (1 - n));
    return [0.34 * l, 0.05 * l, 0, 1.00 * l];
  },

  ember: (t, s) => {
    const n = fbm(t * 0.63 + s * 2.2);
    const l = 0.22 + 0.16 * n;
    return [0.40 * l, 0.06 * l, 0, 0.85 * l];
  },

  furnace: (t, s) => {
    const n = fbm(t * 2.5 + s * 0.9);
    const l = 0.80 + 0.20 * n;
    return [1.00 * l, 0.22 * l, 0.02 * l, 0.55 * l];
  },

  spirit: (t, s) => {
    const b = 0.5 + 0.5 * Math.sin(t * 1.15 + s * 0.8);
    const l = 0.22 + 0.42 * b;
    return [0.10 * l, 1.00 * l, 0.66 * l, 0];
  },

  eyes: (t, s) => {
    const blink = vnoise(t * 1.9 + s * 0.55) > 0.82 ? 0.1 : 1;
    const l = (0.55 + 0.28 * Math.sin(t * 3.1)) * blink;
    return [1.00 * l, 0.05 * l, 0.03 * l, 0];
  },

  seance: (t, s, P) => {
    const b = 0.5 + 0.5 * Math.sin(t * 0.80 + s * 0.6);
    return scaled(mix(pole(P, 0), pole(P, 1), 0), 0.24 + 0.52 * b);
  },

  wisp: (t, s, P) => {
    const n = fbm(t * 2.1 + s * 5.3);
    const l = Math.max(0, 0.18 + 0.82 * n - 0.14);
    return scaled(mix(pole(P, 0), pole(P, 1), 1), l);
  },

  mansion: (t, s, P) => {
    const sweep = 0.5 + 0.5 * Math.sin(t * 0.38 + s * 0.7);
    const k = Math.min(1, Math.max(0, sweep * 0.8 + (P.hue - 0.5) * 0.9));
    const shimmer = 0.84 + 0.16 * fbm(t * 1.05 + s * 2.7);
    return scaled(mix(pole(P, 0), pole(P, 1), k), 0.62 * shimmer);
  },

  throb: (t, s, P) => {
    const p = Math.pow(0.5 + 0.5 * Math.sin(t * 7.4 + s * 0.4), 2);
    return scaled(mix(pole(P, 0), pole(P, 1), P.hue * 0.5), 0.20 + 0.80 * p);
  },

  strobe: (t, s, P) => {
    if (P.soft) {                        // ~7 Hz hard strobe is a seizure risk
      const l = 0.34 + 0.44 * (0.5 + 0.5 * Math.sin(t * 3.1 + s));
      return [0.10 * l, 0.10 * l, 0.14 * l, 1.00 * l];
    }
    const on = Math.sin(t * 44 + s) > 0 ? 1 : 0.06;
    return [0.12 * on, 0.12 * on, 0.18 * on, 1.00 * on];
  },

  chill: (t, s, P) => {
    const b = 0.5 + 0.5 * Math.sin(t * 0.50 + s * 1.1);
    return scaled(mix(pole(P, 0), pole(P, 1), P.hue * 0.35), 0.14 + 0.16 * b);
  },

  blood: (t, s) => {
    // Near-dark deep red smoulder — the floor under the crypt heartbeat.
    const n = fbm(t * 0.35 + s * 1.7);
    const l = 0.045 + 0.05 * n;
    return [1.00 * l, 0.02 * l, 0.01 * l, 0];
  },
};

/** Unknown names fall back to darkness rather than throwing mid-frame. */
export const effect = (name: string): EffectFn =>
  EFFECTS[name as EffectName] ?? EFFECTS.off;

/**
 * RGBW → screen colour. The white die is a real emitter, so it is summed in
 * rather than replacing the colour channels.
 */
export function toScreen(c: Rgbw): Rgb {
  return [
    Math.min(1, c[0] + c[3] * WARM_W[0]),
    Math.min(1, c[1] + c[3] * WARM_W[1]),
    Math.min(1, c[2] + c[3] * WARM_W[2]),
  ];
}

/**
 * The per-pixel seed. Matches the firmware lambda exactly — a flame moves
 * ACROSS a jewel because each pixel walks its own noise path, and that spatial
 * motion is most of what makes it read as fire.
 */
export const pixelSeed = (zoneIndex: number, pixel: number): number =>
  zoneIndex * 4.7 + pixel * 1.31;

/* ── Overlays — a second voice on top of any base effect ───────────────
   The base effects light a whole jewel with one texture. Overlays are what
   give individual pixels ROLES: a glint that lands on one pixel, a point of
   light orbiting the ring, a drip falling through. Same formulas as
   firmware/castle_effects.h; a jewel is pixel 0 (centre) + pixels 1-6 (ring).

   Each takes the base colour and returns it modified — compositing, not
   replacement, so the candle keeps burning under the sparkle. */

export const OVERLAY_NAMES = ["none", "sparkle", "chase", "meteor"] as const;
export type OverlayName = (typeof OVERLAY_NAMES)[number];

export const overlayIndex = (name: string): number => {
  const i = (OVERLAY_NAMES as readonly string[]).indexOf(name);
  return i < 0 ? 0 : i;
};

/** Shortest way around the 6-pixel ring, in pixel units (0..3). */
const ringDist = (a: number, pos: number): number => {
  const d = Math.abs(a - pos);
  return Math.min(d, 6 - d);
};

export function applyOverlay(ov: number, c: Rgbw, t: number, p: number, zi: number): Rgbw {
  if (ov === 1) {           // sparkle: rare single-pixel glints
    const cell = Math.floor(t * 7);
    const g = hash(cell * 13.7 + p * 7.77 + zi * 3.1);
    if (g > 0.93) {
      const k = (g - 0.93) / 0.07;
      return [Math.min(1, c[0] + 0.30 * k), Math.min(1, c[1] + 0.30 * k),
              Math.min(1, c[2] + 0.30 * k), Math.min(1, c[3] + 0.90 * k)];
    }
    return c;
  }
  if (ov === 2) {           // chase: a point of light orbiting the ring
    const pos = ((t * 0.45 + zi * 0.37) % 1) * 6;
    if (p === 0) return [c[0] * 0.55, c[1] * 0.55, c[2] * 0.55, c[3] * 0.55];
    const boost = Math.max(0, 1 - ringDist(p - 1, pos) * 0.9);
    const k = 0.45 + 0.55 * boost;
    return [c[0] * k, c[1] * k, c[2] * k,
            Math.min(1, c[3] * k + 0.50 * boost * boost)];
  }
  if (ov === 3) {           // meteor: a drip — centre flash, falls through the ring
    const ph = (t / 2.6 + zi * 0.41) % 1;
    if (ph < 0.12) {
      if (p !== 0) return c;
      const k = (0.12 - ph) / 0.12;
      return [c[0], c[1], c[2], Math.min(1, c[3] + 0.80 * k)];
    }
    if (p === 0) return c;
    const fall = ((ph - 0.12) / 0.88) * 6;
    const fade = 1 - ((ph - 0.12) / 0.88) * 0.5;
    const boost = Math.max(0, 1 - ringDist(p - 1, fall) * 1.2) * fade;
    return [Math.min(1, c[0] + 0.20 * boost), c[1], Math.min(1, c[2] + 0.25 * boost),
            Math.min(1, c[3] + 0.60 * boost)];
  }
  return c;
}

/* ── Strike masks — which pixels a flash actually hits ─────────────────
   Mode 0 hits the whole jewel (the classic look). The others give a strike
   spatial texture: scatter picks a different random subset each strike
   (epoch changes per strike cue), centre and ring split the jewel by role. */

export const FLASH_MODES = ["all", "scatter", "center", "ring"] as const;
export const flashModeIndex = (name: string): number => {
  const i = (FLASH_MODES as readonly string[]).indexOf(name);
  return i < 0 ? 0 : i;
};

export function flashGate(mode: number, p: number, zi: number, epoch: number): number {
  if (mode === 1) return hash(p * 9.13 + zi * 5.7 + epoch * 17.9) > 0.45 ? 1 : 0.15;
  if (mode === 2) return p === 0 ? 1 : 0.1;
  if (mode === 3) return p === 0 ? 0.1 : 1;
  return 1;
}
