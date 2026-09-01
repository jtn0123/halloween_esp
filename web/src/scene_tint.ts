/**
 * A colour for each scene, derived from the effects the scene actually runs.
 *
 * The scene grid used to be eight identical grey tiles: the only thing telling
 * Séance from Ballroom was reading the word. A swatch is the fastest possible
 * answer to "which one is the green one", and it costs nothing to compute —
 * the effect functions are already here, so the tile can be coloured by the
 * light it will produce rather than by a hand-picked palette that drifts out
 * of sync with scenes.yaml the first time an effect changes.
 *
 * Sampling is the whole problem. Most of these effects sit near black at any
 * given instant — a candle between flickers is almost nothing — so one sample
 * is useless. We look for the PEAK each effect reaches over a spread of times
 * and pixel seeds, keep its hue, and drop its brightness, because a tile is
 * asking "what colour" and not "how bright".
 */

import { defaultParams, effect, pixelSeed, toScreen } from "./effects.js";
import { isLed, type Scene, type ZoneId } from "./types.js";

/** Zones weighted by how much of the castle they are: the doorway is the
 *  widest aperture and the one an effect change reads on first. */
const ZONE_WEIGHT: Record<ZoneId, number> = { door: 1.3, towerL: 0.7, towerR: 0.7 };

/** Normalised RGB (max channel = 1) at the effect's most saturated moment,
 *  or null for an effect that never lights. */
const peaks = new Map<string, readonly [number, number, number] | null>();

function effectPeak(name: string): readonly [number, number, number] | null {
  if (!name || name === "off") return null;
  const hit = peaks.get(name);
  if (hit !== undefined) return hit;

  const fn = effect(name);
  const P = defaultParams();
  let best: readonly [number, number, number] | null = null;
  let score = -1;
  // 14 times × 3 seeds: enough to catch a slow crossfade's ends and a fast
  // flicker's crest without the tile costing a visible pause on load.
  for (let p = 0; p < 14; p++) {
    for (let px = 0; px < 3; px++) {
      const c = toScreen(fn(0.3 + p * 0.47, pixelSeed(px * 7, px * 3), P));
      const r = Math.min(1, c[0]), g = Math.min(1, c[1]), b = Math.min(1, c[2]);
      const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
      if (mx < 0.04) continue;         // effectively dark — carries no hue
      // Saturation first, brightness as a tie-break: a washed-out bright
      // frame says less about an effect than a dim, strongly coloured one.
      const v = (mx - mn) / mx + mx * 0.3;
      if (v > score) { score = v; best = [r / mx, g / mx, b / mx]; }
    }
  }
  peaks.set(name, best);
  return best;
}

/**
 * The scene's signature colour, as a CSS `hsl()` string.
 *
 * Every effect the scene uses counts — the three base zones plus whatever the
 * cues switch to — and the result is pulled toward white in proportion to how
 * much lightning it fires. Without that last step Vigil and Storm come out
 * identical, since they share a base and differ only in strikes.
 *
 * @param alpha 1 for the selected tile, lower for the rest.
 */
export function sceneTint(scene: Scene, alpha: number): string {
  let ar = 0, ag = 0, ab = 0, weight = 0;
  const add = (name: string | undefined, w: number): void => {
    const c = name ? effectPeak(name) : null;
    if (!c) return;
    ar += c[0] * w; ag += c[1] * w; ab += c[2] * w;
    weight += w;
  };

  for (const zone of ["door", "towerL", "towerR"] as const) {
    add(scene.base[zone], ZONE_WEIGHT[zone]);
  }
  let strikes = 0;
  for (const c of scene.cues) {
    if (!isLed(c)) continue;
    if (c.op === "strike") strikes += c.intensity ?? 1;
    else add(c.eff, 0.5);
  }
  if (weight === 0) return `rgba(150,140,125,${alpha})`;

  let r = ar / weight, g = ag / weight, b = ab / weight;
  const white = Math.min(0.62, strikes * 0.09);
  r += (1 - r) * white; g += (1 - g) * white; b += (1 - b) * white;

  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0;
  if (d > 0) {
    if (mx === r) h = ((g - b) / d + 6) % 6;
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  // Floor the saturation so a near-white scene is still a colour and not a
  // grey smudge; cap it so nothing on the tile row screams.
  const sat = Math.max(0.14, Math.min(0.92, (d / mx) * 1.15));
  const lit = alpha >= 1;
  return `hsl(${h.toFixed(0)} ${(sat * 100).toFixed(0)}% ${lit ? 68 : 56}% / ${lit ? 1 : 0.85})`;
}
