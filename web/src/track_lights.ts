/**
 * The look of a track-driven scene — every decision in one table.
 *
 * A generated scene used to be three fixed colours on three fixed lamps, one
 * identical whole-jewel flash per onset. Musically dense material (voices,
 * bells, drums at once) rendered as a slow trickle of indistinguishable
 * blinks — the lights were technically following the music while visibly
 * ignoring it.
 *
 * This module is the upgrade, and the single source both consumers read:
 *
 *   sceneFromTrack (the audition)  — expands onsets to cues HERE
 *   sceneYaml      (the export)    — writes the same decisions as `pulse:`
 *                                    config for tools/gen_previewer.py and
 *                                    tools/gen_esphome.py to expand
 *
 * The expansion arithmetic (velocity colour blend, velocity pixel masks,
 * boost spillover, alternate round-robin) is duplicated in those two Python
 * generators, deliberately and exactly — see pulse_cues() in gen_esphome.py.
 * Keep all three in lockstep.
 */

import { BAND_BY_NAME, type BandName } from "./bands.js";
import type { Cue, Rgbw, StrikeCue, ZoneId } from "./types.js";
import type { Onset } from "./onsets.js";

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

export { BAND_STYLE_CLASSIC, setStyleVariant, styleVariant, setStyleTweak,
         resetStyleTweaks, styleFor, styleAsTs, setFlavor, getFlavors,
         resetFlavors, driftBase, TAKEOVER_COLORS, TAKEOVER_HOT }
  from "./track_style.js";
export type { StyleVariant, StyleTweak, Flavors } from "./track_style.js";
import { driftBase, getFlavors, styleFor, styleVariant,
         TAKEOVER_COLORS, TAKEOVER_HOT } from "./track_style.js";

/** color -> colorHot by velocity. Same maths as blend_color in the generators. */
export const blendColor = (base: Rgbw, hot: Rgbw | undefined, vel: number): Rgbw =>
  !hot ? base
       : [base[0] + (hot[0] - base[0]) * vel, base[1] + (hot[1] - base[1]) * vel,
          base[2] + (hot[2] - base[2]) * vel, base[3] + (hot[3] - base[3]) * vel];

/** Velocity mask thresholds — shared with pixels_for in the generators. */
export const pixelsForVel = (vel: number): "center" | "scatter" | "all" =>
  vel < 0.40 ? "center" : vel < 0.72 ? "scatter" : "all";

/* ── Per-stream dynamics (#3 tempo, #8 accents, #7 pan) ─────────────────
 * Twins of tempo_factor / tempo_decay / is_accent / PAN_DECISIVE in
 * gen_esphome.py (gen_previewer.py imports them from there). The parity
 * tests pin both sides to the same digits. */

/** Median hit gap -> tail-length factor. Neutral below 8 hits, so sparse
 *  streams and every hand-written scene keep their exact old look. */
export function tempoFactor(timesSec: readonly number[]): number {
  if (timesSec.length < 8) return 1.0;
  const gaps = timesSec.slice(1).map((t, i) => t - timesSec[i]!).sort((a, b) => a - b);
  const m = Math.floor(gaps.length / 2);
  const g = gaps.length % 2 ? gaps[m]! : (gaps[m - 1]! + gaps[m]!) / 2;
  return Math.min(1.6, Math.max(0.7, g / 0.45));
}

/** Scale time-to-dark, not the decay digit; floor(+0.5) matches Python. */
export const tempoDecay = (decay: number, factor: number): number =>
  Math.floor((1 - (1 - decay) / factor) * 10000 + 0.5) / 10000;

/** Louder than its recent neighbourhood — the accent a global threshold
 *  misses on compression-mastered music. */
export function isAccent(vels: readonly number[], i: number): boolean {
  const w = vels.slice(Math.max(0, i - 8), i);
  return w.length >= 3
    && vels[i]! >= w.reduce((a, v) => a + v, 0) / w.length + 0.25
    && vels[i]! >= 0.55;
}

/** |pan| at or above this routes the hit to its own tower. Measured against
 *  the real library (see pulse_dynamics.py): 0.25 almost never fired on real
 *  mixes; 0.10 clears the analyzer's dead zone and catches what is audibly
 *  on one side. */
export const PAN_DECISIVE = 0.10;

/**
 * Expand one band's onsets into strike cues, exactly as the Python pulse
 * expansion would. `zoneOverride` is the user pinning this band to one zone
 * in the band editor — movement then defers to their choice.
 */
export function bandStrikes(
  band: BandName, hits: readonly Onset[],
  startSec: number, endSec: number, zoneOverride?: ZoneId,
  gates: ReadonlyArray<readonly [number, string]> = [],
): StrikeCue[] {
  const s = styleFor(band);
  const pinned = zoneOverride !== undefined && zoneOverride !== BAND_BY_NAME[band].zone;
  const zones: readonly ZoneId[] = pinned ? [zoneOverride] : s.zones;
  // Windowed FIRST: the Python expansions only ever see the clip's hits
  // (markers are detected on the rendered clip), so the tempo and accent
  // context must be the clip here too or the two sides drift.
  const win = hits.filter(([sec]) => sec >= startSec && sec <= endSec);
  const factor = tempoFactor(win.map(([sec]) => sec));
  const decay = tempoDecay(s.decay, factor);
  const ms = Math.floor(s.ms * factor + 0.5);
  const vels = win.map(([, vel]) => vel);
  // Flavours (#1 drift, #2 takeover) colour engine A only: the classic
  // baseline predates them, and B exists to lose fairly.
  const flav = styleVariant() === "current"
    ? getFlavors() : { drift: false, takeover: false, swells: false };
  const out: StrikeCue[] = [];
  win.forEach(([sec, vel, pan], i) => {
    const tMs = Math.round((sec - startSec) * 1000);
    const mul = gateMul(band, gates, tMs);
    if (mul === null) return;                 // gated out by its section (#9)
    let targets: ZoneId[];
    if (s.alternate && !pinned) {
      // A decisively panned hit goes to ITS tower (#7); the rest keep the
      // round-robin movement.
      targets = pan !== undefined && Math.abs(pan) >= PAN_DECISIVE
             && zones.includes("towerL") && zones.includes("towerR")
        ? [pan < 0 ? "towerL" : "towerR"]
        : [zones[i % zones.length]!];
    } else {
      targets = [...zones];
    }
    if (s.boostTargets
        && ((s.boostAt !== undefined && vel >= s.boostAt) || isAccent(vels, i))) {
      targets = targets.concat(s.boostTargets.filter(z => !targets.includes(z)));
    }
    const pixels = s.pixelsByVel ? pixelsForVel(vel) : s.pixels;
    let base = s.colors[i % s.colors.length]!;
    let hot: Rgbw | undefined = s.colorHot;
    if (flav.takeover && gateNote(gates, tMs) === "chorus") {
      base = TAKEOVER_COLORS[i % TAKEOVER_COLORS.length]!;
      hot = TAKEOVER_HOT;
    } else if (flav.drift) {
      base = driftBase(s.colors, i, tMs);
    }
    out.push({
      t: tMs,
      bus: "LED", op: "strike", ms,
      intensity: Math.round(s.intensity * vel * mul * 1000) / 1000,
      color: blendColor(base, hot, vel),
      decay,
      ...(pixels ? { pixels } : {}),
      ...(s.attackMs ? { attack: s.attackMs } : {}),
      targets,
      detail: band,
    });
  });
  return out;
}

/* The standing light — tiers, sections, silence, gates, swells — lives
 * in track_sections.ts (500-line cap; the seam is "what stands" vs
 * "what strikes"). Re-exported so consumers keep one import surface. */
export { TIERS, SILENCE_TIER, SILENCE_LOOK, ZONES_BLOCK, sections,
         sectionCues, sectionGates, gateMul, gateNote, sustainedSwells }
  from "./track_sections.js";
export type { TierLook } from "./track_sections.js";
import { gateMul, gateNote, sectionCues, sectionGates, sustainedSwells }
  from "./track_sections.js";

/** Everything the audition needs, windowed to the clip and sorted. */
export function trackCues(
  onsets: Readonly<Record<string, readonly Onset[]>>,
  env: ReadonlyArray<readonly [number, number]> | undefined,
  startSec: number, endSec: number,
  zoneOverrides: Partial<Record<BandName, ZoneId>> = {},
  active: Partial<Record<BandName, boolean>> = {},
): Cue[] {
  const cues: Cue[] = [...sectionCues(env, startSec, endSec)];
  // The strike gates come from the cues just emitted — the same timeline
  // the exported YAML will carry, which is what the Python side reads.
  const gates = sectionGates(cues);
  if (styleVariant() === "current" && getFlavors().swells)
    cues.push(...sustainedSwells(env, startSec, endSec));
  for (const band of Object.keys(BAND_STYLE) as BandName[]) {
    const hits = onsets[band];
    // Muted in the band editor = absent from the audition. Audition only:
    // the export ignores mutes, which are a listening tool, not a decision.
    if (hits && active[band] !== false)
      cues.push(...bandStrikes(band, hits, startSec, endSec,
                               zoneOverrides[band], gates));
  }
  cues.sort((a, z) => a.t - z.t);
  return cues;
}
