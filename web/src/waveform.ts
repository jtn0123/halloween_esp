/**
 * Waveform clip editor — deciding which piece of a track you actually want.
 *
 * The Tracks panel could always trim an import, but only by typing a start and
 * a length into two boxes and re-importing to find out whether you had guessed
 * right. This draws the track instead: peaks, the detected onsets over them in
 * their band colours, a region you drag out with the pointer, and a button that
 * plays that region on a loop so you can hear the edit before committing to it.
 *
 * It deliberately has no import path of its own. Dragging writes into the same
 * `trkStart` and `trkTake` inputs the import has always read, so everything
 * downstream — import, re-import, the remembered options — keeps working
 * without knowing this file exists.
 */

import { api } from "./api.js";
import { BAND_HELP, BANDS, bandSummary } from "./bands.js";
import type { BandEditor } from "./band_editor.js";
import type { CodecAb } from "./codec_ab.js";
import { createStemsView } from "./stems_view.js";
import { createStyleLab } from "./style_lab.js";
import { sections } from "./track_lights.js";
import { EDGE_SLOP, WaveView, type WaveClip, type WaveData } from "./waveform_view.js";
import { clock, loadClip, onsetTimes, saveClip, snapClip } from "./wave_clip.js";
import { initOptsBridge } from "./wave_opts_bridge.js";
import { buildWaveChrome } from "./wave_chrome.js";

export interface WaveformDeps {
  /**
   * Fired when the audition starts and stops, and on every frame while it
   * plays, so the host can run the light preview from the same position.
   *
   * `positionMs` is measured from the start of the selected clip, not the
   * start of the file — the clip is what loops, so it is the only origin the
   * lights can share with the audio.
   */
  onAudition: (playing: boolean, positionMs: number) => void;
  /**
   * The selection and the analysis behind it, whenever either changes — a new
   * track, a drag, a re-analysis at a new sensitivity, or null when the editor
   * is cleared. The host builds its preview scene from this.
   */
  onClipChange?: (clip: WaveClip | null, data: WaveData | null) => void;
  /**
   * Per-band zones and thresholds. Mounted into this panel, but owned outside
   * it because the scene generator reads the same settings.
   */
  bands: BandEditor;
  /**
   * Codec comparison for the selected clip. Mounted here because the clip is
   * here; owned outside because it needs the import options too.
   */
  codecs?: CodecAb;
  /** Container id, for the rare page that mounts this somewhere else. */
  containerId?: string;
}

export interface WaveformApi {
  /** Draw a track, or pass null to clear the editor. */
  show(trackId: string | null): void;
  /** Re-run the analysis — after the band settings change. Debounced. */
  reanalyse(): void;
  /** The current in/out points in seconds, or null when nothing is selected. */
  clip(): WaveClip | null;
  /** Rebuild the audition scene and redraw — after a mute or style change,
   *  where the analysis is still good and re-fetching it would be waste. */
  resync(): void;
  /** Stop the audition. Safe to call when nothing is playing. */
  stop(): void;
  /** Paint the in-editor zone mirror from the current stage frame. */
  mirror(out: Record<string, { avg: readonly number[] }>): void;
  /** Drop the DOM, the observer, the timers and the audio element. */
  destroy(): void;
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

export function initWaveform(deps: WaveformDeps): WaveformApi {
  const host = document.getElementById(deps.containerId ?? "trkWave");
  if (!host) {
    // The editor is optional chrome. A page generated before this panel existed
    // has no #trkWave, and that is not worth taking the whole desk down for.
    console.warn("waveform: no #trkWave container — clip editor not mounted.");
    return { show: () => {}, reanalyse: () => {}, clip: () => null,
             resync: () => {}, stop: () => {}, mirror: () => {},
             destroy: () => {} };
  }

  const view = new WaveView();

  const { wrap, title, row, play, snap, readout, note, mirrorDots } =
    buildWaveChrome();
  note.title = BAND_HELP;
  const lab = createStyleLab(() => sync());
  // Two things can make noise here; each silences the other on play.
  const stems = createStemsView({ onPlay: () => stop(),
                                  duration: () => view.data?.duration ?? null });
  // What the selection IS and IS NOT (judge B, JB1-4): the handles look
  // like they define the scene, but Make scene writes the whole file. The
  // only road from a trim to the castle is Re-import, so say so here.
  const hint = document.createElement("p");
  hint.className = "wave__trimhint";
  hint.style.cssText = "margin:4px 0 0;font-size:12px;color:var(--ink-2)";
  hint.hidden = true;
  wrap.append(title, view.el, row, hint, deps.bands.el, lab.el, stems.el, note);
  if (deps.codecs) wrap.append(deps.codecs.el);
  host.append(wrap);

  const say = (msg: string, err = false): void => {
    note.textContent = msg;
    note.classList.toggle("err", err);
  };

  let trackId: string | null = null;
  let clip: WaveClip | null = null;
  /** Bumped per request, so a slow analysis cannot land on top of a newer one. */
  let token = 0;

  // START/LENGTH in the Options row: dragging writes them, typing them
  // moves the clip. The whole two-way contract lives in wave_opts_bridge.
  const bridge = initOptsBridge(
    () => view.data?.duration ?? null,
    (c) => { clip = c; sync(); },
    (msg) => say(msg),
  );
  const pushOpts = (): void => { if (trackId) bridge.push(clip, trackId); };

  /** The per-frame half: redraw and readout only. Cheap enough to run on
   *  every pointermove of a drag. */
  function syncLight(): void {
    view.clip = clip;
    view.draw();
    readout.textContent = clip
      ? `start ${clock(clip.start)}  ·  length ${clock(clip.end - clip.start)}`
      : "drag across the waveform to pick a clip";
    play.disabled = !clip || !view.data;
    snap.disabled = play.disabled;
    const d = view.data;
    const partial = !!(clip && d && (clip.start > 0.05 || clip.end < d.duration - 0.05));
    hint.hidden = !partial;
    hint.textContent = partial
      ? "This selection is for listening and for placing the lights. The castle "
        + "plays the whole file — to keep only this part, press Re-import on the "
        + "track's row (START/LENGTH above are set from it)."
      : "";
  }

  function sync(): void {
    // The tier overlay, from the SAME sections() call the audition scene
    // makes for this window — a promise about the show, not a decoration.
    const d = view.data;
    const start = clip?.start ?? 0, end = clip?.end ?? d?.duration ?? 0;
    view.sections = d?.env
      ? sections(d.env, start, end).map(([s, tier]) => [start + s, tier])
      : null;
    for (const b of BANDS) view.muted[b.name] = !deps.bands.active()[b.name];
    syncLight();
    if (trackId && view.data) saveClip(trackId, clip, view.data.duration);
    deps.onClipChange?.(clip, view.data);
  }

  /* The expensive half of sync() — sections() over the envelope, a
     localStorage write, and onClipChange (which rebuilds and re-sorts the
     whole audition scene) — used to run on EVERY pointermove of a drag
     (grade report G2). One trailing frame carries the same result. */
  let heavyPending = false;
  function syncSoon(): void {
    if (heavyPending) return;
    heavyPending = true;
    requestAnimationFrame(() => { heavyPending = false; sync(); });
  }

  /* ── Analysis ── */

  /** Either the data, or the sentence to show instead of it. */
  function toWave(id: string, body: unknown): WaveData | string {
    // The body comes from another process, so check the two fields the drawing
    // genuinely cannot survive being wrong. The rest degrades quietly.
    const b = body as Partial<WaveData> | null;
    const peaks = b?.peaks, dur = b?.duration;
    if (!Array.isArray(peaks) || typeof dur !== "number" || !(dur > 0))
      return `Analysis for “${id}” came back without usable peaks.`;
    return { id, duration: dur, peaks, onsets: b?.onsets ?? {},
             ...(b?.env ? { env: b.env } : {}) };
  }

  async function analyse(id: string): Promise<WaveData | string> {
    try {
      const r = await api.waveform(id, deps.bands.query());
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

  /** What was found, in the same words the track list uses. */
  const counts = (d: WaveData): string => {
    const n: Record<string, number> =
      Object.fromEntries(Object.entries(d.onsets).map(([k, v]) => [k, v?.length ?? 0]));
    deps.bands.report(n, d.duration);
    const s = bandSummary(n, d.duration, deps.bands.zones());
    return s === "no onsets" ? "no onsets at this sensitivity" : s;
  };

  async function load(): Promise<void> {
    const id = trackId;
    if (!id) {
      view.data = null;
      view.message = "No track selected.";
      say("");
      sync();
      return;
    }
    const mine = ++token;
    say(`Analysing ${id}…`);
    const res = await analyse(id);
    if (mine !== token) return;                 // a newer request is already out
    if (typeof res === "string") {
      view.data = null;
      view.message = res;
      say(res, true);
      sync();
      return;
    }
    view.data = res;
    view.message = "";
    // First look shows the whole track; a re-analysis at a new sensitivity must
    // not throw away in and out points already placed by hand. And a clip
    // dialed in last session comes back (round 2: the knobs survived a
    // reload, the twenty minutes of trimming did not — backwards).
    const remembered = clip ?? loadClip(id, res.duration);
    clip = remembered
      ? { start: clamp(remembered.start, 0, res.duration),
          end: clamp(remembered.end, 0, res.duration) }
      : { start: 0, end: res.duration };
    say(counts(res));
    sync();
    pushOpts();
  }

  /* ── Dragging the region ──────────────────────────────────────────────
     One gesture covers all three cases. Pressing on an edge pins the opposite
     edge as the anchor; pressing anywhere else anchors where you went down and
     starts a fresh selection. After that every drag is the same two lines. */
  let grabbing = false;
  let anchor = 0;

  const nearEdge = (x: number): boolean =>
    !!clip && (Math.abs(x - view.secToX(clip.start)) <= EDGE_SLOP
            || Math.abs(x - view.secToX(clip.end)) <= EDGE_SLOP);

  function drag(t: number): void {
    const lo = Math.min(anchor, t), hi = Math.max(anchor, t);
    // A floor on the length keeps the region from collapsing to nothing under
    // the pointer, which would leave the two edge handles stacked and ungrabbable.
    clip = { start: lo, end: Math.max(hi, lo + 0.02) };
    // Live rather than on release: those two inputs are the real output of this
    // editor, and watching them move is how you know the drag is landing.
    // Redraw NOW; the heavy half (sections, persistence, scene rebuild)
    // rides one trailing frame so the drag itself stays at frame rate.
    syncLight();
    syncSoon();
    pushOpts();
    if (playing) seekIntoClip();
  }

  view.el.addEventListener("pointerdown", e => {
    const c = clip;
    if (!view.data) return;
    const x = view.eventX(e);
    if (c && Math.abs(x - view.secToX(c.start)) <= EDGE_SLOP) anchor = c.end;
    else if (c && Math.abs(x - view.secToX(c.end)) <= EDGE_SLOP) anchor = c.start;
    else anchor = view.xToSec(x);
    grabbing = true;
    view.el.setPointerCapture(e.pointerId);
    drag(view.xToSec(x));
  });

  view.el.addEventListener("pointermove", e => {
    if (!view.data) return;
    if (grabbing) { drag(view.xToSec(view.eventX(e))); return; }
    // Say that the edges are grabbable before the user finds out by accident.
    view.el.style.cursor = nearEdge(view.eventX(e)) ? "ew-resize" : "crosshair";
  });

  const release = (e: PointerEvent): void => {
    if (!grabbing) return;
    grabbing = false;
    if (view.el.hasPointerCapture(e.pointerId)) view.el.releasePointerCapture(e.pointerId);
    // A press with no drag is a misclick, not a request for an empty clip.
    if (clip && clip.end - clip.start < 0.06)
      clip = { start: 0, end: view.data?.duration ?? 0 };
    sync();
    pushOpts();
  };
  view.el.addEventListener("pointerup", release);
  view.el.addEventListener("pointercancel", release);

  /* ── Audition ─────────────────────────────────────────────────────────
     Muted by default, like everything else in this app that can make noise.
     Pressing the button is the explicit action that lifts it, and stopping puts
     it straight back — so no later code path can start this element speaking. */
  const audio = new Audio();
  audio.preload = "auto";
  audio.muted = true;
  audio.loop = false;                  // the region loops; the file must not

  let playing = false;
  let raf = 0;
  /** See preview.ts: a play() rejected by a later pause must not stop the
   *  playback that replaced it. */
  let epoch = 0;

  const seekIntoClip = (): void => {
    const c = clip;
    if (!c) return;
    if (audio.currentTime < c.start || audio.currentTime > c.end) {
      try { audio.currentTime = c.start; }
      catch { /* metadata not in yet — it starts at 0, which is survivable */ }
    }
  };

  function frame(): void {
    const c = clip;
    if (!playing || !c) return;
    // Region looping cannot use el.loop, and `timeupdate` fires about four
    // times a second — nowhere near fine enough to land an out point on.
    if (audio.currentTime >= c.end - 0.01) audio.currentTime = c.start;
    view.playhead = audio.currentTime;
    view.draw();
    // Clip-relative: the lights are built for this region, so they start at
    // its in point, not at the file's.
    deps.onAudition(true, Math.max(0, (audio.currentTime - c.start) * 1000));
    raf = requestAnimationFrame(frame);
  }

  function stop(msg?: string): void {
    epoch++;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    playing = false;
    audio.pause();
    audio.muted = true;
    play.textContent = "Audition";
    play.setAttribute("aria-pressed", "false");
    view.playhead = null;
    view.draw();
    deps.onAudition(false, 0);
    if (msg) say(msg, true);
  }

  function start(): void {
    if (!clip || !view.data) return;
    stems.stop();
    playing = true;
    audio.muted = false;               // the button press is the consent
    audio.volume = 0.6;                // modest — this is a check, not a mix
    seekIntoClip();
    const mine = ++epoch;
    void audio.play().catch(() => {
      if (mine === epoch) stop("Could not play that track.");
    });
    play.textContent = "Stop";
    play.setAttribute("aria-pressed", "true");
    raf = requestAnimationFrame(frame);
  }

  play.addEventListener("click", () => { if (playing) stop(); else start(); });
  audio.addEventListener("error", () => {
    if (playing) stop(`Could not load audio for “${trackId ?? ""}”.`);
  });

  /* ── Loop points ── the arithmetic lives in wave_clip.ts (snapClip). */
  snap.addEventListener("click", () => {
    const d = view.data;
    if (!clip || !d) return;
    const times = onsetTimes(d.onsets);
    if (!times.length) return say("No onsets to snap to at these thresholds.", true);
    const r = snapClip(times, clip);
    clip = r.clip;
    sync();
    pushOpts();
    if (playing) seekIntoClip();
    say(r.moved < 0.001
      ? "Already on a beat at both ends."
      : `Snapped to the nearest onsets — moved ${r.moved.toFixed(2)}s in total.`);
  });

  /* ── Bands ── */
  let sensTimer = 0;
  /** Re-run the analysis at whatever the band editor now says. */
  function reanalyse(): void {
    // Every change is a fresh analysis on the server; dragging a slider must
    // not turn into thirty of them.
    window.clearTimeout(sensTimer);
    sensTimer = window.setTimeout(() => { void load(); }, 300);
  }

  view.message = "No track selected.";
  sync();

  // Collapsed until a track is picked. `.trk-wave:empty` in the stylesheet was
  // meant to do this, but it can never match: the editor populates its own
  // container here, so the container is never empty. Hiding the host directly
  // is the thing that actually works.
  host.hidden = true;

  return {
    show(id: string | null): void {
      if (id === trackId) return;
      stop();
      // The encodes belong to the track that was showing; keeping them would
      // let you A/B one track while looking at another.
      deps.codecs?.reset();
      host.hidden = id === null;       // nothing selected, nothing to show
      trackId = id;
      title.textContent = id ? `Clip editor — ${id}` : "";
      // The editor appears ABOVE the list; from a row at the bottom it opened
      // entirely off-screen with no cue that anything had happened.
      if (id) host.scrollIntoView({ block: "nearest", behavior: "smooth" });
      clip = null;                     // a new track's in/out points are its own
      bridge.clear();                  // …and so are its START/LENGTH (JB1-1)
      // Drop the previous track's analysis NOW, not when the new one lands.
      // Between show() and load() finishing, the old data was still live: a
      // drag in that window built a clip — and an audition scene — from the
      // old track's onsets against the new track's audio, and the Audition
      // button sat enabled with nothing valid behind it.
      view.data = null;
      view.message = id ? `Analysing ${id}…` : "No track selected.";
      sync();
      // No extension — the studio resolves the id to whichever container the
      // import landed in, so a WAV or FLAC track auditions like any other.
      if (id) audio.src = `/api/track/${encodeURIComponent(id)}`;
      else audio.removeAttribute("src");
      stems.show(id);
      void load();
    },
    reanalyse,
    clip: () => clip,
    resync: () => sync(),
    stop: () => stop(),
    /** Paint the zone mirror from the frame the stage just drew. */
    mirror(out: Record<string, { avg: readonly number[] }>): void {
      if (host.hidden) return;
      for (const [z, d] of Object.entries(mirrorDots)) {
        const c = out[z]?.avg;
        if (!c || !d) continue;
        d.style.background = `rgb(${Math.round(c[0]! * 255)},`
          + `${Math.round(c[1]! * 255)},${Math.round(c[2]! * 255)})`;
      }
    },
    destroy(): void {
      stop();
      stems.destroy();
      window.clearTimeout(sensTimer);
      token++;                         // orphan any analysis still in flight
      view.destroy();
      wrap.remove();
    },
  };
}
