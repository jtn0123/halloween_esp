/**
 * The desk's scene builder, without the desk.
 *
 * `sceneYaml` decides everything an imported song's show looks like, and it
 * only ever ran behind a button in a browser: re-rendering a song meant
 * opening the page and clicking through it. This is the same function on a
 * command line, bundled for node by tools/render_cues.py — so the show a
 * song gets headless is, by construction, the show the desk would splice.
 *
 *   node scene_cli.mjs <track-id> <waveform.json> [ext]
 *
 * `waveform.json` is what GET /studio/waveform/<id> answers: duration,
 * per-band onsets and the loudness envelope. The scene block goes to stdout.
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
