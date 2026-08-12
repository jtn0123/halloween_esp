/**
 * Shared types for the cue desk.
 *
 * These describe the data `tools/gen_previewer.py` splices into the page, so
 * they are the contract between the Python generators and the browser. When
 * scenes.yaml grows a field, it gets added here first — a mismatch then shows
 * up at build time instead of as a scene that silently does nothing.
 */

/** The three lit apertures on the castle. Matches `zones:` in scenes.yaml. */
export type ZoneId = "towerL" | "towerR" | "door";

/**
 * Effect names, mirroring the `Effect` enum in firmware/castle_effects.h and
 * the EFFECT_IDS table in tools/gen_esphome.py. All three must agree; this is
 * the copy a compiler can check.
 */
export type EffectName =
  | "off" | "candle" | "ember" | "furnace" | "spirit" | "eyes"
  | "seance" | "wisp" | "mansion" | "chill" | "throb" | "strobe" | "blood";

/** Linear RGBW, 0..1 per channel. The W is a real warm-white LED, not a mix. */
export type Rgbw = readonly [r: number, g: number, b: number, w: number];

/** A strike's colour multiplier, same shape as Rgbw. */
export type StrikeColor = Rgbw;

interface CueBase {
  /** Milliseconds from the start of the scene. */
  t: number;
  detail?: string;
}

/** Play a sound. Only fires in live-synth mode; rendered mode plays one file. */
export interface AudioCue extends CueBase {
  bus: "AUD";
  op: "play" | "play_loop";
  snd: string;
}

/** Switch a zone's standing effect, optionally at a reduced brightness. */
export interface SetCue extends CueBase {
  bus: "LED";
  op: "set";
  zone: ZoneId;
  eff: EffectName;
  /** Scales the base effect only; strikes are unscaled. See zone_level. */
  level?: number;
}

/** A flash on top of the standing effect. */
export interface StrikeCue extends CueBase {
  bus: "LED";
  op: "strike";
  /** Intended visual length, documentation only — decay does the work. */
  ms: number;
  /** Absent means every zone. */
  targets?: ZoneId[];
  zone?: ZoneId;
  /** 1.0 for lightning; beat pulses come through much smaller. */
  intensity?: number;
  color?: StrikeColor;
  /** Per-frame multiplier at 16 ms. 0.82 snaps, 0.97 blooms. */
  decay?: number;
}

export type Cue = AudioCue | SetCue | StrikeCue;

export const isLed = (c: Cue): c is SetCue | StrikeCue => c.bus === "LED";
export const isAudio = (c: Cue): c is AudioCue => c.bus === "AUD";

/** One scene, as generated from scenes.yaml. */
export interface Scene {
  id: string;
  name: string;
  /** e.g. "ambient · loops" */
  kind: string;
  /** Total length in milliseconds. */
  dur: number;
  loop: boolean;
  /** Playback level for this scene, 0..1. */
  volume: number;
  blurb: string;
  base: Record<ZoneId, EffectName>;
  /** Per-zone base brightness; absent entries are 1.0. */
  levels: Partial<Record<ZoneId, number>>;
  cues: Cue[];
  /** The rendered file name, e.g. "08_crypt.mp3". */
  file: string;
  /** Size of that file on disk. 0 when it has not been rendered yet. */
  bytes: number;
  /** Verbatim slice of scenes.yaml, for the source panel. */
  yaml: string;
}

/** The block spliced in between the @GEN-DATA markers. */
export interface GeneratedData {
  scenes: Scene[];
  /** Scene id -> `data:audio/mpeg;base64,…` */
  audio: Record<string, string>;
}
