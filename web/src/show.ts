/**
 * The show engine — the clock, the cue list, and the light state it produces.
 *
 * This is the module that mirrors the firmware most closely. Every field on
 * `ShowState` has a counterpart in a global in castle.yaml (`zone_effect`,
 * `zone_flash`, `zone_flash_col`, `zone_flash_decay`, `zone_level`), and the
 * per-frame decay and render maths below are the same arithmetic the device's
 * `addressable_lambda` performs. Keeping them in step is what makes the
 * previewer a preview rather than an illustration.
 *
 * It deliberately owns no DOM. Rendering, audio and chrome are elsewhere; this
 * takes a scene and a timestamp and answers "what colour is every pixel".
 */

import {
  EFFECTS, effect, toScreen, pixelSeed, applyOverlay, flashGate,
  overlayIndex, paletteIndex, flashModeIndex, type EffectParams,
} from "./effects.js";
import { DEFAULT_RIG, fixture, layoutOf, type Layout } from "./rig.js";
import type { Cue, EffectName, Rgbw, Scene, StrikeColor, ZoneId } from "./types.js";

export const ZONE_IDS: readonly ZoneId[] = ["towerL", "towerR", "door"];

const WHITE: StrikeColor = [1, 1, 1, 1];
const DEFAULT_DECAY = 0.90;

type PerZone<T> = Record<ZoneId, T>;
const perZone = <T,>(make: () => T): PerZone<T> =>
  ({ towerL: make(), towerR: make(), door: make() });

/** One zone's rendered output: every pixel of whatever fixture is in that
 *  spot, plus their average for the meters and the castle wash. */
export interface ZoneOutput {
  pix: Array<[number, number, number]>;
  avg: [number, number, number];
}
export type ZoneRender = Record<ZoneId, ZoneOutput>;

/** What a frame produced, for whoever wants to draw or measure it. */
export interface Frame {
  /** Milliseconds into the scene. */
  elapsed: number;
  zones: ZoneRender;
  /** Strongest strike anywhere, and its colour — drives the sky/stone wash. */
  flash: number;
  flashColor: StrikeColor;
}

export interface ShowState {
  scene: Scene;
  /** While playing, elapsed = now - t0. While paused, `held` is frozen and
   *  t0 means nothing until we resume. */
  t0: number;
  held: number;
  running: boolean;
  fired: Set<number>;
  eff: PerZone<EffectName>;
  flash: PerZone<number>;
  flashCol: PerZone<StrikeColor>;
  flashDecay: PerZone<number>;
  /** #10 attack: a pending rise. While > 0 the flash climbs by flashRise per
   *  frame instead of decaying; at the peak it flips back to decay. 0 = the
   *  classic instant slam. Mirrors zone_flash_target/_rise in castle.yaml. */
  flashTarget: PerZone<number>;
  flashRise: PerZone<number>;
  /** Scales the base effect only; strikes are unscaled. */
  level: PerZone<number>;
  /** Modelled decode spin-up, so screen and speaker agree. */
  latency: number;
  soft: boolean;

  /** What fixture is in each spot, and therefore how many pixels the zone
   *  has and how they sit in space. Set from the rig panel; the default is
   *  the castle as built (three Jewels). Every overlay reads it, which is
   *  what lets a chase orbit a Ring 16 instead of pretending it has six
   *  pixels and a middle. */
  layout: PerZone<Layout>;

  /* Per-pixel texture — the "not every pixel the same" state. Every field
     mirrors a firmware global; see the zone lambda in castle.yaml. */
  /** Effect for the fixture's centre pixel only; null = the whole fixture
   *  runs one texture (the classic look). */
  centerEff: PerZone<EffectName | null>;
  /** Overlay index (effects.OVERLAY_NAMES) composited over the base. */
  overlay: PerZone<number>;
  /** Palette index (effects.PALETTE_NAMES) for the crossfade effects. */
  palette: PerZone<number>;
  /** Seconds added to the clock per zone — anti-phase breathing. */
  phase: PerZone<number>;
  /** Which pixels strikes hit (effects.FLASH_MODES index). */
  flashMode: PerZone<number>;
  /** Bumped per strike so `scatter` picks a fresh subset each time. */
  flashEpoch: PerZone<number>;
}

export function createState(scene: Scene, now: number): ShowState {
  return {
    scene,
    t0: now,
    held: 0,
    running: false,
    fired: new Set<number>(),
    eff: perZone<EffectName>(() => "off"),
    flash: perZone(() => 0),
    flashCol: perZone<StrikeColor>(() => WHITE),
    flashDecay: perZone(() => DEFAULT_DECAY),
    flashTarget: perZone(() => 0),
    flashRise: perZone(() => 0),
    level: perZone(() => 1),
    latency: 70,
    soft: false,
    layout: perZone(() => layoutOf(fixture(DEFAULT_RIG.zones.towerL.fixture))),
    centerEff: perZone<EffectName | null>(() => null),
    overlay: perZone(() => 0),
    palette: perZone(() => 0),
    phase: perZone(() => 0),
    flashMode: perZone(() => 0),
    flashEpoch: perZone(() => 0),
  };
}

/** Apply a scene's per-zone texture (zones: block in scenes.yaml). */
function applyZoneDetail(st: ShowState, sc: Scene): void {
  st.centerEff = perZone<EffectName | null>(() => null);
  st.overlay = perZone(() => 0);
  st.palette = perZone(() => 0);
  st.phase = perZone(() => 0);
  st.flashMode = perZone(() => 0);
  for (const [z, d] of Object.entries(sc.zones ?? {})) {
    const id = z as ZoneId;
    if (d.center) st.centerEff[id] = d.center;
    if (d.overlay) st.overlay[id] = overlayIndex(d.overlay);
    if (d.palette) st.palette[id] = paletteIndex(d.palette);
    if (d.phase) st.phase[id] = d.phase;
  }
}

/**
 * Put the lights into the state they would be in at `ms`, from the scene's
 * base. Seeking without this leaves whatever effects happened to be on stage
 * when you grabbed the scrub bar — the classic seek bug.
 */
export function rebuildLightsAt(st: ShowState, sc: Scene, ms: number): void {
  st.flash = perZone(() => 0);
  st.flashCol = perZone<StrikeColor>(() => WHITE);
  st.flashDecay = perZone(() => DEFAULT_DECAY);
  st.flashTarget = perZone(() => 0);
  st.flashRise = perZone(() => 0);
  st.level = { towerL: 1, towerR: 1, door: 1, ...sc.levels };
  st.eff = { ...perZone<EffectName>(() => "off"), ...sc.base };
  applyZoneDetail(st, sc);
  st.fired.clear();

  sc.cues.forEach((c, i) => {
    if (c.t > ms) return;

    // Apply standing state for everything up to and including `ms`, but only
    // mark cues STRICTLY before `ms` as already fired.
    //
    // The difference matters at the seam. Marking `c.t <= ms` as fired meant a
    // cue sitting exactly on the seek point was swallowed: loading a scene
    // rebuilds at 0, so any cue at t=0 was retired before the show started.
    // Three scenes have strikes there — seance, ballroom, and both of crypt's
    // opening heartbeats — so every one of them lost its downbeat on the first
    // play-through, and only got it back after the first loop cleared `fired`.
    //
    // Same bug as the analyser's dropped t=0 onset, one layer up.
    if (c.t < ms) st.fired.add(i);

    if (c.bus === "LED" && c.op === "set") {
      st.eff[c.zone] = c.eff;
      if (c.level !== undefined) st.level[c.zone] = c.level;
    }
  });
}

/** Which zones a strike lands on. No target at all means every zone. */
function strikeTargets(c: Extract<Cue, { op: "strike" }>): readonly ZoneId[] {
  if (c.targets && c.targets.length) return c.targets;
  if (c.zone) return [c.zone];
  return ZONE_IDS;
}

/**
 * Fire any cues due at `elapsed`. Audio cues are handed to `onAudio` rather
 * than played here, so this module stays free of the audio graph.
 */
export function fireCues(
  st: ShowState, elapsed: number, onAudio: (snd: string) => void,
): void {
  const cues = st.scene.cues;
  for (let i = 0; i < cues.length; i++) {
    const c = cues[i];
    if (!c || elapsed < c.t || st.fired.has(i)) continue;
    st.fired.add(i);

    if (c.bus === "AUD") {
      onAudio(c.snd);
    } else if (c.op === "set") {
      st.eff[c.zone] = c.eff;
      if (c.level !== undefined) st.level[c.zone] = c.level;
    } else {
      // Beat pulses carry a small `intensity`; full lightning omits it and
      // lands at 1.0. Each strike brings its own colour and decay, which is
      // what lets a heartbeat be a fast red snap and a bell toll a slow
      // violet bloom in the same vocabulary.
      const amt = (st.soft ? 0.42 : 1) * (c.intensity ?? 1);
      for (const id of strikeTargets(c)) {
        const peak = Math.min(1, st.flash[id] + amt);
        if (c.attack && c.attack > 0) {
          // #10: swell to the peak over attack ms rather than popping.
          // The rise is per 16 ms frame, same arithmetic as the firmware.
          st.flashTarget[id] = peak;
          st.flashRise[id] = peak * 16 / c.attack;
        } else {
          st.flash[id] = peak;
          st.flashTarget[id] = 0;    // a slam cancels any pending swell
        }
        st.flashCol[id] = c.color ?? WHITE;
        st.flashDecay[id] = c.decay ?? DEFAULT_DECAY;
        // Where the strike lands on the jewel: whole face, a fresh random
        // scatter, just the centre, or just the ring. The epoch bump is what
        // makes each scattered strike pick different pixels.
        st.flashMode[id] = flashModeIndex(c.pixels ?? "all");
        st.flashEpoch[id] = (st.flashEpoch[id] + 1) % 1000;
      }
    }
  }
}

/** Per-zone strike rise-then-decay. Soft mode slows the fall, as the
 *  firmware does; the rise is not softened — soft already shrank the peak. */
export function decayFlashes(st: ShowState): void {
  for (const id of ZONE_IDS) {
    if (st.flashTarget[id] > 0) {
      st.flash[id] += st.flashRise[id];
      if (st.flash[id] >= st.flashTarget[id]) {
        st.flash[id] = st.flashTarget[id];
        st.flashTarget[id] = 0;       // peak reached: decay takes over
      }
      continue;
    }
    const d0 = st.flashDecay[id];
    st.flash[id] *= st.soft ? 1 - (1 - d0) * 0.35 : d0;
    if (st.flash[id] < 0.004) st.flash[id] = 0;
  }
}

/** Render every pixel for this instant. `ts` is seconds, free-running. */
export function renderZones(st: ShowState, ts: number, P: EffectParams): ZoneRender {
  const out = {} as ZoneRender;

  ZONE_IDS.forEach((id, zi) => {
    const ringFn = effect(st.eff[id]) ?? EFFECTS.off;
    // The centre pixel may play a different role than the rest — ember core
    // inside a candle ring, eyes in a dark window. null means one texture
    // across the whole fixture, and a fixture with no middle (a bare ring)
    // never matches, so the base effect covers all of it.
    const centerFn = st.centerEff[id] ? effect(st.centerEff[id]!) : ringFn;
    const fBase = st.flash[id] * (st.soft ? 0.55 : 0.92);
    const fc = st.flashCol[id];
    const lv = st.level[id];
    const tz = ts + st.phase[id];        // anti-phase breathing between zones
    const pal = st.palette[id];
    const ov = st.overlay[id];
    const pix: Array<[number, number, number]> = [];
    const avg: [number, number, number] = [0, 0, 0];
    const L = st.layout[id];

    const savedPal = P.pal;
    P.pal = pal;
    for (let p = 0; p < L.n; p++) {
      const fn = p === L.center ? centerFn : ringFn;
      let c = fn(tz, pixelSeed(zi, p), P);
      c = applyOverlay(ov, c, tz, p, zi, L);
      const f = fBase * flashGate(st.flashMode[id], p, zi, st.flashEpoch[id], L);
      const lit: Rgbw = [c[0] * lv, c[1] * lv, c[2] * lv, c[3] * lv + f * fc[3]];
      const [sr, sg, sb] = toScreen(lit);
      const r = Math.min(1, sr * P.bright + f * fc[0]);
      const g = Math.min(1, sg * P.bright + f * fc[1]);
      const b = Math.min(1, sb * P.bright + f * fc[2] * 0.96);
      pix.push([r, g, b]);
      // Divided by the fixture's own count, so a zone holding a 32-pixel
      // FeatherWing and one holding a 7-pixel Jewel still report comparable
      // levels to the meters and the castle wash.
      avg[0] += r / L.n;
      avg[1] += g / L.n;
      avg[2] += b / L.n;
    }
    P.pal = savedPal;
    out[id] = { pix, avg };
  });

  return out;
}

/** The strongest strike anywhere, with its colour — for the sky/stone wash. */
export function dominantFlash(st: ShowState): { flash: number; color: StrikeColor } {
  let flash = 0;
  let color: StrikeColor = WHITE;
  for (const id of ZONE_IDS) {
    if (st.flash[id] > flash) {
      flash = st.flash[id];
      color = st.flashCol[id];
    }
  }
  return { flash, color };
}

/**
 * Advance the clock one frame and produce everything a frame needs.
 * `onAudio` fires for audio cues; `onEnded` fires when a non-looping scene
 * runs out, so the transport can drop out of play without this module
 * knowing what a transport is.
 */
export function step(
  st: ShowState, nowMs: number, P: EffectParams,
  onAudio: (snd: string) => void, onEnded: () => void,
): Frame {
  const sc = st.scene;
  let el = st.running ? nowMs - st.t0 : st.held;

  if (el > sc.dur) {
    if (sc.loop) {
      el %= sc.dur;
      st.t0 = nowMs - el;
      st.fired.clear();
    } else {
      el = sc.dur;
      st.held = el;
      onEnded();
    }
  }

  if (st.running) fireCues(st, el, onAudio);
  decayFlashes(st);

  const { flash, color } = dominantFlash(st);
  return {
    elapsed: el,
    zones: renderZones(st, nowMs / 1000, P),
    flash,
    flashColor: color,
  };
}
