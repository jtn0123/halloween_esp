/* Offline, silent whole-song simulation using the webpage's actual renderer.
 * Usage: node demo/castle-radio/simulate_show.mjs path/to/song.show.json [...]
 * No server, audio element, network, or device is involved.
 */
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const context = vm.createContext({console});
vm.runInContext(fs.readFileSync(new URL('./visuals.js', import.meta.url), 'utf8'), context);
vm.runInContext(fs.readFileSync(new URL('./cue-playback.js', import.meta.url), 'utf8'), context);
const V = context.CastleVisuals;
if (process.argv.length < 3) {
  console.error('usage: simulate_show.mjs <song.show.json> [...]');
  process.exit(2);
}
for (const path of process.argv.slice(2)) {
  const scene = JSON.parse(fs.readFileSync(path, 'utf8'));
  const state = V.createState(scene, 0);
  V.rebuildLightsAt(state, scene, 0);
  const params = V.defaultParams();
  const patterns = new Set();
  let frames = 0;
  for (let time = 0; time <= scene.dur; time += 16) {
    context.CastleCuePlayback.fire(state, time);
    V.decayFlashes(state);
    const output = V.renderZones(state, time / 1000, params);
    for (const zone of Object.values(output)) {
      for (const pixel of zone.pix) {
        assert(pixel.every(value => Number.isFinite(value) && value >= 0 && value <= 1),
          `Invalid pixel at ${time} ms in ${path}`);
      }
    }
    if (time % 160 === 0) {
      patterns.add(JSON.stringify(Object.values(output).map(zone =>
        zone.pix.map(pixel => pixel.map(value => Math.round(value * 255))))));
    }
    frames++;
  }
  assert.equal(state.fired.size, scene.cues.length, 'Every cue must execute');
  console.log(JSON.stringify({song:scene.name, frames, cues:state.fired.size,
    sampledPatterns:patterns.size, finitePixels:true}));
}
