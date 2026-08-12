/**
 * Wiring only.
 *
 * Every other module in here owns one job and knows nothing about the others:
 * `show` computes pixels, `stage` draws, `audio` plays files, `synth` makes
 * sound, `transport` owns the clock, `panels` and `tracks` own their corners
 * of the DOM. This file is the one place that knows they exist together, and
 * it should stay boring — if logic starts accumulating here, it belongs in a
 * module instead.
 */

import { RenderedAudio, type AudioMode } from "./audio.js";
import { createBandEditor } from "./band_editor.js";
import { createCodecAb } from "./codec_ab.js";
import { defaultParams } from "./effects.js";
import { Panels } from "./panels.js";
import { createState, step } from "./show.js";
import { Stage } from "./stage.js";
import { Synth } from "./synth.js";
import { Transport } from "./transport.js";
import { initTracks } from "./tracks.js";
import { sceneFromTrack } from "./track_scene.js";
import { initWaveform } from "./waveform.js";
import type { GeneratedData, Scene } from "./types.js";

declare global {
  interface Window { CASTLE_GEN?: GeneratedData }
}

const GEN: GeneratedData = window.CASTLE_GEN ?? { scenes: [], audio: {} };
const SCENES: Scene[] = GEN.scenes;

if (SCENES.length === 0) {
  // Better a visible complaint than a black stage nobody can explain.
  console.error("No scenes in CASTLE_GEN — run `make preview` to regenerate.");
}

const P = defaultParams();
const rendered = new RenderedAudio(GEN.audio);
const synth = new Synth();
synth.params = P;

let audioMode: AudioMode = rendered.defaultMode;

const firstScene = SCENES[0];
if (!firstScene) throw new Error("cue desk needs at least one scene");

const state = createState(firstScene, performance.now());
const canvas = document.getElementById("stage") as HTMLCanvasElement | null;
if (!canvas) throw new Error("no #stage canvas in the page");
const stage = new Stage(canvas);

// Panels wires the cue-sheet row clicks itself, so seeking is a constructor
// dependency rather than a separate binding.
const panels = new Panels((ms) => transport.seekTo(ms));

const transport = new Transport({
  state, rendered, synth,
  getMode: () => audioMode,
  onSceneChange: (sc) => {
    panels.renderSheet(sc);
    panels.renderTicks(sc);
    panels.renderSceneInfo(sc);
  },
});

/* ── Chrome ── */

// Picking a scene while playing keeps playing; while stopped stays quiet.
panels.renderScenes(SCENES, (sc) => transport.loadScene(sc, { play: state.running }));

panels.bindSliders({
  depth: (v) => { P.depth = v; },
  speed: (v) => { P.speed = v; },
  hue: (v) => { P.hue = v; },
  bright: (v) => { P.bright = v; },
  stops: (v) => { P.stops = v; synth.setStops(v); },
  hall: (v) => synth.setReverb(v),
  trem: (v) => synth.setTremolo(v),
  lat: (ms) => { state.latency = ms; rendered.latency = ms; },
  vol: (v) => {
    rendered.volume = v;
    synth.setVolume(v);
    rendered.apply(SCENES);
  },
  soft: (on) => { state.soft = on; P.soft = on; },
  renderedAudio: (useRendered) => {
    const wasPlaying = state.running;
    audioMode = useRendered ? "rendered" : "synth";
    if (audioMode === "rendered") synth.stopWind(0.4);
    rendered.stopAll();
    transport.loadScene(state.scene, { play: wasPlaying });
  },
});

/* ── Transport controls ── */

document.getElementById("play")?.addEventListener("click", () => transport.toggle());
document.getElementById("restart")?.addEventListener("click", () => transport.restart());
document.getElementById("stop")?.addEventListener("click", () => transport.blackout());

const scrub = document.getElementById("scrub");
if (scrub) transport.bindScrub(scrub);

/* ── Mute ──────────────────────────────────────────────────────────────
   Muted by default, always. Mute is the element's own `muted` flag rather
   than volume 0, because volume is also written by the fade-in and the
   master slider — a volume-based mute is one stray write from being undone. */
const muteBtn = document.getElementById("mute");
function syncMuteUI(): void {
  if (!muteBtn) return;
  muteBtn.setAttribute("aria-pressed", String(rendered.muted));
  muteBtn.textContent = rendered.muted ? "Muted" : "Mute";
  muteBtn.title = rendered.muted
    ? "Currently muted — click to hear audio (M)"
    : "Mute (M)";
}
function toggleMute(): void {
  rendered.muted = !rendered.muted;
  synth.setMuted(rendered.muted);
  rendered.apply(SCENES);
  syncMuteUI();
}
muteBtn?.addEventListener("click", toggleMute);
syncMuteUI();
synth.setMuted(rendered.muted);

transport.bindKeys(toggleMute, (i) => {
  const b = document.querySelector<HTMLElement>(`.scene[data-i="${i}"]`);
  b?.click();
});

/* ── Rendered vs live synth ──
   With no rendered files there is nothing to switch to, so the toggle is
   disabled rather than silently doing nothing. */
const modeEl = document.getElementById("renderedAudio") as HTMLInputElement | null;
if (modeEl) {
  if (rendered.count === 0) { modeEl.checked = false; modeEl.disabled = true; }
  audioMode = modeEl.checked ? "rendered" : "synth";
}

/* ── Tracks panel and clip editor ──────────────────────────────────────
   Auditioning a clip drives the light preview from the track's own position,
   so you see what the scene will look like before committing to it — which
   is the actual question when picking 20 seconds out of a song. */
/* While a clip is being auditioned the stage shows *that clip's* lights,
   built from its own onsets, and the scene that was loaded before is put back
   when the audition stops. Seeking inside the previously loaded scene — which
   is what this did — showed the old scene's lights against the new track's
   audio, which reads as the detection being broken. */
let previewScene: Scene | null = null;
let sceneBeforeAudition: Scene | null = null;

/* Which zone each band lights and how hard it has to hit. Created here rather
   than inside either panel because both read it: the clip editor mounts it and
   re-analyses on change, and the scene generator writes it out. */
const bands = createBandEditor(() => wave.reanalyse());

/* Hearing what each codec costs, on the clip that is actually selected. It
   needs the clip from the editor and the encoder settings from the options
   row, which is why it is assembled here and not inside either. */
let selectedTrack: string | null = null;
const codecs = createCodecAb({
  target: () => {
    const c = wave.clip();
    if (!selectedTrack || !c) return null;
    return { id: selectedTrack, start: c.start, take: c.end - c.start };
  },
  opts: () => {
    const v = (id: string): string =>
      (document.getElementById(id) as HTMLInputElement | null)?.value.trim() ?? "";
    return { bitrate: v("trkBitrate"), channels: v("trkCh"), sample_rate: v("trkRate") };
  },
  onClaim: () => { wave.stop(); tracks.stopPreview(); },
});

const wave = initWaveform({
  bands,
  codecs,
  onClipChange: (clip, data) => {
    previewScene = data ? sceneFromTrack(data, clip, bands.zones()) : null;
    // Already auditioning: adopt the new selection without stopping.
    if (sceneBeforeAudition && previewScene) transport.loadScene(previewScene);
  },
  onAudition: (playing, positionMs) => {
    if (!playing) {
      const back = sceneBeforeAudition;
      sceneBeforeAudition = null;
      if (back) transport.loadScene(back);
      return;
    }
    // The region audition and the row preview are two audio elements; only one
    // of them should ever be making noise.
    tracks.stopPreview();
    codecs.stop();
    if (!sceneBeforeAudition && previewScene) {
      sceneBeforeAudition = state.scene;
      transport.loadScene(previewScene);
    }
    // The audition element is the audio; the show engine only supplies light,
    // with its clock pulled along by the player.
    transport.syncTo(positionMs);
  },
});
const tracks = initTracks({
  scenes: SCENES,
  bands,
  onSelect: (id) => { selectedTrack = id; wave.show(id); },
  onAudioClaim: () => wave.stop(),
});

/* ── Frame loop ── */
function frame(now: number): void {
  const f = step(
    state, now, P,
    (snd) => {
      // Rendered mode plays one pre-mixed file; per-cue synthesis is only
      // meaningful when the live synth is the source.
      if (audioMode === "synth") window.setTimeout(() => synth.play(snd), state.latency);
    },
    () => transport.setPlaying(false),
  );

  stage.draw(f.zones, now / 1000, f.flash, f.flashColor);
  panels.updateMeters(f.zones);
  panels.updateTransport(f.elapsed, state.scene);
  requestAnimationFrame(frame);
}

transport.loadScene(firstScene);
requestAnimationFrame(frame);
