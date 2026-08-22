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

import { RenderedAudio } from "./audio.js";
import { createBandEditor } from "./band_editor.js";
import { initBudget } from "./budget.js";
import { lightChrome, setStatus } from "./chrome_light.js";
import { createCodecAb, type CodecAb } from "./codec_ab.js";
import { auditioning, initialMode, transition,
         type DeskEvent, type DeskMode } from "./desk_mode.js";
import { deviceBridge } from "./device.js";
import { el, input, val } from "./dom.js";
import { defaultParams } from "./effects.js";
import { PixelInsets } from "./insets.js";
import { createZoneDesigner } from "./zone_designer.js";
import { installKiosk, isKiosk, kioskCastle, KIOSK_POLL_MS } from "./kiosk.js";
import { loadRig, zoneLayout, zoneRgbw, ZONE_ORDER } from "./rig.js";
import { createRigPanel } from "./rig_panel.js";
import { Panels } from "./panels.js";
import { createState, step } from "./show.js";
import { Stage } from "./stage.js";
import { initStageView } from "./stage_view.js";
import { Synth } from "./synth.js";
import { Transport } from "./transport.js";
import { initTracks, type TracksApi } from "./tracks.js";
import { sceneFromTrack } from "./track_scene.js";
import { initWaveform, type WaveformApi } from "./waveform.js";
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

/* ── The one value that says what the desk is doing ────────────────────
   Sound source, audition/adoption phase, the clip's preview scene, the
   selected track, and the players once they exist. The rules that move it
   are desk_mode.ts's transition(), tested under node; `dispatch` applies
   one event and loads whatever scene the rule says must follow. */
interface Players { wave: WaveformApi; tracks: TracksApi; codecs: CodecAb }
let mode: DeskMode<Players> = initialMode(rendered.defaultMode);
function dispatch(e: DeskEvent<Players>): void {
  const t = transition(mode, e);
  mode = t.mode;
  if (t.load) transport.loadScene(t.load);
}

const firstScene = SCENES[0];
if (!firstScene) throw new Error("cue desk needs at least one scene");

const state = createState(firstScene, performance.now());
// The frame loop's "something changed, paint" flag — declared up here because
// applyRig() and the transport set it long before the loop exists (see the
// Frame loop section for the rule it drives).
let dirty = true;
let settle = false;
const markDirty = (): void => { dirty = true; };
const draws = { frames: 0 };
(window as unknown as { __castleDraws: typeof draws }).__castleDraws = draws;
const canvas = el<HTMLCanvasElement>("stage");
if (!canvas) throw new Error("no #stage canvas in the page");
const stage = new Stage(canvas);

/* What is physically in each window. Everything that used to assume three
   seven-pixel Jewels now asks this instead — the render loop, the pixel view
   and the channel strip. See rig.ts. */
const rig = loadRig();
function applyRig(): void {
  for (const z of ZONE_ORDER) {
    state.layout[z] = zoneLayout(rig, z);
    state.rgbw[z] = zoneRgbw(rig, z);   // an RGB strip drops W on the device; so does the screen
  }
  insets.setRig(rig);
  panels.renderChannels(rig);
  markDirty();
}

// Every real pixel as a dot, in the shape the fixture actually has.
const insets = new PixelInsets(canvas, rig);
// Live per-zone texture editing on the running preview.
const designer = createZoneDesigner(() => state);

const kiosk = isKiosk();
// Which of the two the masthead toggle is showing. CSS only; both keep
// drawing. Not in kiosk: there is no toggle on screen to undo it with, and a
// wall tablet inheriting "pixels only" from whoever last used the browser
// would show the porch a row of dots and no castle.
if (!kiosk) initStageView();
// ?kiosk=1: the stage alone, dark, with nothing on screen that could drive
// the castle. What that strips and why is kiosk.ts's business.
if (kiosk) installKiosk();

/* ── Where this page is served from ────────────────────────────────────
   The Tracks panel's studio strip names its host from the address bar, not
   a constant (it read "127.0.0.1:8765" while served on :8766 — JB1-9), and
   its Restart / Stop-server buttons are for the laptop running it: on a
   phone reached over the LAN they would stop the server under everyone —
   tracks.ts keeps them hidden there (servedLocally). Hidden, not removed:
   the first cut removed the element and initTracks then threw on its id,
   so every LAN phone read "studio not running" (judge B, JB2-1). */
{
  const host = document.querySelector<HTMLElement>(".trk-srvtxt b");
  if (host) host.textContent = location.host;
}

/* The three sound sources are built AFTER the transport that needs to stop
   them, so `mode.players` is honestly null until then. The previous spelling
   closed over the consts directly with `?.` guards — but esbuild hoists
   top-level const to var, TypeScript typed them non-nullable, and nothing
   stopped a future edit from dropping a guard. This exact class of bug once
   took out 26 tests (grade report C3); now the compiler enforces every access. */

// Panels wires the cue-sheet row clicks itself, so seeking is a constructor
// dependency rather than a separate binding.
const panels = new Panels((ms) => transport.seekTo(ms));

/* Picking a fixture re-derives the layouts the render loop reads, so the
   stage changes on the very next frame. The firmware still needs a reflash
   before the castle agrees, which the panel says out loud. */
createRigPanel(rig, { onChange: () => applyRig() });
applyRig();

/* What the show is allowed to cost, in both builds. Scene sizes are real
   whenever `make audio` has run; the imported library arrives later, once the
   studio has answered — see the onList hook below. */
const budget = initBudget(SCENES);

/* ── The masthead's one live line ──────────────────────────────────────
   Which machine is listening and what the speakers are doing. It is the only
   status on the page that is true before you have opened anything, so it has
   to be maintained rather than written once. */
let deviceLine = "simulator";
let deviceOk = true;
function syncStatus(): void {
  setStatus(`${deviceLine} · `
    + `${mode.source === "rendered" ? "rendered audio" : "live synth"} · `
    + `${rendered.muted ? "muted" : "sounding"}`, deviceOk);
}

const transport = new Transport({
  state, rendered, synth,
  getMode: () => mode.source,
  onSceneChange: (sc) => {
    markDirty();
    panels.renderSheet(sc);
    panels.renderTicks(sc);
    panels.renderSceneInfo(sc);
  },
  // syncUI runs during THIS construction (loadScene → setPlaying), before
  // the players exist — the null then is real, typed, and a no-op.
  stopExternal: () => {
    mode.players?.wave.stop();
    mode.players?.tracks.stopPreview();
    mode.players?.codecs.stop();
  },
  isExternalPlaying: () => mode.players?.tracks.previewing() ?? false,
  // ■ Stop / Esc reach the castle too (when one is mirroring): the desk
  // fired the scene on the porch, so the desk's own Stop must end it.
  onBlackout: () => { markDirty(); device.stop(); },   // a dark stage is a repaint too
});

/* ── Chrome ── */

// Picking a scene while playing keeps playing; while stopped stays quiet.
// When this page is served from the castle itself, the pick also fires the
// scene on the hardware — see device.ts for the probe that decides.
//
// The "adopting" phase guards the loop: on load the desk ADOPTS the castle's
// current scene by picking its tile, and that pick must not mirror back —
// the castle is already running it, and a re-fire would restart it audibly.
const device = deviceBridge({
  adoptScene: (id) => {
    // The kiosk FOLLOWS the castle: an empty id is the porch going idle,
    // and the wall tablet goes dark with it. The desk is only ever handed
    // a real scene, once, at first contact.
    if (!id) {
      if (kiosk) transport.blackout();
      return;
    }
    const i = SCENES.findIndex((s) => s.id === id);
    const sc = SCENES[i];
    if (!sc) return;
    if (sc !== state.scene) {
      dispatch({ type: "adopt-start" });
      panels.selectScene(i);
      dispatch({ type: "adopt-end" });
    }
    // A kiosk adopting a scene the castle is RUNNING must run it too —
    // loaded at frame 0 and paused, the tablet was never in sync (JB1-2).
    // The desk keeps its own rule: nothing plays until someone presses Play.
    if (kiosk && !state.running) transport.loadScene(sc, { play: true });
  },
  onStatus: (line, ok) => {
    deviceLine = line; deviceOk = ok; syncStatus();
    if (kiosk) kioskCastle(ok);
  },
  // The card's size, for the SD budget — a measured ceiling beats the
  // assumed one, whenever a castle is there to report it.
  onCard: (totalKb) => budget.setCard(totalKb),
  // A kiosk is a display, not a console: nothing it does may reach the
  // porch, and it asks often enough to follow what the porch is doing.
  ...(kiosk ? { mirror: false, follow: true, pollMs: KIOSK_POLL_MS } : {}),
  // The chip's SOUND switch. Pressing it is the consent the muted-by-default
  // rule wants: route Mac unmutes this browser, route castle hushes it.
  onSoundRoute: (local) => { if (rendered.muted === local) toggleMute(); },
});
panels.renderScenes(SCENES, (sc) => {
  transport.loadScene(sc, { play: state.running });
  designer.refresh();
  if (mode.phase.kind !== "adopting") device.scene(sc.id);
});

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
    dispatch({ type: "source", source: useRendered ? "rendered" : "synth" });
    if (mode.source === "rendered") synth.stopWind(0.4);
    rendered.stopAll();
    transport.loadScene(state.scene, { play: wasPlaying });
    syncStatus();
  },
});

/* ── Transport controls ── */

el("play")?.addEventListener("click", () => {
  // ♪ Mac route: pressing Play IS the consent to sound. Without this the
  // first play ran a silent light show and the operator hunted for the
  // second, unrelated-looking MUTED button (dogfood 004).
  if (localStorage.getItem("castleSoundRoute") !== "castle" && rendered.muted) toggleMute();
  transport.toggle();
});
el("restart")?.addEventListener("click", () => transport.restart());
el("stop")?.addEventListener("click", () => transport.blackout());

const scrub = el("scrub");
if (scrub) transport.bindScrub(scrub);

/* ── Mute ──────────────────────────────────────────────────────────────
   Muted by default, always. Mute is the element's own `muted` flag rather
   than volume 0, because volume is also written by the fade-in and the
   master slider — a volume-based mute is one stray write from being undone. */
const muteBtn = el("mute");
function syncMuteUI(): void {
  syncStatus();
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

// Not in kiosk: a wall tablet's keyboard (or a cat on it) must not be able
// to pick scenes — which, with mirroring, fires them on the porch.
if (!kiosk) {
  transport.bindKeys(toggleMute, (i) => panels.selectScene(i));
}

/* ── Rendered vs live synth ──
   With no rendered files there is nothing to switch to, so the toggle is
   disabled rather than silently doing nothing. */
const modeEl = input("renderedAudio");
if (modeEl) {
  if (rendered.count === 0) { modeEl.checked = false; modeEl.disabled = true; }
  dispatch({ type: "source", source: modeEl.checked ? "rendered" : "synth" });
}

/* ── Tracks panel and clip editor ──────────────────────────────────────
   Auditioning a clip drives the light preview from the track's own position,
   so you see what the scene will look like before committing to it — which
   is the actual question when picking 20 seconds out of a song. */
/* While a clip is being auditioned the stage shows *that clip's* lights,
   built from its own onsets, and the scene that was loaded before is put back
   when the audition stops (mode.phase "audition" / `before`). Seeking inside
   the previously loaded scene — which is what this did — showed the old
   scene's lights against the new track's audio, which reads as the detection
   being broken. */

/* Which zone each band lights and how hard it has to hit. Created here rather
   than inside either panel because both read it: the clip editor mounts it and
   re-analyses on change, and the scene generator writes it out. */
const bands = createBandEditor(() => mode.players?.wave.reanalyse(),
                               // Mute/solo: same analysis, different mix —
                               // rebuild the audition without re-fetching.
                               () => mode.players?.wave.resync());

/* Hearing what each codec costs, on the clip that is actually selected. It
   needs the clip from the editor and the encoder settings from the options
   row, which is why it is assembled here and not inside either. */
const codecs = createCodecAb({
  target: () => {
    const c = mode.players?.wave.clip();
    if (!mode.track || !c) return null;
    return { id: mode.track, start: c.start, take: c.end - c.start };
  },
  opts: () => ({ bitrate: val("trkBitrate"), channels: val("trkCh"),
                 sample_rate: val("trkRate") }),
  onClaim: () => { mode.players?.wave.stop(); mode.players?.tracks.stopPreview(); },
});

const wave = initWaveform({
  bands,
  codecs,
  // The clip (or its bands) changed: rebuild the preview scene. Already
  // auditioning, the rule loads it without stopping — see transition().
  onClipChange: (clip, data) => dispatch({
    type: "clip",
    preview: data ? sceneFromTrack(data, clip, bands.zones(), bands.active()) : null,
  }),
  onAudition: (playing, positionMs) => {
    // Stopping puts back the scene the audition displaced, if it did.
    if (!playing) { dispatch({ type: "audition-stop" }); return; }
    // The region audition and the row preview are two audio elements; only one
    // of them should ever be making noise.
    tracks.stopPreview();
    codecs.stop();
    dispatch({ type: "audition-start", current: state.scene });
    // The audition element is the audio; the show engine only supplies light,
    // with its clock pulled along by the player.
    transport.syncTo(positionMs);
  },
});
const tracks = initTracks({
  scenes: SCENES,
  bands,
  onSelect: (id) => { dispatch({ type: "select", track: id }); wave.show(id); },
  onAudioClaim: () => wave.stop(),
  onPreviewState: () => transport.refreshUI(),
  // The library is the SD build's biggest number, and it only exists once
  // the studio has answered — so the budget card learns it here.
  onList: (list) => budget.setTracks(list),
});
dispatch({ type: "ready", players: { wave, tracks, codecs } });

/* ── Frame loop ──────────────────────────────────────────────────────────
   A stopped desk should cost nothing (grade report G3). Once the show is
   stopped, no clip is being auditioned and the flash has decayed, nothing
   on screen changes between frames — so the paint is skipped until
   something marks the scene dirty (any slider, click or key, a rig change,
   a scene load, a scrub drag) or the show runs again. One extra frame is
   always painted after activity ends, so the meters and stage settle on the
   final state rather than the last moving one. A hidden tab paints nothing
   and repaints once when it comes back. `step` still runs every frame: it
   owns the clock and the cue sounds, which must not stall in a background
   tab. window.__castleDraws counts paints, so a test can assert the loop
   idles after Stop. */
for (const ev of ["input", "change", "click", "keydown"] as const) {
  document.addEventListener(ev, markDirty, true);
}
scrub?.addEventListener("pointermove", markDirty);
document.addEventListener("visibilitychange", markDirty);

function frame(now: number): void {
  const f = step(
    state, now, P,
    (snd) => {
      // Rendered mode plays one pre-mixed file; per-cue synthesis is only
      // meaningful when the live synth is the source.
      if (mode.source === "synth") window.setTimeout(() => synth.play(snd), state.latency);
    },
    () => transport.setPlaying(false),
  );

  const active = state.running || auditioning(mode) || f.flash > 0;
  if (!document.hidden && (active || dirty || settle)) {
    stage.draw(f.zones, now / 1000, f.flash, f.flashColor);
    insets.draw(f.zones);
    panels.updateMeters(f.zones);
    lightChrome(f.zones);
    wave.mirror(f.zones);
    panels.updateTransport(f.elapsed, state.scene);
    draws.frames++;
    settle = active || dirty;          // one more paint after the last change
    dirty = false;
  }
  requestAnimationFrame(frame);
}

transport.loadScene(firstScene);
syncStatus();
requestAnimationFrame(frame);
