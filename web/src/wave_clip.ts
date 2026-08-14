/**
 * Clip arithmetic and persistence for the waveform editor.
 *
 * Split from waveform.ts for the 500-line cap; the seam is "pure functions
 * about a clip" vs "the DOM that edits one". Everything here is callable
 * without a page.
 */

import type { WaveClip } from "./waveform_view.js";

/* ── Formatting ──
   Three formats, because three different things read them: the readout is
   for a human judging an edit, and the two inputs are for ffmpeg. */

/** m:ss.s — a tenth of a second is worth seeing when you are placing an edit. */
export const clock = (s: number): string =>
  `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

/** m:ss for `trkStart`. Floored, never rounded: a start that rounds *up* eats
    the first transient of the clip you just spent a minute lining up. */
export const mmss = (s: number): string =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** "m:ss", "m:ss.s" or bare seconds → seconds; null when it isn't one yet. */
export const parseClock = (s: string): number | null => {
  const m = /^(?:(\d+):)?(\d+(?:\.\d+)?)$/.exec(s.trim());
  return m ? Number(m[1] ?? 0) * 60 + Number(m[2]) : null;
};

/* ── The remembered clip selection, per track ──
   A full-track selection is not saved — it is the default, and saving it
   would shadow a later, better one. Round 2 of the UX gauntlet: the knobs
   survived a reload, the twenty minutes of trimming did not. */

const clipKey = (id: string): string => `castle.clip.${id}`;

export function loadClip(id: string, dur: number): WaveClip | null {
  try {
    const c = JSON.parse(localStorage.getItem(clipKey(id)) ?? "null") as
      { start: number; end: number } | null;
    return c && Number.isFinite(c.start) && Number.isFinite(c.end)
        && c.start >= 0 && c.end > c.start && c.start < dur ? c : null;
  } catch { return null; }
}

export function saveClip(id: string, c: WaveClip | null,
                         dur: number | undefined): void {
  try {
    if (!c || (c.start === 0 && dur !== undefined && c.end >= dur - 0.05)) {
      localStorage.removeItem(clipKey(id));
    } else {
      localStorage.setItem(clipKey(id),
        JSON.stringify({ start: c.start, end: c.end }));
    }
  } catch { /* private mode: selections stay session-only */ }
}
