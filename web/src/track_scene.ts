/**
 * A throwaway scene built from a track's detected onsets.
 *
 * Auditioning a clip used to seek inside whatever scene happened to be loaded,
 * so the stage showed the *old* scene's lights against the *new* track's audio.
 * That is worse than showing nothing: it looks like the detection is wrong.
 *
 * This builds the scene the import would actually generate, for just the
 * selected region, so the stage above shows what you are about to commit to.
 *
 * The cue shape here mirrors the `pulse:` expansion in tools/gen_previewer.py
 * exactly — same ms, same `intensity * velocity`, same `targets` — because a
 * preview that disagrees with the generated scene is the one bug this project
 * cannot afford. If that expansion changes, this changes with it.
 */

import { BANDS, type BandName } from "./bands.js";
import type { BandEditor } from "./band_editor.js";
import type { WaveClip, WaveData } from "./waveform_view.js";
import type { Cue, EffectName, Scene, ZoneId } from "./types.js";

/** Matches the `base:`/`levels:` that sceneYaml writes for an import. */
const BASE: Record<ZoneId, EffectName> =
  { towerL: "chill", towerR: "chill", door: "ember" };
const LEVELS: Partial<Record<ZoneId, number>> =
  { towerL: 0.4, towerR: 0.4, door: 0.5 };

/** The `intensity:` a generated pulse stream carries, before velocity. */
const PULSE_INTENSITY = 0.55;

/** Playback level for a generated scene. */
const VOLUME = 0.7;

/** Zone per band, when the caller is not overriding the defaults. */
export type ZoneMap = Partial<Record<BandName, ZoneId>>;

/**
 * Build the preview scene for `clip` of `data`.
 *
 * Times come out relative to the clip's start, because that is what the
 * audition loops over — an onset at 1:30 of the track is at 0.0s of a clip
 * that starts at 1:30.
 */
export function sceneFromTrack(data: WaveData, clip: WaveClip | null,
                               zones: ZoneMap = {}): Scene {
  const start = clip?.start ?? 0;
  const end = clip?.end ?? data.duration;
  const dur = Math.max(1, Math.round((end - start) * 1000));

  const cues: Cue[] = [];
  for (const b of BANDS) {
    const hits = data.onsets[b.name];
    if (!hits) continue;
    const zone = zones[b.name] ?? b.zone;
    for (const [sec, vel] of hits) {
      if (sec < start || sec > end) continue;
      cues.push({
        t: Math.round((sec - start) * 1000),
        bus: "LED", op: "strike", ms: 120,
        intensity: Math.round(PULSE_INTENSITY * vel * 1000) / 1000,
        color: b.rgbw,
        decay: b.decay,
        targets: [zone],
        detail: b.name,
      });
    }
  }
  cues.sort((a, z) => a.t - z.t);

  return {
    id: `preview:${data.id}`,
    name: data.id,
    kind: "audition",
    dur,
    loop: true,
    volume: VOLUME,
    blurb: "Live preview of the clip you have selected. The lights are the "
         + "onsets detected in this region — the same ones the scene would use.",
    base: BASE,
    levels: LEVELS,
    cues,
    // No rendered file: the audition element is the audio, and giving this a
    // file name would have the show engine try to play a second copy.
    file: "",
    bytes: 0,
    yaml: "",
  };
}

/**
 * The same scene, as the YAML that goes into scenes.yaml.
 *
 * Deliberately in this file rather than in tracks.ts. This and
 * `sceneFromTrack` above are two renderings of one decision — which band
 * lights which zone, in what colour, decaying how fast — and if they drift
 * apart the audition stops predicting the show. Side by side, a divergence is
 * visible; a file apart, it is not.
 *
 * @param bands the editor's per-band choices, when there are any.
 */
export function sceneYaml(id: string, dur: number | undefined,
                          counts: Record<string, number>, ext = "mp3",
                          bands?: BandEditor): string {
  const L = [
    `  - id: ${id}`,
    `    name: ${id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}`,
    `    kind: custom`, `    volume: ${VOLUME}`,
    `    duration_ms: ${Math.round((dur ?? NaN) * 1000)}`, `    loop: true`,
    `    blurb: >`,
    `      Imported track. Light cues are onset-detected from the audio`,
    `      itself, so they follow whatever the track actually does.`,
    `    audio_file: tracks/${id}.${ext}`,
    // Only written when it differs from the defaults. A scene carrying the
    // default spelled out reads as a decision that was made, and this file
    // is meant to be read.
    ...(bands?.customised()
      ? [`    sensitivity: {${BANDS.map(b =>
           `${b.label}: ${bands.settings().sensitivity[b.name].toFixed(2)}`).join(", ")}}`]
      : []),
    `    base: {towerL: ${BASE.towerL}, towerR: ${BASE.towerR}, door: ${BASE.door}}`,
    `    levels: {towerL: ${LEVELS.towerL}, towerR: ${LEVELS.towerR}, door: ${LEVELS.door}}`,
    `    pulse:`,
  ];
  for (const b of BANDS) {
    const n = counts[b.name];
    if (!n) continue;
    const zone = bands?.zones()[b.name] ?? b.zone;
    L.push(`      - {synth: ${b.name}, zone: ${zone}, intensity: ${PULSE_INTENSITY}, `
         + `decay: ${b.decay}, color: [${b.rgbw.join(", ")}]}`
         + `   # ${n} onsets in ${b.lo}-${b.hi}Hz`);
  }
  L.push(`    cues: []`);
  return L.join("\n");
}
