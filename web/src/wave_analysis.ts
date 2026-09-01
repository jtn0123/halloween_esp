/**
 * What the clip editor KNOWS: its state, and how the server fills it.
 *
 * Split out of waveform.ts (grade report 2026-09-01 C1) along the seam the
 * file's own shape was already asking for. What stayed there is the panel —
 * chrome, pointer gestures, the audition element, the two-way bridge to the
 * Options row. What came here is the model: the four pieces of mutable state
 * every one of those halves was closing over, and the request that replaces
 * them.
 *
 * The state used to be four `let`s inside a 393-line closure, which meant
 * nothing could be read or written from outside it and nothing could be
 * tested without a DOM. It is one object now, passed in — so `loadWave` is
 * an ordinary async function you can hand a state, a stub `say` and a
 * counting `sync` to, and `toWave` is a pure body-check with no closure at
 * all.
 */

import { api } from "./api.js";
import { bandSummary } from "./bands.js";
import type { BandEditor } from "./band_editor.js";
import { WaveView, type WaveClip, type WaveData } from "./waveform_view.js";
import { loadClip } from "./wave_clip.js";

/**
 * Everything the editor's halves share and mutate.
 *
 * One object rather than four closure variables, because the drag handler,
 * the audition loop and the analysis all read and write the same selection —
 * and the drag has to see the analysis's answer the instant it lands.
 */
export interface WaveState {
  /** The canvas, and everything drawn on it: data, selection, playhead. */
  view: WaveView;
  /** The track on show, null when the editor is cleared. */
  trackId: string | null;
  /** In and out points in seconds, null when nothing is selected. */
  clip: WaveClip | null;
  /** Bumped per request, so a slow analysis cannot land on top of a newer
   *  one — and so `destroy()` can orphan whatever is still in flight. */
  token: number;
}

export const newWaveState = (): WaveState =>
  ({ view: new WaveView(), trackId: null, clip: null, token: 0 });

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/** Either the data, or the sentence to show instead of it. */
export function toWave(id: string, body: unknown): WaveData | string {
  // The body comes from another process, so check the two fields the drawing
  // genuinely cannot survive being wrong. The rest degrades quietly.
  const b = body as Partial<WaveData> | null;
  const peaks = b?.peaks, dur = b?.duration;
  if (!Array.isArray(peaks) || typeof dur !== "number" || !(dur > 0))
    return `Analysis for “${id}” came back without usable peaks.`;
  return { id, duration: dur, peaks, onsets: b?.onsets ?? {},
           ...(b?.env ? { env: b.env } : {}) };
}

export async function analyse(id: string, bands: BandEditor): Promise<WaveData | string> {
  try {
    const r = await api.waveform(id, bands.query());
    // A missing track is an ordinary outcome — the panel can be showing a row
    // the server has since deleted — so it reads as a message, not a throw.
    if (r.status === 404) return `No waveform for “${id}” — the studio has not analysed it.`;
    if (r.body === null) return `Analysis failed — HTTP ${r.status}.`;
    return toWave(id, r.body);
  } catch (err) {
    // Static mode: the page is open without tools/studio.py behind it.
    return `Could not reach the studio — ${String(err)}`;
  }
}

/** What was found, in the same words the track list uses. Also reports the
 *  tally back to the band editor, which draws it beside each slider. */
export function counts(d: WaveData, bands: BandEditor): string {
  const n: Record<string, number> =
    Object.fromEntries(Object.entries(d.onsets).map(([k, v]) => [k, v?.length ?? 0]));
  bands.report(n, d.duration);
  const s = bandSummary(n, d.duration, bands.zones());
  return s === "no onsets" ? "no onsets at this sensitivity" : s;
}

/** The panel's side of a load: the two things this module must not own —
 *  a line of prose on the screen, and the redraw that follows a change. */
export interface LoadDeps {
  bands: BandEditor;
  say: (msg: string, err?: boolean) => void;
  /** The editor's full redraw: overlay, persistence, onClipChange. */
  sync: () => void;
  /** Push the new selection into the Options row's START/LENGTH. Only on
   *  the path that actually produced a selection — an error must not blank
   *  boxes the operator typed into. */
  pushOpts: () => void;
}

/** Fetch, check, and settle `st` on the answer. Everything visible happens
 *  through `d`; the only things this touches directly are the four fields. */
export async function loadWave(st: WaveState, d: LoadDeps): Promise<void> {
  const id = st.trackId;
  if (!id) {
    st.view.data = null;
    st.view.message = "No track selected.";
    d.say("");
    d.sync();
    return;
  }
  const mine = ++st.token;
  d.say(`Analysing ${id}…`);
  const res = await analyse(id, d.bands);
  if (mine !== st.token) return;                // a newer request is already out
  if (typeof res === "string") {
    st.view.data = null;
    st.view.message = res;
    d.say(res, true);
    d.sync();
    return;
  }
  st.view.data = res;
  st.view.message = "";
  // First look shows the whole track; a re-analysis at a new sensitivity must
  // not throw away in and out points already placed by hand. And a clip
  // dialed in last session comes back (round 2: the knobs survived a
  // reload, the twenty minutes of trimming did not — backwards).
  const remembered = st.clip ?? loadClip(id, res.duration);
  st.clip = remembered
    ? { start: clamp(remembered.start, 0, res.duration),
        end: clamp(remembered.end, 0, res.duration) }
    : { start: 0, end: res.duration };
  d.say(counts(res, d.bands));
  d.sync();
  d.pushOpts();
}
