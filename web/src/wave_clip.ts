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

/* ── Loop points ──
   A looping scene whose seam lands mid-note clicks every time round. The
   fix is to put both ends on a transient, which the detector has already
   found — so this is a search, not a guess. */

/** Every onset in the analysis, in time order, across all bands. */
export function onsetTimes(
  onsets: Readonly<Record<string, ReadonlyArray<readonly [number, ...unknown[]]> | undefined>>,
): number[] {
  const t: number[] = [];
  for (const hits of Object.values(onsets)) for (const h of hits ?? []) t.push(h[0]);
  return t.sort((a, z) => a - z);
}

/** Nearest of `times` to `sec`, or `sec` itself if none is within reach. */
const nearest = (times: number[], sec: number, within: number): number => {
  let best = sec, gap = within;
  for (const t of times) {
    const d = Math.abs(t - sec);
    if (d < gap) { gap = d; best = t; }
  }
  return best;
};

/**
 * The clip with both ends nudged onto the nearest onsets. Half a second is
 * about as far as an edit can move before it stops being the edit asked
 * for; a snap that would collapse the clip leaves the end where it was.
 */
export function snapClip(times: number[], clip: WaveClip,
                         within = 0.5): { clip: WaveClip; moved: number } {
  const start = nearest(times, clip.start, within);
  let end = nearest(times, clip.end, within);
  if (end - start < 0.25) end = clip.end;
  const moved = Math.abs(start - clip.start) + Math.abs(end - clip.end);
  return { clip: { start, end }, moved };
}
