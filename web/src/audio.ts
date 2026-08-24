/**
 * Rendered-audio playback — the exact MP3s that ship in flash.
 *
 * Separate from synth.ts on purpose. The synth is a WebAudio graph for
 * auditioning parameter changes before committing to a re-render; this plays
 * the finished files byte-for-byte as the device will, which is what makes the
 * previewer a dry run rather than an impression.
 *
 * The mute rule lives here and is the one piece of behaviour worth defending:
 * **muted by default, always**, and mute is expressed as the element's own
 * `muted` property rather than as volume 0. Volume is also written by the
 * fade-in and by the master slider, so a volume-based mute is one stray write
 * away from being silently undone — which is exactly the bug that shipped
 * once already.
 */

import type { Scene } from "./types.js";

export type AudioMode = "rendered" | "synth";

export class RenderedAudio {
  /** Every scene's source, held as a STRING until first audition (G5): ten
   *  live <audio> elements from startup meant ten decoded buffers for the
   *  whole session, growing per scene. `els` holds only what has played. */
  private uris = new Map<string, string>();
  private els = new Map<string, HTMLAudioElement>();
  private fadeTimer: ReturnType<typeof setInterval> | null = null;
  /** The pending start: play() waits out `latency` before the element runs.
   *  Stop/pause/another play inside that window must cancel it, or the file
   *  starts AFTER the Stop — and a scene switched within the window starts
   *  both files (the outgoing one late). */
  private startTimer: ReturnType<typeof setTimeout> | null = null;

  /** Master level, 0..1. Scene `volume` multiplies on top of this. */
  volume = 0.6;
  /** Starts muted. Nothing here should make noise you did not ask for. */
  muted = true;
  /** Modelled decode spin-up, so the screen and the speaker agree. */
  latency = 70;

  /** `audio` maps scene id -> a source the element can take: the
   *  `data:audio/mpeg;base64,…` the portable build inlines, or the
   *  `/studio/scene-audio/<id>` URL the studio's lean page serves instead
   *  (same bytes, fetched when played). Nothing here tells them apart. */
  constructor(audio: Readonly<Record<string, string>>) {
    for (const [sid, uri] of Object.entries(audio)) this.uris.set(sid, uri);
  }

  /** The element for `sid`, created on first use. On a lean page (URL
   *  sources) `preload="none"` — the studio honours Range requests, so the
   *  first play fetches and seeking still works; eager preload pulled every
   *  scene's megabytes at page load for tracks that may never play (G3).
   *  A data: URI has no fetch to defer. */
  private ensure(sid: string): HTMLAudioElement | null {
    const got = this.els.get(sid);
    if (got) return got;
    const uri = this.uris.get(sid);
    if (uri === undefined) return null;
    const el = new Audio(uri);
    el.preload = uri.startsWith("data:") ? "auto" : "none";
    el.muted = true;
    this.els.set(sid, el);
    return el;
  }

  /** Drop every element but `keep` (G5): a decoded track the desk moved on
   *  from has no business holding its buffer for the rest of the session.
   *  Re-auditioning re-creates it from the uri, which is the cheap part. */
  private release(keep: string): void {
    for (const [sid, el] of this.els) {
      if (sid === keep) continue;
      el.pause();
      el.removeAttribute("src");
      el.load();
      this.els.delete(sid);
    }
  }

  /** Rendered files are preferred when we have any; otherwise the synth. */
  get defaultMode(): AudioMode {
    return this.uris.size ? "rendered" : "synth";
  }

  get count(): number {
    return this.uris.size;
  }

  has(sceneId: string): boolean {
    return this.uris.has(sceneId);
  }

  /** Current playback position of a scene's file, in ms. */
  positionMs(sceneId: string): number | null {
    const el = this.els.get(sceneId);
    return el ? el.currentTime * 1000 : null;
  }

  stopAll(): void {
    this.cancelStart();
    this.clearFade();
    for (const el of this.els.values()) {
      el.pause();
      el.currentTime = 0;
    }
  }

  pauseAll(): void {
    this.cancelStart();
    this.clearFade();
    for (const el of this.els.values()) el.pause();
  }

  /**
   * Start (or resume) a scene's file at `fromMs`.
   *
   * The volume ramps up over ~180 ms rather than starting at full. Cutting a
   * 44.1 kHz file in at an arbitrary sample is a step discontinuity — you hear
   * it as a click, and at the head of a loud scene it lands as a blast. The
   * ramp costs nothing and removes both.
   */
  play(sc: Scene, fromMs = 0): boolean {
    const el = this.ensure(sc.id);
    if (!el) return false;

    this.cancelStart();
    this.release(sc.id);
    const target = Math.min(1, this.volume * (sc.volume ?? 0.8));
    el.muted = this.muted;             // authoritative, independent of the fade
    el.loop = Boolean(sc.loop);
    el.volume = 0;
    try {
      el.currentTime = Math.max(0, fromMs) / 1000;
    } catch {
      // Seeking before metadata has loaded throws; the file starts at 0,
      // which is a better outcome than refusing to play at all.
    }

    this.startTimer = setTimeout(() => {
      this.startTimer = null;
      void el.play().catch(() => {
        // Autoplay policy, or the element was torn down mid-flight. Either
        // way there is nothing useful to do and nothing worth logging.
      });
      this.clearFade();
      const step = target / 12;
      this.fadeTimer = setInterval(() => {
        el.volume = Math.min(target, el.volume + step);
        if (el.volume >= target - 1e-6) this.clearFade();
      }, 15);
    }, this.latency);

    return true;
  }

  /** Drag a playing file to a new position, so light and sound stay locked. */
  seek(sceneId: string, ms: number): void {
    const el = this.els.get(sceneId);
    if (!el) return;
    try {
      el.currentTime = Math.max(0, ms) / 1000;
    } catch {
      // As above — an un-seekable element is not worth failing the seek for.
    }
  }

  /**
   * Apply the current volume and mute to every element.
   *
   * Kills any in-flight fade first: a running ramp would otherwise keep
   * writing toward its own target and undo whatever was just set.
   */
  apply(scenes: readonly Scene[]): void {
    this.clearFade();
    for (const sc of scenes) {
      const el = this.els.get(sc.id);
      if (!el) continue;
      el.muted = this.muted;
      el.volume = Math.min(1, this.volume * (sc.volume ?? 0.8));
    }
  }

  private cancelStart(): void {
    if (this.startTimer !== null) {
      clearTimeout(this.startTimer);
      this.startTimer = null;
    }
  }

  private clearFade(): void {
    if (this.fadeTimer !== null) {
      clearInterval(this.fadeTimer);
      this.fadeTimer = null;
    }
  }
}
