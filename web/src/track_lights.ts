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
import type { Cue, EffectName, Rgbw, SetCue, StrikeCue, ZoneId } from "./types.js";
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

/* ── The style lab: A/B and live knobs ────────────────────────────────
 * Evaluation tooling, not show design. "B" is the pre-4K look — one flat
 * colour per band, whole-jewel flashes, no movement, no sections — kept as
 * the honest baseline so "did the new dynamics help?" is one button, not an
 * argument. Tweaks are live multipliers from the knobs panel.
 *
 * The EXPORT always uses variant A (with tweaks): B exists to be compared
 * against, never to be shipped, and writing whatever the toggle happened to
 * be on would reintroduce the audition/device divergence this file exists
 * to prevent. */

export const BAND_STYLE_CLASSIC: Readonly<Record<BandName, BandStyle>> = {
  onset_low: { zones: ["door"], alternate: false, intensity: 0.62,
               decay: 0.90, ms: 120, colors: [[1.0, 0.15, 0.05, 0.0]],
               colorHot: [1.0, 0.15, 0.05, 0.0] },
  onset_mid: { zones: ["towerL"], alternate: false, intensity: 0.62,
               decay: 0.90, ms: 120, colors: [[0.7, 0.2, 1.0, 0.0]],
               colorHot: [0.7, 0.2, 1.0, 0.0] },
  onset_high: { zones: ["towerR"], alternate: false, intensity: 0.62,
                decay: 0.90, ms: 120, colors: [[0.2, 1.0, 0.4, 0.0]],
                colorHot: [0.2, 1.0, 0.4, 0.0] },
};

export type StyleVariant = "current" | "classic";
export interface StyleTweak { intensity: number; decay: number }
const NEUTRAL: StyleTweak = { intensity: 1, decay: 1 };

let variant: StyleVariant = "current";
const tweaks: Record<BandName, StyleTweak> = {
  onset_low: { ...NEUTRAL }, onset_mid: { ...NEUTRAL }, onset_high: { ...NEUTRAL },
};

export const setStyleVariant = (v: StyleVariant): void => { variant = v; };
export const styleVariant = (): StyleVariant => variant;
export const setStyleTweak = (band: BandName, t: Partial<StyleTweak>): void => {
  Object.assign(tweaks[band], t);
};
export const resetStyleTweaks = (): void => {
  for (const b of Object.keys(tweaks) as BandName[]) tweaks[b] = { ...NEUTRAL };
};

/**
 * The style a band renders AND exports with. Tweaks apply in both cases;
 * the classic variant applies only when auditioning (`forExport` false).
 * Decay is eased toward 1 rather than multiplied — decay 0.945 × 1.05 would
 * exceed 1 and a strike would never die.
 */
export function styleFor(band: BandName, forExport = false): BandStyle {
  const base = !forExport && variant === "classic"
    ? BAND_STYLE_CLASSIC[band] : BAND_STYLE[band];
  const t = tweaks[band];
  if (t.intensity === 1 && t.decay === 1) return base;
  return {
    ...base,
    intensity: Math.min(1, Math.round(base.intensity * t.intensity * 1000) / 1000),
    decay: Math.min(0.995, Math.max(0.5,
      Math.round((1 - (1 - base.decay) / t.decay) * 1000) / 1000)),
  };
}

/** The effective styles as a BAND_STYLE literal, for pasting back into this
 *  file once a knob setting has earned permanence. */
export function styleAsTs(): string {
  const num = (v: number): string => String(Math.round(v * 1000) / 1000);
  const col = (c: Rgbw): string => `[${c.map(num).join(", ")}]`;
  const lines = (Object.keys(BAND_STYLE) as BandName[]).map(b => {
    const s = styleFor(b, true);
    return `  ${b}: {\n`
      + `    zones: [${s.zones.map(z => `"${z}"`).join(", ")}], `
      + `alternate: ${s.alternate}, intensity: ${num(s.intensity)}, `
      + `decay: ${num(s.decay)}, ms: ${s.ms},\n`
      + `    colors: [${s.colors.map(col).join(", ")}],\n`
      + `    colorHot: ${col(s.colorHot)},\n`
      + (s.pixelsByVel ? `    pixelsByVel: true,\n` : "")
      + (s.pixels ? `    pixels: "${s.pixels}",\n` : "")
      + (s.boostAt !== undefined
         ? `    boostAt: ${num(s.boostAt)}, boostTargets: `
           + `[${(s.boostTargets ?? []).map(z => `"${z}"`).join(", ")}],\n` : "")
      + `  },`;
  });
  return `export const BAND_STYLE = {\n${lines.join("\n")}\n};`;
}

/** color -> colorHot by velocity. Same maths as blend_color in the generators. */
export const blendColor = (base: Rgbw, hot: Rgbw | undefined, vel: number): Rgbw =>
  !hot ? base
       : [base[0] + (hot[0] - base[0]) * vel, base[1] + (hot[1] - base[1]) * vel,
          base[2] + (hot[2] - base[2]) * vel, base[3] + (hot[3] - base[3]) * vel];

/** Velocity mask thresholds — shared with pixels_for in the generators. */
export const pixelsForVel = (vel: number): "center" | "scatter" | "all" =>
  vel < 0.40 ? "center" : vel < 0.72 ? "scatter" : "all";

/**
 * Expand one band's onsets into strike cues, exactly as the Python pulse
 * expansion would. `zoneOverride` is the user pinning this band to one zone
 * in the band editor — movement then defers to their choice.
 */
export function bandStrikes(
  band: BandName, hits: readonly Onset[],
  startSec: number, endSec: number, zoneOverride?: ZoneId,
): StrikeCue[] {
  const s = styleFor(band);
  const pinned = zoneOverride !== undefined && zoneOverride !== BAND_BY_NAME[band].zone;
  const zones: readonly ZoneId[] = pinned ? [zoneOverride] : s.zones;
  const out: StrikeCue[] = [];
  hits.forEach(([sec, vel], i) => {
    if (sec < startSec || sec > endSec) return;
    let targets = s.alternate && !pinned ? [zones[i % zones.length]!] : [...zones];
    if (s.boostTargets && s.boostAt !== undefined && vel >= s.boostAt) {
      targets = targets.concat(s.boostTargets.filter(z => !targets.includes(z)));
    }
    const pixels = s.pixelsByVel ? pixelsForVel(vel) : s.pixels;
    out.push({
      t: Math.round((sec - startSec) * 1000),
      bus: "LED", op: "strike", ms: s.ms,
      intensity: Math.round(s.intensity * vel * 1000) / 1000,
      color: blendColor(s.colors[i % s.colors.length]!, s.colorHot, vel),
      decay: s.decay,
      ...(pixels ? { pixels } : {}),
      targets,
      detail: band,
    });
  });
  return out;
}

/* ── Sections: the scene breathes with the song ───────────────────────── */

/** What each loudness tier does to the standing light. */
export interface TierLook {
  towers: EffectName; towersLevel: number;
  door: EffectName; doorLevel: number;
}
/* The verse held `candle` towers until a screenshot audit showed why the
 * show still read whitish: candle is W-LED-dominated (W 1.0 vs R 0.34), and
 * the verse is most of any song. seance breathes through the zone's palette
 * instead — saturated violet/green on the haunt tower, blues on the
 * moonlight one — so the ambience carries colour, not just the strikes.
 * The door keeps its fire (ember -> furnace); warm is the point there. */
export const TIERS: readonly TierLook[] = [
  { towers: "chill", towersLevel: 0.40, door: "ember", doorLevel: 0.50 },   // quiet
  { towers: "seance", towersLevel: 0.70, door: "ember", doorLevel: 0.80 },  // mid
  { towers: "mansion", towersLevel: 0.95, door: "furnace", doorLevel: 0.95 }, // loud
];

/** A tier change must hold this long before it commits — a snare hit is not
 *  a chorus, and a breath between phrases is not a hush. */
const DEBOUNCE_SEC = 1.0;
/** Hysteresis width: once entered, a tier is left this far below its edge. */
const HYST = 0.08;
/** A song whose loudness barely moves has no sections worth cutting. */
const FLAT_RANGE = 0.15;

/** env sampled at `sec` — points are sparse and near-silence is omitted,
 *  so a miss reads as quiet. */
function envAt(env: ReadonlyArray<readonly [number, number]>, sec: number): number {
  let best = 0, bestDt = 0.5;               // nothing within half a second: 0
  for (const [t, v] of env) {
    const dt = Math.abs(t - sec);
    if (dt < bestDt) { bestDt = dt; best = v; }
  }
  return best;
}

/** Where `v` wants the castle, given where it is — hysteresis, direct jump. */
function desiredTier(v: number, cur: number, up: readonly number[]): number {
  if (cur < 0) return v >= up[1]! ? 2 : v >= up[0]! ? 1 : 0;
  let next = cur;
  if (v >= up[1]!) next = 2;
  else if (v >= up[0]!) next = Math.max(next, 1);
  if (v < up[0]! - HYST) next = 0;
  else if (v < up[1]! - HYST) next = Math.min(next, 1);
  return next;
}

const clamp = (v: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, v));

/** The tier boundaries of a clip: [startSec, tierIndex] pairs, first at 0. */
export function sections(
  env: ReadonlyArray<readonly [number, number]> | undefined,
  startSec: number, endSec: number,
): Array<[number, number]> {
  if (!env || !env.length) return [[0, 1]];      // no envelope: hold the middle
  const STEP = 0.25;

  // Smooth over ±0.5 s so one hit cannot promote a whole section.
  const smoothed: number[] = [];
  for (let sec = startSec; sec <= endSec; sec += STEP) {
    smoothed.push((envAt(env, sec - 0.5) + envAt(env, sec) + envAt(env, sec + 0.5)) / 3);
  }

  // The tier edges come from the song's OWN loudness distribution. Fixed
  // edges never fired on real music: mastering compression parks the whole
  // normalised envelope mid-range, so a fixed 0.70 chorus bar was simply
  // never cleared and every track sat in "verse" for its full length.
  const sorted = [...smoothed].sort((a, b) => a - b);
  const pct = (q: number): number => sorted[Math.floor(q * (sorted.length - 1))]!;
  if (pct(0.9) - pct(0.1) < FLAT_RANGE) return [[0, 1]];   // flat: hold middle
  const up0 = clamp(pct(0.35), 0.25, 0.55);
  const up: readonly number[] = [up0, clamp(pct(0.70), up0 + 0.15, 0.80)];

  const out: Array<[number, number]> = [];
  let tier = -1;
  let pending = -1, pendingFor = 0;
  for (let i = 0; i < smoothed.length; i++) {
    const sec = startSec + i * STEP;
    const v = smoothed[i]!;
    const want = desiredTier(v, tier, up);
    if (tier < 0) {                                    // first sample commits
      tier = want;
      out.push([0, tier]);
      continue;
    }
    if (want === tier) { pending = -1; pendingFor = 0; continue; }
    if (want === pending) {
      pendingFor += STEP;
      if (pendingFor >= DEBOUNCE_SEC) {
        tier = want;
        out.push([sec - startSec, tier]);
        pending = -1; pendingFor = 0;
      }
    } else {
      pending = want; pendingFor = 0;
    }
  }
  return out.length ? out : [[0, 1]];
}

/** Sections as explicit `set` cues, one per zone per boundary. */
export function sectionCues(
  env: ReadonlyArray<readonly [number, number]> | undefined,
  startSec: number, endSec: number,
): SetCue[] {
  // The classic baseline predates sections: it held one look for the whole
  // song, and the A/B comparison is only honest if B still does.
  const segs = styleVariant() === "classic"
    ? ([[0, 1]] as Array<[number, number]>)
    : sections(env, startSec, endSec);
  const out: SetCue[] = [];
  for (const [sec, tierIdx] of segs) {
    const look = TIERS[tierIdx]!;
    const t = Math.round(sec * 1000);
    const note = ["hush", "verse", "chorus"][tierIdx]!;
    out.push(
      { t, bus: "LED", op: "set", zone: "towerL", eff: look.towers,
        level: look.towersLevel, detail: note },
      { t, bus: "LED", op: "set", zone: "towerR", eff: look.towers,
        level: look.towersLevel, detail: note },
      { t, bus: "LED", op: "set", zone: "door", eff: look.door,
        level: look.doorLevel, detail: note },
    );
  }
  return out;
}

/* ── Static texture ───────────────────────────────────────────────────── */

/**
 * The zones: block a generated scene carries. Ember cores inside whatever the
 * ring is doing; the towers on different palettes so the loud sections'
 * crossfade effects colour them differently; the door always faintly
 * glinting; towerR breathing out of phase with towerL.
 */
export const ZONES_BLOCK = {
  towerL: { center: "ember" as EffectName, palette: "haunt" },
  towerR: { center: "ember" as EffectName, palette: "moonlight", phase: 1.3 },
  door: { overlay: "sparkle", palette: "ember" },
} as const;

/** Everything the audition needs, windowed to the clip and sorted. */
export function trackCues(
  onsets: Readonly<Record<string, readonly Onset[]>>,
  env: ReadonlyArray<readonly [number, number]> | undefined,
  startSec: number, endSec: number,
  zoneOverrides: Partial<Record<BandName, ZoneId>> = {},
  active: Partial<Record<BandName, boolean>> = {},
): Cue[] {
  const cues: Cue[] = [...sectionCues(env, startSec, endSec)];
  for (const band of Object.keys(BAND_STYLE) as BandName[]) {
    const hits = onsets[band];
    // Muted in the band editor = absent from the audition. Audition only:
    // the export ignores mutes, which are a listening tool, not a decision.
    if (hits && active[band] !== false)
      cues.push(...bandStrikes(band, hits, startSec, endSec,
                               zoneOverrides[band]));
  }
  cues.sort((a, z) => a.t - z.t);
  return cues;
}
