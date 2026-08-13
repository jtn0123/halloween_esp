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
 * Every visual decision — colour ramps, pixel masks, movement, sections —
 * lives in track_lights.ts, which BOTH sceneFromTrack and sceneYaml read.
 * The audition and the exported YAML cannot drift apart, because neither of
 * them owns the look.
 */

import { BANDS, type BandName } from "./bands.js";
import type { BandEditor } from "./band_editor.js";
import type { WaveClip, WaveData } from "./waveform_view.js";
import { TIERS, ZONES_BLOCK, getFlavors, sectionCues, styleFor,
         sustainedSwells, trackCues } from "./track_lights.js";
import type { EffectName, Scene, ZoneId } from "./types.js";
import type { Onset } from "./onsets.js";

/** Matches the `base:`/`levels:` that sceneYaml writes — the quiet tier. */
const BASE: Record<ZoneId, EffectName> =
  { towerL: TIERS[0]!.towers, towerR: TIERS[0]!.towers, door: TIERS[0]!.door };
const LEVELS: Partial<Record<ZoneId, number>> = {
  towerL: TIERS[0]!.towersLevel, towerR: TIERS[0]!.towersLevel,
  door: TIERS[0]!.doorLevel,
};

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
                               zones: ZoneMap = {},
                               active: Partial<Record<BandName, boolean>> = {},
                               ): Scene {
  const start = clip?.start ?? 0;
  const end = clip?.end ?? data.duration;
  const dur = Math.max(1, Math.round((end - start) * 1000));

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
    zones: ZONES_BLOCK,
    cues: trackCues(data.onsets as Record<string, readonly Onset[]>,
                    data.env, start, end, zones, active),
    // No rendered file: the audition element is the audio, and giving this a
    // file name would have the show engine try to play a second copy.
    file: "",
    bytes: 0,
    yaml: "",
  };
}

const num = (v: number): string => String(Math.round(v * 1000) / 1000);
const rgbw = (c: readonly number[]): string => `[${c.map(num).join(", ")}]`;

/**
 * The same scene, as the YAML that goes into scenes.yaml.
 *
 * The strikes go out as `pulse:` streams carrying the per-hit dynamics
 * (color_hot, pixels_by_vel, boost) that tools/gen_previewer.py and
 * tools/gen_esphome.py expand with the same arithmetic trackCues uses.
 * The sections go out as explicit `set` cues, because they are few and the
 * generators already understand them.
 *
 * @param bands the editor's per-band choices, when there are any.
 * @param env   full-range loudness from the waveform analysis; without it the
 *              scene still works, it just holds one look instead of breathing.
 */
export function sceneYaml(id: string, dur: number | undefined,
                          counts: Record<string, number>, ext = "mp3",
                          bands?: BandEditor,
                          env?: ReadonlyArray<readonly [number, number]>): string {
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
    `    zones:`,
    `      towerL: {center: ${ZONES_BLOCK.towerL.center}, palette: ${ZONES_BLOCK.towerL.palette}}`,
    `      towerR: {center: ${ZONES_BLOCK.towerR.center}, palette: ${ZONES_BLOCK.towerR.palette}, phase: ${ZONES_BLOCK.towerR.phase}}`,
    `      door: {overlay: ${ZONES_BLOCK.door.overlay}, palette: ${ZONES_BLOCK.door.palette}}`,
    `    pulse:`,
  ];
  for (const b of BANDS) {
    const n = counts[b.name];
    if (!n) continue;
    // Effective style: the knobs panel's live tweaks apply, the A/B variant
    // never does — see the style-lab note in track_lights.ts.
    const s = styleFor(b.name, true);
    const pinned = bands !== undefined && bands.zones()[b.name] !== b.zone;
    const zones: readonly ZoneId[] = pinned ? [bands.zones()[b.name]!] : s.zones;
    const opts = [
      `synth: ${b.name}`, `zones: [${zones.join(", ")}]`,
      ...(s.alternate && !pinned ? ["alternate: true"] : []),
      `intensity: ${num(s.intensity)}`, `decay: ${num(s.decay)}`, `ms: ${s.ms}`,
      ...(s.attackMs ? [`attack_ms: ${s.attackMs}`] : []),
      // Flavour toggles ship when they are ON — what you audition is what
      // the generators expand.
      ...(getFlavors().drift ? ["drift: true"] : []),
      ...(getFlavors().takeover ? ["takeover: true"] : []),
      `colors: [${s.colors.map(rgbw).join(", ")}]`,
      `color_hot: ${rgbw(s.colorHot)}`,
      ...(s.pixelsByVel ? ["pixels_by_vel: true"] : []),
      ...(s.pixels ? [`pixels: ${s.pixels}`] : []),
      ...(s.boostAt !== undefined
        ? [`boost_at: ${num(s.boostAt)}`,
           `boost_targets: [${(s.boostTargets ?? []).join(", ")}]`]
        : []),
    ];
    L.push(`      - {${opts.join(", ")}}   # ${n} onsets in ${b.lo}-${b.hi}Hz`);
  }
  const sects = env?.length && dur ? sectionCues(env, 0, dur) : [];
  // #4 swells ride along as explicit strikes — attack does the shaping, so
  // the generators expand them with code they already have.
  const swells = getFlavors().swells && env?.length && dur
    ? sustainedSwells(env, 0, dur) : [];
  if (!sects.length && !swells.length) {
    L.push(`    cues: []`);
  } else {
    L.push(`    cues:`);
    for (const c of sects) {
      L.push(`      - {t: ${c.t}, op: set, zone: ${c.zone}, effect: ${c.eff}, `
           + `level: ${num(c.level ?? 1)}, note: ${c.detail}}`);
    }
    for (const c of swells) {
      L.push(`      - {t: ${c.t}, op: strike, targets: [${(c.targets ?? []).join(", ")}], `
           + `ms: ${c.ms}, intensity: ${num(c.intensity ?? 1)}, `
           + `color: ${rgbw(c.color ?? [1, 1, 1, 1])}, decay: ${num(c.decay ?? 0.9)}, `
           + `attack: ${c.attack}, note: ${c.detail}}`);
    }
  }
  return L.join("\n");
}
