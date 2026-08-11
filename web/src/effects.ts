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
}

export const defaultParams = (): EffectParams => ({
  depth: 0.55, speed: 1.4, bright: 0.85, hue: 0.5, soft: false, stops: 0.42,
});

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

/** Mansion palette: the two poles everything crossfades between. */
const VIOLET: readonly [number, number, number] = [0.66, 0.08, 1.00];
const GREEN: readonly [number, number, number] = [0.14, 1.00, 0.42];

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

  seance: (t, s) => {
    const b = 0.5 + 0.5 * Math.sin(t * 0.80 + s * 0.6);
    return scaled(mix(VIOLET, GREEN, 0), 0.24 + 0.52 * b);
  },

  wisp: (t, s) => {
    const n = fbm(t * 2.1 + s * 5.3);
    const l = Math.max(0, 0.18 + 0.82 * n - 0.14);
    return scaled(mix(VIOLET, GREEN, 1), l);
  },

  mansion: (t, s, P) => {
    const sweep = 0.5 + 0.5 * Math.sin(t * 0.38 + s * 0.7);
    const k = Math.min(1, Math.max(0, sweep * 0.8 + (P.hue - 0.5) * 0.9));
    const shimmer = 0.84 + 0.16 * fbm(t * 1.05 + s * 2.7);
    return scaled(mix(VIOLET, GREEN, k), 0.62 * shimmer);
  },

  throb: (t, s, P) => {
    const p = Math.pow(0.5 + 0.5 * Math.sin(t * 7.4 + s * 0.4), 2);
    return scaled(mix(VIOLET, GREEN, P.hue * 0.5), 0.20 + 0.80 * p);
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
    return scaled(mix(VIOLET, GREEN, P.hue * 0.35), 0.14 + 0.16 * b);
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
