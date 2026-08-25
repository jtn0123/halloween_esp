/**
 * The transport — play, pause, seek, blackout, and the keyboard.
 *
 * The rule this module exists to enforce: **audio is never started by page
 * load, by picking a scene, by toggling a mode, or by any stray click. It
 * starts in `play()` and only there.** A preview tool that makes noise you did
 * not ask for is a bad preview tool, and this is the single place that can be
 * checked to know it does not.
 *
 * Everything else here is clock arithmetic. `show.ts` owns what the lights
 * look like; this owns when.
 */

import type { RenderedAudio } from "./audio.js";
import type { Synth } from "./synth.js";
import { rebuildLightsAt, type ShowState } from "./show.js";
import type { Scene } from "./types.js";
import { el as byId } from "./dom.js";

export interface TransportDeps {
  state: ShowState;
  rendered: RenderedAudio;
  synth: Synth;
  /** Which source plays: the shipped files, or the live synth. */
  getMode: () => "rendered" | "synth";
  /** Redraw the cue sheet, ticks and scene labels when the scene changes. */
  onSceneChange: (sc: Scene) => void;
  /**
   * Silence every player this module does NOT own — the track-row preview,
   * the clip audition, the codec A/B. Pause and Stop are the user saying
   * "quiet", and "quiet except the song in the panel below" reads as a stop
   * button that does not work. Deliberately NOT called from loadScene: the
   * audition adopts clip changes by reloading the scene mid-play.
   */
  stopExternal?: () => void;
  /** Whether an external player is sounding. With the button showing Pause
   *  for a row preview, pressing it must pause THAT — not start the scene. */
  isExternalPlaying?: () => boolean;
  /** Blackout happened — the button or Esc. The host mirrors it to the
   *  castle the way scene picks are mirrored: a scene fired on the porch
   *  from this desk must be stoppable from the same transport, or ■ Stop
   *  silences the laptop while the castle plays on. */
  onBlackout?: () => void;
}

export class Transport {
  private slidersApplied = false;

  constructor(private readonly d: TransportDeps) {}

  private get st(): ShowState {
    return this.d.state;
  }

  /** Elapsed ms right now, whether running or held. */
  elapsed(): number {
    const st = this.st;
    return st.running ? performance.now() - st.t0 : st.held;
  }

  setPlaying(on: boolean): void {
    const st = this.st;
    if (on === st.running) {
      this.syncUI();
      return;
    }
    if (on) {
      st.t0 = performance.now() - st.held;
    } else {
      st.held = Math.min(st.scene.dur, performance.now() - st.t0);
      this.d.rendered.pauseAll();
      this.d.synth.stopWind(0.4);
    }
    st.running = on;
    this.syncUI();
  }

  /** Start playback. The only path that may produce sound. */
  play(): void {
    const st = this.st;
    this.d.synth.arm();                 // inside a user gesture; silent alone

    if (!this.slidersApplied && this.d.synth.armed) {
      // Sliders moved before the audio graph existed had nothing to talk to,
      // so replay their current values now that it does.
      this.slidersApplied = true;
      for (const id of ["hall", "trem", "vol"]) {
        byId(id)?.dispatchEvent(new Event("input"));
      }
    }

    if (st.held >= st.scene.dur) st.held = 0;
    // Always re-derive the lights for where we are starting from. Without
    // this, Play after Stop resumed a stage that Stop had forced to black and
    // ran the whole scene with every zone off.
    rebuildLightsAt(st, st.scene, st.held);
    this.setPlaying(true);

    if (this.d.getMode() === "rendered") this.d.rendered.play(st.scene, st.held);
    else this.d.synth.startWind();
  }

  toggle(): void {
    if (this.st.running) {
      // The audition slaves this clock from outside (syncTo), so pausing the
      // clock alone leaves the song playing and the button looking broken.
      this.d.stopExternal?.();
      this.setPlaying(false);
    } else if (this.d.isExternalPlaying?.()) {
      // A row preview is sounding and the button says Pause: pause means
      // that, not "also start the scene".
      this.d.stopExternal?.();
      this.syncUI();
    } else {
      this.play();
    }
  }

  /**
   * Seek. Moves the clock, rebuilds the lights, and drags the audio with it so
   * sound and light stay locked while scrubbing.
   */
  seekTo(ms: number): void {
    const st = this.st;
    const t = Math.max(0, Math.min(st.scene.dur, ms));
    if (st.running) st.t0 = performance.now() - t;
    else st.held = t;
    rebuildLightsAt(st, st.scene, t);
    this.d.rendered.seek(st.scene.id, t);
  }

  /**
   * Slave the show clock to an outside audio source — the clip audition —
   * so the lights run against audio this module is not playing.
   *
   * `seekTo` is the wrong tool for this. It calls `rebuildLightsAt`, which
   * zeroes every flash: correct for one seek, but called every frame it wipes
   * each pulse before it can decay and the stage just stays dark. This moves
   * the clock and nothing else, and lets `step` do the rendering.
   */
  syncTo(ms: number): void {
    const st = this.st;
    // Running, but never through play() — no audio starts from here.
    if (!st.running) {
      st.running = true;
      this.syncUI();
    }
    // The clip looped back to its in point, so the cues are due again. The
    // tolerance keeps a frame of jitter from counting as a rewind.
    if (ms + 40 < this.elapsed()) st.fired.clear();
    st.t0 = performance.now() - ms;
  }

  /** Load a scene. Plays only if explicitly asked, or if already playing. */
  loadScene(sc: Scene, opts: { play?: boolean } = {}): void {
    const st = this.st;
    this.d.synth.newShowBus();
    this.d.rendered.stopAll();
    st.scene = sc;
    st.held = 0;
    st.t0 = performance.now();
    rebuildLightsAt(st, sc, 0);
    this.d.onSceneChange(sc);
    if (opts.play) this.play();
    else this.setPlaying(false);
  }

  /** Everything off, clock to zero. EVERYTHING — including the players this
   *  module does not own. */
  blackout(): void {
    const st = this.st;
    this.d.stopExternal?.();
    this.setPlaying(false);
    st.held = 0;
    rebuildLightsAt(st, st.scene, 0);
    for (const id of ["towerL", "towerR", "door"] as const) st.eff[id] = "off";
    this.d.synth.newShowBus();
    this.d.rendered.stopAll();
    this.d.synth.stopWind();
    this.d.onBlackout?.();
  }

  restart(): void {
    this.loadScene(this.st.scene, { play: this.st.running });
  }

  /** Re-read the world and redraw the play button — for external players. */
  refreshUI(): void {
    this.syncUI();
  }

  private syncUI(): void {
    // A sounding row preview counts: the button showing a dead ▶ Play while
    // music audibly plays read as "the transport is broken" (round 2).
    const running = this.st.running || (this.d.isExternalPlaying?.() ?? false);
    const icon = byId("playIcon");
    const label = byId("playLabel");
    const btn = byId("play");
    if (icon) icon.innerHTML = running ? "&#10073;&#10073;" : "&#9654;";
    if (label) label.textContent = running ? "Pause" : "Play";
    btn?.setAttribute("aria-pressed", String(running));
  }

  /** Drag-to-seek on the scrub bar. */
  bindScrub(scrub: HTMLElement): void {
    const posFrom = (e: PointerEvent): number => {
      const r = scrub.getBoundingClientRect();
      const k = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      return k * this.st.scene.dur;
    };
    let scrubbing = false;
    scrub.addEventListener("pointerdown", (e) => {
      scrubbing = true;
      scrub.setPointerCapture(e.pointerId);
      this.seekTo(posFrom(e));
    });
    scrub.addEventListener("pointermove", (e) => {
      if (scrubbing) this.seekTo(posFrom(e));
    });
    const end = (): void => { scrubbing = false; };
    scrub.addEventListener("pointerup", end);
    scrub.addEventListener("pointercancel", end);
  }

  /**
   * Keyboard shortcuts. Ignored while a form field has focus, so typing a
   * track id into the Tracks panel doesn't scrub the show.
   */
  bindKeys(onMute: () => void, onPickScene: (index: number) => void): void {
    document.addEventListener("keydown", (e) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase() ?? "";
      if (tag === "input" || tag === "textarea" || tag === "select") return;

      const step = e.shiftKey ? 5000 : 1000;
      const el = this.elapsed();

      switch (e.key) {
        case " ":
        case "k":
          e.preventDefault();
          this.toggle();
          break;
        case "ArrowRight": e.preventDefault(); this.seekTo(el + step); break;
        case "ArrowLeft":  e.preventDefault(); this.seekTo(el - step); break;
        case "Home":       e.preventDefault(); this.seekTo(0); break;
        case "r": case "R": this.restart(); break;
        case "m": case "M": onMute(); break;
        case "Escape": this.blackout(); break;
        default:
          if (/^[1-9]$/.test(e.key)) onPickScene(Number(e.key) - 1);
      }
    });
  }
}
