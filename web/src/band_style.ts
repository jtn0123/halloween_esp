/**
 * The band style vocabulary — one leaf module, no imports from the rest of
 * the light engine.
 *
 * This lived in track_lights.ts, which track_style.ts imported BACK from
 * while track_lights imported its helpers — a live value cycle that only
 * survived because BAND_STYLE was always read lazily inside functions.
 * A leaf everyone imports from cannot cycle.
 */

import type { BandName } from "./bands.js";
import type { Rgbw, ZoneId } from "./types.js";

/** One band's whole visual treatment. */
export interface BandStyle {
  /** Round-robin movement when the user has not pinned the band to a zone. */
  zones: readonly ZoneId[];
  alternate: boolean;
  intensity: number;
  decay: number;
  ms: number;
  /**
   * Colours the stream cycles through hit by hit — consecutive hits differ
   * in HUE, not just brightness. Exported as `colors:` for the generators.
   */
  colors: readonly Rgbw[];
  /**
   * Each hit's base colour blends toward this by velocity. SATURATED on
   * purpose: an earlier pass used white-ish hot colours, and since the hard
   * hits are the visible ones, the whole show read as "mostly white" —
   * confirmed by the user against real screenshots. W stays near zero here;
   * white is what strikes decay THROUGH on screen, not what they are.
   */
  colorHot: Rgbw;
  /** Velocity picks the mask: soft centre, medium scatter, hard whole jewel. */
  pixelsByVel?: boolean;
  /** Fixed mask instead (the highs always glint as scattered pixels). */
  pixels?: "all" | "scatter" | "center" | "ring";
  /** A hit this hard spills onto the extra zones too — the big downbeat. */
  boostAt?: number;
  boostTargets?: readonly ZoneId[];
  /** #10: rise time to peak, ms. Absent = instant slam. Drums slam; voices
   *  and pads swell in. */
  attackMs?: number;
}

/**
 * low   — drums, heartbeats: deep red at the door's core, gold when it slams,
 *         and the hardest hits light the whole castle.
 * mid   — voices, piano: violet answering between the towers, hot pink-white
 *         on the belted notes.
 * high  — bells, sparkle: green-ice glints scattered across single pixels,
 *         walking tower→door→tower so the glitter moves.
 */
export const BAND_STYLE: Readonly<Record<BandName, BandStyle>> = {
  /* Intensity and decay are tuned against real screenshots: at the first
     pass (intensity ~0.6, decay 0.86-0.94) the median hit landed at 0.25
     over a lit amber base and was gone in ~200 ms — the colours were firing
     and invisible. Hits must READ, or the whole exercise is three lamps.

     The colour cycles are triads a few hue-steps apart, so a run of hits in
     one band walks through related-but-distinct colours instead of pulsing
     one. Every entry is saturated (one channel low or zero, W ≤ 0.12). */
  onset_low: {
    zones: ["door"], alternate: false, intensity: 0.85, decay: 0.90, ms: 140,
    colors: [[1.0, 0.04, 0.0, 0.0],      // deep red
             [1.0, 0.18, 0.0, 0.0],      // vermilion
             [0.85, 0.0, 0.25, 0.0]],    // crimson-magenta
    colorHot: [1.0, 0.45, 0.02, 0.10],   // hot amber, not white
    pixelsByVel: true, boostAt: 0.85, boostTargets: ["towerL", "towerR"],
  },
  onset_mid: {
    zones: ["towerL", "towerR"], alternate: true,
    intensity: 0.80, decay: 0.945, ms: 160,
    colors: [[0.55, 0.0, 1.0, 0.0],      // violet
             [1.0, 0.0, 0.75, 0.0],      // magenta
             [0.25, 0.12, 1.0, 0.0]],    // indigo
    colorHot: [1.0, 0.15, 0.85, 0.12],   // hot pink, stays pink
    pixelsByVel: true,
    attackMs: 90,                        // voices swell in; drums still slam
  },
  onset_high: {
    zones: ["towerR", "door", "towerL"], alternate: true,
    intensity: 0.72, decay: 0.95, ms: 130,
    colors: [[0.05, 1.0, 0.35, 0.0],     // emerald
             [0.0, 0.85, 1.0, 0.0],      // cyan
             [0.65, 1.0, 0.0, 0.0]],     // chartreuse
    colorHot: [0.25, 1.0, 0.70, 0.15],   // hot spring-green
    pixels: "scatter",
  },
};

