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

import { EDGE_SLOP, WaveView, type WaveClip, type WaveData } from "./waveform_view.js";

export interface WaveformDeps {
  /**
   * Fired when the audition starts and stops, and on every frame while it
   * plays, so the host can run the light preview from the same position.
   */
  onAudition: (playing: boolean, positionMs: number) => void;
  /** Container id, for the rare page that mounts this somewhere else. */
  containerId?: string;
}

export interface WaveformApi {
  /** Draw a track, or pass null to clear the editor. */
  show(trackId: string | null): void;
  /** The current in/out points in seconds, or null when nothing is selected. */
  clip(): WaveClip | null;
  /** Stop the audition. Safe to call when nothing is playing. */
  stop(): void;
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
    return { show: () => {}, clip: () => null, stop: () => {}, destroy: () => {} };
  }

  const view = new WaveView();

  /* ── Chrome ──────────────────────────────────────────────────────────
     Built here rather than in the template because the template is generated
     and this panel is the only thing that needs any of it. Layout is inline
     for the same reason: four rules, and no stylesheet to keep in step. */
  const mk = <K extends keyof HTMLElementTagNameMap>(
    tag: K, css: string, text = ""): HTMLElementTagNameMap[K] => {
    const n = document.createElement(tag);
    n.style.cssText = css;
    if (text) n.textContent = text;
    return n;
  };

  const wrap = mk("div", "margin:8px 0");
  const row = mk("div", "display:flex;align-items:center;gap:12px;flex-wrap:wrap;"
    + "margin-top:6px;font:12px/1.6 var(--f-data),ui-monospace,monospace;color:var(--ink-2)");
  const play = mk("button", "min-width:7em", "Audition");
  play.type = "button";
  play.disabled = true;
  play.title = "Play just the selected region, on a loop";
  const readout = mk("span", "font-variant-numeric:tabular-nums");
  const sens = document.createElement("input");
  sens.type = "range";
  sens.min = "0.3";
  sens.max = "3";
  sens.step = "0.05";
  sens.value = "1.1";
  sens.style.cssText = "width:130px;vertical-align:middle";
  sens.title = "Onset sensitivity — higher finds more, and more of it is noise";
  const sensVal = mk("span", "min-width:2.6em;font-variant-numeric:tabular-nums", "1.10");
  const sensLbl = mk("label", "display:flex;align-items:center;gap:6px", "sens");
  sensLbl.append(sens, sensVal);
  const note = mk("p", "margin:4px 0 0;color:var(--ink-2)");
  row.append(play, readout, sensLbl);
  wrap.append(view.el, row, note);
  host.append(wrap);

  const say = (msg: string, err = false): void => {
    note.textContent = msg;
    note.classList.toggle("err", err);
  };

  let trackId: string | null = null;
  let clip: WaveClip | null = null;
  /** Bumped per request, so a slow analysis cannot land on top of a newer one. */
  let token = 0;

  /* ── Formatting ──
     Three formats, because three different things read them: the readout is
     for a human judging an edit, and the two inputs are for ffmpeg. */

  /** m:ss.s — a tenth of a second is worth seeing when you are placing an edit. */
  const clock = (s: number): string =>
    `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

  /** m:ss for `trkStart`. Floored, never rounded: a start that rounds *up* eats
      the first transient of the clip you just spent a minute lining up. */
  const mmss = (s: number): string =>
    `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  /**
   * Push the selection into the Tracks option row.
   *
   * This is the entire output of the editor. The inputs are also typed into by
   * hand, so the synthetic `input` event goes out too — anything that watches
   * them should not be able to tell a drag from a keystroke.
   */
  function pushOpts(): void {
    if (!clip) return;
    const write = (id: string, value: string): void => {
      const el = document.getElementById(id) as HTMLInputElement | null;
      if (!el) return;
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    write("trkStart", mmss(clip.start));
    write("trkTake", (clip.end - clip.start).toFixed(1));
  }

  function sync(): void {
    view.clip = clip;
    view.draw();
    readout.textContent = clip
      ? `start ${clock(clip.start)}  ·  length ${clock(clip.end - clip.start)}`
      : "drag across the waveform to pick a clip";
    play.disabled = !clip || !view.data;
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
    return { id, duration: dur, peaks, onsets: b?.onsets ?? {} };
  }

  async function analyse(id: string): Promise<WaveData | string> {
    try {
      const r = await fetch(
        `/api/waveform/${encodeURIComponent(id)}?sensitivity=${encodeURIComponent(sens.value)}`);
      // A missing track is an ordinary outcome — the panel can be showing a row
      // the server has since deleted — so it reads as a message, not a throw.
      if (r.status === 404) return `No waveform for “${id}” — the studio has not analysed it.`;
      if (!r.ok) return `Analysis failed — HTTP ${r.status}.`;
      return toWave(id, await r.json());
    } catch (err) {
      // Static mode: the page is open without tools/studio.py behind it.
      return `Could not reach the studio — ${String(err)}`;
    }
  }

  /** Band hit counts, in the same shape the track list prints them. */
  const counts = (d: WaveData): string =>
    Object.entries(d.onsets)
      .map(([k, v]) => `${k.replace("onset_", "")} ${v?.length ?? 0}`)
      .join(" · ") || "no onsets at this sensitivity";

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
    // not throw away in and out points already placed by hand.
    clip = clip
      ? { start: clamp(clip.start, 0, res.duration), end: clamp(clip.end, 0, res.duration) }
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
    sync();
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
    deps.onAudition(true, audio.currentTime * 1000);
    raf = requestAnimationFrame(frame);
  }

  function stop(msg?: string): void {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    playing = false;
    audio.pause();
    audio.muted = true;
    play.textContent = "Audition";
    play.setAttribute("aria-pressed", "false");
    view.playhead = null;
    view.draw();
    deps.onAudition(false, (clip?.start ?? 0) * 1000);
    if (msg) say(msg, true);
  }

  function start(): void {
    if (!clip || !view.data) return;
    playing = true;
    audio.muted = false;               // the button press is the consent
    audio.volume = 0.6;                // modest — this is a check, not a mix
    seekIntoClip();
    void audio.play().catch(() => stop("Could not play that track."));
    play.textContent = "Stop";
    play.setAttribute("aria-pressed", "true");
    raf = requestAnimationFrame(frame);
  }

  play.addEventListener("click", () => { if (playing) stop(); else start(); });
  audio.addEventListener("error", () => {
    if (playing) stop(`Could not load audio for “${trackId ?? ""}”.`);
  });

  /* ── Sensitivity ── */
  let sensTimer = 0;
  sens.addEventListener("input", () => {
    sensVal.textContent = (+sens.value).toFixed(2);
    // Every change is a fresh analysis on the server; dragging the slider must
    // not turn into thirty of them.
    window.clearTimeout(sensTimer);
    sensTimer = window.setTimeout(() => { void load(); }, 300);
  });

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
      host.hidden = id === null;       // nothing selected, nothing to show
      trackId = id;
      clip = null;                     // a new track's in/out points are its own
      // No extension — the studio resolves the id to whichever container the
      // import landed in, so a WAV or FLAC track auditions like any other.
      if (id) audio.src = `/api/track/${encodeURIComponent(id)}`;
      else audio.removeAttribute("src");
      void load();
    },
    clip: () => clip,
    stop: () => stop(),
    destroy(): void {
      stop();
      window.clearTimeout(sensTimer);
      token++;                         // orphan any analysis still in flight
      view.destroy();
      wrap.remove();
    },
  };
}
