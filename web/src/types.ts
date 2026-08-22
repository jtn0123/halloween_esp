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
  /** Which pixels the flash hits: "all" (default) | "scatter" | "center" |
   *  "ring". Scatter picks a fresh random subset per strike. */
  pixels?: string;
  /** Per-frame multiplier at 16 ms. 0.82 snaps, 0.97 blooms. */
  decay?: number;
  /** Rise time to peak, ms. Absent/0 = the classic instant slam; ~90 lets a
   *  voice or pad swell in instead of popping. Decay starts at the peak. */
  attack?: number;
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
  /** Optional per-zone texture: pixel roles, overlays, palette, phase. */
  zones?: Partial<Record<ZoneId, ZoneDetail>>;
  cues: Cue[];
  /** The rendered file name, e.g. "08_crypt.mp3". */
  file: string;
  /** Size of that file on disk. 0 when it has not been rendered yet. */
  bytes: number;
  /** Verbatim slice of scenes.yaml, for the source panel. */
  yaml: string;
}

/**
 * Per-zone texture detail — what makes a jewel more than one lamp. All
 * optional; an absent field keeps the classic uniform-jewel look.
 */
export interface ZoneDetail {
  /** Effect for the CENTRE pixel only; the base effect keeps the ring. */
  center?: EffectName;
  /** "sparkle" | "chase" | "meteor" — composited over the base. */
  overlay?: string;
  /** "haunt" | "ember" | "moonlight" | "toxic" — poles for crossfade effects. */
  palette?: string;
  /** Seconds added to this zone's clock (anti-phase breathing). */
  phase?: number;
}

/** The block spliced in between the @GEN-DATA markers. */
export interface GeneratedData {
  scenes: Scene[];
  /** Scene id -> `data:audio/mpeg;base64,…` in the portable build, or a
   *  `/studio/scene-audio/<id>` URL when the studio serves the lean page. */
  audio: Record<string, string>;
}

/* ── Wire types the studio and castle speak ────────────────────────────
   Here rather than in the panels that display them, so api.ts (the
   transport) never imports from its own consumers. The old homes
   (tracks.ts, codec_ab.ts) re-export them. */

/** The import options row, as tools/studio.py remembers them per track. */
export interface TrackOpts {
  id?: string;
  start?: string;
  take?: string;
  sensitivity?: string;
  bitrate?: number;
  sample_rate?: number;
  channels?: number;
  format?: string;
  normalize?: boolean;
  fade_in?: number | string | null;
  fade_out?: number | string | null;
}

/** One entry from `GET /studio/tracks`. */
export interface TrackInfo {
  id: string;
  /** Container it landed in: mp3 | wav | flac | opus. */
  ext?: string;
  /** File size on disk, kilobytes. */
  kb: number;
  /** Exact size in bytes — what proves a card copy current vs stale. */
  bytes?: number;
  /** Duration in seconds; missing if ffprobe could not say. */
  dur?: number;
  /** Where it came from — a URL, or `file:<path>` (tracks/_src/<id>.<ext>
   *  for a dropped or card-pulled file, kept so Re-import can work). */
  source: string;
  /** A file: source whose file has since gone — no Re-import possible. */
  source_missing?: boolean;
  title: string;
  imported: string;
  opts: TrackOpts;
  notes: string;
  /** Band name -> how many onsets were found in it. */
  onsets?: Record<string, number>;
  error?: string;
}

/** One row of a codec A/B (`POST /studio/compare`). */
export interface CodecRow {
  codec: string;
  bytes: number;
  /** Spectral distance from the lossless reference, dB. 0 for the reference. */
  db: number;
  url: string;
}

/** One entry of the castle's card listing (`GET /api/files`). */
export interface SdFile { name: string; size: number; dir: boolean }
