/**
 * The stems panel's strips — peaks, onset ticks and the audit marks.
 *
 * Split from stems_view.ts at the 500-line cap along the seam that was
 * already there: these paint one (layer, channel) picture onto a canvas
 * and say what they counted; the panel decides when and which. Pure
 * functions of the analysis, so the captions can be checked without a
 * page.
 */

import type { StemChannel } from "./api.js";
import { BANDS } from "./bands.js";

/** A channel onset with no mono ("both") onset within this window was never
 *  seen by the pipeline. 90 ms is just past the detector's own frame jitter. */
export const MISS_WINDOW = 0.09;

/** Onsets in `chan` that have no counterpart in `other` — the generic form
 *  behind both audits: channel-vs-mono, and left-vs-right in the stacked view. */
export function missedTimes(chan: StemChannel, other: StemChannel | undefined,
                     band: string): number[] {
  const seen = (other?.onsets[band] ?? []).map(h => h[0]).sort((a, z) => a - z);
  const out: number[] = [];
  for (const [t] of chan.onsets[band] ?? []) {
    // seen is sorted and short; a linear probe is cheaper than being clever.
    if (!seen.some(s => Math.abs(s - t) <= MISS_WINDOW)) out.push(t);
  }
  return out;
}

/** One channel's strip; returns the caption for the row. */
export function drawSingle(g: CanvasRenderingContext2D, d: StemChannel,
                           mono: StemChannel | undefined, dur: number,
                           channel: string, w: number, h: number,
                           ink: string): string {
  const peakH = h - 14;
  g.fillStyle = ink;
  g.globalAlpha = 0.85;
  const n = d.peaks.length || 1;
  for (let i = 0; i < n; i++) {
    const bh = Math.max(1, (d.peaks[i] ?? 0) * peakH);
    g.fillRect(i / n * w, (peakH - bh) / 2 + 2, Math.max(1, w / n - 0.4), bh);
  }
  g.globalAlpha = 1;
  let missed = 0;
  for (const b of BANDS) {
    g.fillStyle = b.ink;
    for (const [t] of d.onsets[b.name] ?? [])
      g.fillRect(t / dur * w, h - 11, 1.5, 9);
    if (channel !== "both") {
      // The audit marks: this channel heard it, the pipeline did not.
      // Along the TOP edge, opposite the band ticks — full-height bars
      // drowned the waveform on real music (hundreds of them).
      g.fillStyle = "#fff";
      for (const t of missedTimes(d, mono, b.name)) {
        g.fillRect(t / dur * w, 1, 1.5, 8);
        missed++;
      }
    }
  }
  const total = Object.values(d.onsets).reduce((s, v) => s + v.length, 0);
  if (channel === "both") return `${total} hits — the pipeline's own picture`;
  const seen = missed === 0
    ? "all seen by mono analysis"
    : `${missed} the mono analysis missed`;
  return `${total} hits · ${seen}`;
}

/** Left grows up from the centre line, right grows down. The two halves
 *  are scaled by each channel's TRUE level rather than its own normalised
 *  peaks — a channel that is genuinely quieter must look quieter, or the
 *  view answers the wrong question. */
export function drawStacked(g: CanvasRenderingContext2D, L: StemChannel,
                            R: StemChannel, dur: number, w: number, h: number,
                            ink: string): string {
  const mid = h / 2;
  const half = mid - 10;             // room for the tick lanes at each edge
  const top = Math.max(L.level, R.level) || 1;

  const halfBars = (d: StemChannel, dir: -1 | 1, alpha: number): void => {
    const scale = (d.level / top) * half;
    const n = d.peaks.length || 1;
    g.globalAlpha = alpha;
    g.fillStyle = ink;
    for (let i = 0; i < n; i++) {
      const bh = Math.max(1, (d.peaks[i] ?? 0) * scale);
      g.fillRect(i / n * w, dir < 0 ? mid - bh : mid,
                 Math.max(1, w / n - 0.4), bh);
    }
    g.globalAlpha = 1;
  };
  halfBars(L, -1, 0.9);
  halfBars(R, 1, 0.55);              // dimmer, so the halves read apart
  g.fillStyle = "rgba(255,255,255,0.35)";
  g.fillRect(0, mid - 0.5, w, 1);

  // One-sided hits: white ticks on the half that heard them alone.
  let lOnly = 0, rOnly = 0;
  for (const b of BANDS) {
    g.fillStyle = "#fff";
    for (const t of missedTimes(L, R, b.name)) {
      g.fillRect(t / dur * w, 1, 1.5, 8);
      lOnly++;
    }
    for (const t of missedTimes(R, L, b.name)) {
      g.fillRect(t / dur * w, h - 9, 1.5, 8);
      rOnly++;
    }
  }
  return lOnly === 0 && rOnly === 0
    ? "left and right hit together everywhere"
    : `${lOnly} left-only · ${rOnly} right-only hits (white ticks, top/bottom)`;
}
