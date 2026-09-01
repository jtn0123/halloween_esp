/**
 * The three onset bands, and what each one drives.
 *
 * This is the single place that knows a band's frequency range, the zone it
 * lights, the colour it lights it, and how fast that fades. It used to be
 * spread across three files — the scene generator in tracks.ts, the tick
 * colours in waveform_view.ts, and the detector in analyze.py — which is how
 * you end up with a violet lane in the editor and a red pulse in the show.
 *
 * The Python side (tools/analyze.py, BANDS) owns the actual detection and its
 * refractory periods. What is duplicated here is only the presentation and the
 * defaults for a generated scene; the ranges are repeated so the panel can say
 * what a band *is* without a round trip.
 */

import type { ZoneId } from "./types.js";

export type BandName = "onset_low" | "onset_mid" | "onset_high";

export interface BandInfo {
  name: BandName;
  /** What the panel calls it. */
  label: string;
  /** Range in Hz, as tools/analyze.py splits it. */
  lo: number;
  hi: number;
  /**
   * Shortest spacing the detector will allow between two hits in this band,
   * in seconds — the refractory period from analyze.py. Bass needs the most
   * room; a cymbal the least. It sets the ceiling on a band's hit count, which
   * is why the three counts are not directly comparable to each other.
   */
  minGap: number;
  /** Zone this band lights in a generated scene. */
  zone: ZoneId;
  /** Pulse colour, linear RGBW as scenes.yaml wants it. */
  rgbw: readonly [number, number, number, number];
  /** The same colour as CSS, for the editor's onset lanes. */
  ink: string;
  /** Per-frame decay of the pulse. Lower snaps, higher blooms. */
  decay: number;
}

export const BANDS: readonly BandInfo[] = [
  { name: "onset_low", label: "low", lo: 20, hi: 200, minGap: 0.16,
    zone: "door", rgbw: [1.0, 0.12, 0.02, 0.0], ink: "#ff2a1a", decay: 0.86 },
  { name: "onset_mid", label: "mid", lo: 200, hi: 2000, minGap: 0.11,
    zone: "towerL", rgbw: [0.66, 0.10, 1.0, 0.05], ink: "#a83aff", decay: 0.92 },
  { name: "onset_high", label: "high", lo: 2000, hi: 16000, minGap: 0.09,
    zone: "towerR", rgbw: [0.30, 1.0, 0.55, 0.0], ink: "#4dff8c", decay: 0.94 },
];

export const BAND_BY_NAME: Readonly<Record<BandName, BandInfo>> =
  Object.fromEntries(BANDS.map(b => [b.name, b])) as Record<BandName, BandInfo>;

/** Band colours by name, for the editor's lanes. */
export const BAND_INK: Readonly<Record<BandName, string>> =
  Object.fromEntries(BANDS.map(b => [b.name, b.ink])) as Record<BandName, string>;

/**
 * How the panel reports what was detected.
 *
 * A raw count ("low 679 · mid 601 · high 899") is the wrong number to show.
 * It cannot be compared across bands — each has its own refractory period, so
 * each has a different ceiling — and it says nothing about what you will
 * actually see. A rate against the zone it drives does both: "door 2.8/s" is
 * how often that lamp pulses, which is the thing being decided.
 *
 * @param overrides zone per band, when the scene does not use the defaults.
 */
export function bandSummary(counts: Readonly<Record<string, number>>,
                            durationSec: number | undefined,
                            overrides: Partial<Record<BandName, ZoneId>> = {}): string {
  const parts: string[] = [];
  for (const b of BANDS) {
    const n = counts[b.name];
    if (n === undefined) continue;
    const zone = overrides[b.name] ?? b.zone;
    // Without a duration the rate is unknowable, so show the count and say so
    // rather than dividing by a guess.
    parts.push(durationSec && durationSec > 0
      ? `${zone} ${(n / durationSec).toFixed(1)}/s`
      : `${zone} ${n}`);
  }
  // level_* entries appear when a band has no beat and falls back to following
  // loudness. That is a different thing from a pulse rate, so it is named.
  const levels = Object.keys(counts).filter(k => k.startsWith("level_"));
  if (levels.length) {
    parts.push(`${levels.map(k => k.replace("level_", "")).join("+")} follow loudness`);
  }
  return parts.join(" · ") || "no onsets";
}

/** The long-form explanation, for a tooltip. Written once, used in both panels. */
export const BAND_HELP =
  "How often each zone pulses. An onset is a jump in level, not loudness — "
  + "a kick drum registers, a sustained pad does not, however loud it is. "
  + "The three bands are not comparable to each other: each has its own "
  + "minimum spacing (low 0.16s, mid 0.11s, high 0.09s).";
