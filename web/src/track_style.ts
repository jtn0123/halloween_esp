/**
 * The style lab's machinery: the A/B baseline and the live knobs.
 *
 * Split from track_lights.ts purely for the 500-line cap; the seam is
 * clean — everything here EVALUATES the style table, nothing here expands
 * cues. style_lab.ts is the UI; track_lights.ts consumes styleFor(). The
 * rules are unchanged:
 *
 *   - "B" is the frozen pre-4K look, kept to be compared against, never
 *     shipped: styleFor(band, true) ignores the variant.
 *   - Knob tweaks DO export — what you audition is what ships.
 */

import type { BandName } from "./bands.js";
import { BAND_STYLE, type BandStyle } from "./track_lights.js";
import type { Rgbw } from "./types.js";

/* ── Phase-3 flavours, each behind a default-off toggle ───────────────
 * Session state like the knobs: turning one on colours the audition AND the
 * next export (what you audition is what ships), but nothing is on unless
 * the user chose it this session. The A/B baseline ignores them all. */
export interface Flavors { drift: boolean; takeover: boolean; swells: boolean }
const flavors: Flavors = { drift: false, takeover: false, swells: false };
export const setFlavor = (name: keyof Flavors, on: boolean): void => {
  flavors[name] = on;
};
export const getFlavors = (): Flavors => ({ ...flavors });
export const resetFlavors = (): void => {
  flavors.drift = flavors.takeover = flavors.swells = false;
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


/* ── Flavour arithmetic — digit twins of pulse_dynamics.py ── */

/** #1: one full lap of the band's triad per this many seconds. */
export const DRIFT_PERIOD_S = 60;

/** The drifted base colour: lerp AROUND the cycle by time. */
export function driftBase(colors: readonly Rgbw[], i: number, tMs: number): Rgbw {
  const n = colors.length;
  if (n < 2) return colors[0]!;
  const p = ((tMs / 1000) % DRIFT_PERIOD_S) / DRIFT_PERIOD_S;
  const pos = (i + p * n) % n;
  const k = Math.floor(pos);
  const f = pos - k;
  const a = colors[k]!, b = colors[(k + 1) % n]!;
  return [0, 1, 2, 3].map(c =>
    Math.round((a[c]! + (b[c]! - a[c]!) * f) * 1000) / 1000) as unknown as Rgbw;
}

/** #2: the one warm family every band shares during a chorus. */
export const TAKEOVER_COLORS: readonly Rgbw[] = [
  [1.0, 0.55, 0.0, 0.0],    // gold
  [1.0, 0.25, 0.0, 0.05],   // flame orange
  [1.0, 0.0, 0.35, 0.0],    // hot rose
];
export const TAKEOVER_HOT: Rgbw = [1.0, 0.75, 0.1, 0.12];
