/**
 * The desk's scene builder, without the desk.
 *
 * `sceneYaml` decides everything an imported song's show looks like. This is
 * the same function on a command line, for holding a REAL song's scene
 * against tools/track_scene.py — the Python twin render_cues.py and Castle
 * Radio run, so a buyer needs no node — when web/test/scene_parity.ts's
 * seeded corpus is not enough to explain a difference:
 *
 *   esbuild src/scene_cli.ts --bundle --platform=node --outfile=scene_cli.mjs
 *   node scene_cli.mjs <track-id> <waveform.json> [ext]
 *
 * `waveform.json` is what GET /studio/waveform/<id> answers (or
 * analyze_track with `"waveform": true`): duration, per-band onsets and the
 * loudness envelope. The scene block goes to stdout.
 */

import { readFileSync } from "node:fs";
import { sceneYaml } from "./track_scene.js";

interface Wave {
  duration: number;
  onsets: Record<string, unknown[]>;
  env?: [number, number][];
}

const [id, wavePath, ext = "mp3"] = process.argv.slice(2);
if (!id || !wavePath) {
  console.error("usage: scene_cli <track-id> <waveform.json> [ext]");
  process.exit(2);
}
const wave = JSON.parse(readFileSync(wavePath, "utf8")) as Wave;
const counts: Record<string, number> = {};
for (const [band, hits] of Object.entries(wave.onsets)) counts[band] = hits.length;
console.log(sceneYaml(id, wave.duration, counts, ext, undefined, wave.env));
