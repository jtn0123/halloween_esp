/**
 * The STANDING light of a track scene: loudness tiers, section detection,
 * silence, the anticipation pre-dim, strike gates, and sustained swells.
 *
 * Split from track_lights.ts along the real seam — this file decides what
 * the castle holds between hits; track_lights.ts decides what each hit does.
 * The gating tables have digit twins in tools/pulse_dynamics.py.
 */

import type { BandName } from "./bands.js";
import { styleVariant } from "./track_style.js";
import type { Cue, EffectName, SetCue, StrikeCue } from "./types.js";

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

/* Tier index 3: real silence — a dramatic pause, a spoken-word gap. Near
 * black, not off: a faint ember means "holding its breath", where zero reads
 * as a power fault. The recovery flash when audio returns is free drama. */
export const SILENCE_TIER = 3;
export const SILENCE_LOOK: TierLook =
  { towers: "chill", towersLevel: 0.06, door: "ember", doorLevel: 0.08 };
/** env below this is silence for our purposes... */
const SILENCE_LEVEL = 0.04;
/** ...but only when it HOLDS — a breath between phrases is not a pause. */
const SILENCE_MIN_SEC = 2.0;

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
  return overlaySilence(out.length ? out : [[0, 1]], env, startSec, endSec);
}

/**
 * #5: carve real silences out of the tier timeline. Uses the RAW envelope,
 * not the smoothed one — smoothing would erode a pause from both ends until
 * a real four-second hole read as two seconds and slipped under the bar.
 */
function overlaySilence(
  segs: Array<[number, number]>,
  env: ReadonlyArray<readonly [number, number]>,
  startSec: number, endSec: number,
): Array<[number, number]> {
  const STEP = 0.25;
  const n = Math.max(1, Math.floor((endSec - startSec) / STEP) + 1);
  const need = Math.round(SILENCE_MIN_SEC / STEP);
  const inRun = new Array<boolean>(n).fill(false);
  let any = false;
  for (let i = 0; i < n;) {
    if (envAt(env, startSec + i * STEP) >= SILENCE_LEVEL) { i++; continue; }
    let j = i;
    while (j < n && envAt(env, startSec + j * STEP) < SILENCE_LEVEL) j++;
    if (j - i >= need) { inRun.fill(true, i, j); any = true; }
    i = j;
  }
  if (!any) return segs;

  const tierAt = (rel: number): number => {
    let t = segs[0]![1];
    for (const [s, tier] of segs) { if (s <= rel + 1e-9) t = tier; else break; }
    return t;
  };
  const merged: Array<[number, number]> = [];
  for (let k = 0; k < n; k++) {
    const rel = k * STEP;
    const tier = inRun[k] ? SILENCE_TIER : tierAt(rel);
    if (!merged.length || merged[merged.length - 1]![1] !== tier)
      merged.push([rel, tier]);
  }
  return merged;
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
  const NOTES = ["hush", "verse", "chorus", "silence"];
  const lookOf = (tier: number): TierLook =>
    tier === SILENCE_TIER ? SILENCE_LOOK : TIERS[tier]!;
  const trio = (t: number, look: TierLook, note: string, dim = 1): SetCue[] => {
    const lv = (l: number): number => Math.round(l * dim * 1000) / 1000;
    return [
      { t, bus: "LED", op: "set", zone: "towerL", eff: look.towers,
        level: lv(look.towersLevel), detail: note },
      { t, bus: "LED", op: "set", zone: "towerR", eff: look.towers,
        level: lv(look.towersLevel), detail: note },
      { t, bus: "LED", op: "set", zone: "door", eff: look.door,
        level: lv(look.doorLevel), detail: note },
    ];
  };
  const out: SetCue[] = [];
  segs.forEach(([sec, tierIdx], i) => {
    const prev = segs[i - 1];
    // #6: a dip right before the chorus slams in. A drop reads twice as big
    // from half a beat of darkness — every lighting desk knows this one.
    // Not out of silence (already dark), and not when the previous section
    // was too short to establish a level worth dipping from.
    if (tierIdx === 2 && prev && prev[1] !== 2 && prev[1] !== SILENCE_TIER
        && sec - prev[0] >= 0.7) {
      out.push(...trio(Math.round((sec - 0.45) * 1000), lookOf(prev[1]),
                       "predim", 0.45));
    }
    out.push(...trio(Math.round(sec * 1000), lookOf(tierIdx), NOTES[tierIdx]!));
  });
  return out;
}

/**
 * The section timeline as [ms, note] gates for strike expansion (#9). Built
 * from the section SET CUES rather than the envelope, because that is the
 * only section information an exported scene carries — the Python
 * generators reconstruct exactly this list from the YAML, so both sides
 * gate from the same source of truth.
 */
export function sectionGates(cues: readonly Cue[]): Array<[number, string]> {
  const out: Array<[number, string]> = [];
  for (const c of cues) {
    const note = c.detail;
    if (c.op !== "set" || note === undefined
        || !["hush", "verse", "chorus", "silence"].includes(note)) continue;
    if (!out.length || out[out.length - 1]![0] !== c.t) out.push([c.t, note]);
  }
  return out;
}

/** The section a moment lives in — twin of gate_note in pulse_dynamics.py. */
export function gateNote(gates: ReadonlyArray<readonly [number, string]>,
                         tMs: number): string | undefined {
  let note: string | undefined;
  for (const [gt, n] of gates) { if (gt <= tMs) note = n; else break; }
  return note;
}

/** What a section does to one band's hit: null = skip, else an intensity
 *  multiplier. Same table as gate_mul in gen_esphome.py. */
export function gateMul(band: BandName,
                        gates: ReadonlyArray<readonly [number, string]>,
                        tMs: number): number | null {
  let note: string | undefined;
  for (const [gt, n] of gates) { if (gt <= tMs) note = n; else break; }
  if (note === "silence") return null;      // nothing strikes in a held pause
  if (note === "hush") {
    if (band === "onset_high") return null; // quiet passages lose the glitter
    if (band === "onset_mid") return 0.5;   // and the voices whisper
  }
  return 1;
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

/**
 * #4: sustained-energy swells — a slow tower bloom wherever the envelope
 * holds a loud plateau. These export as EXPLICIT strike cues (attack does
 * the shaping), so the generators need no new code and parity is free.
 */
export function sustainedSwells(
  env: ReadonlyArray<readonly [number, number]> | undefined,
  startSec: number, endSec: number,
): StrikeCue[] {
  if (!env || !env.length) return [];
  const STEP = 0.25, LEVEL = 0.6, MIN_SEC = 3.0;
  const n = Math.max(1, Math.floor((endSec - startSec) / STEP) + 1);
  const out: StrikeCue[] = [];
  for (let i = 0; i < n;) {
    if (envAt(env, startSec + i * STEP) < LEVEL) { i++; continue; }
    let j = i;
    while (j < n && envAt(env, startSec + j * STEP) >= LEVEL) j++;
    if ((j - i) * STEP >= MIN_SEC) {
      out.push({
        t: Math.round(i * STEP * 1000), bus: "LED", op: "strike", ms: 2000,
        targets: ["towerL", "towerR"], intensity: 0.35,
        color: [0.45, 0.0, 0.9, 0.0], decay: 0.995, attack: 900,
        detail: "swell",
      });
    }
    i = j;
  }
  return out;
}

