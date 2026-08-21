/**
 * Show-engine simulation: every scene in scenes/scenes.yaml, start to finish.
 *
 *     node test/show_sim.mjs                       (from web/; dist built)
 *     SIM_SEED=5 SIM_FUZZ=40 node test/show_sim.mjs
 *
 * show_engine.mjs pins the engine's rules on hand-built scenes. This drives
 * the REAL scenes — as tools/gen_previewer.py hands them to the browser,
 * pulse streams expanded from audio/markers.json — through step() at
 * accelerated time and checks the invariants that hold for any show:
 *
 *   - every frame: per-zone pixel count equals the rig's, every channel a
 *     finite number in 0..1, flash in 0..1, elapsed inside the scene
 *   - cues fire in time order, each exactly once per pass, and a pass fires
 *     every cue up to and including one sitting on the scene's last ms
 *   - a looping scene wraps and re-arms; a finite scene ends exactly once
 *   - blackout (effects off, strikes cleared) zeroes every pixel
 *   - every audio file a scene names exists on disk
 *
 * Then a seeded fuzz: random rigs (any fixture in any spot, empty spots
 * included), random slider params, random frame jitter, soft mode on/off.
 * The seed is printed on failure so a red run can be replayed.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import {
  createState, rebuildLightsAt, renderZones, step, ZONE_IDS,
} from "../dist/show.mjs";
import { defaultParams } from "../dist/effects.mjs";
import { FIXTURES, fixture, layoutOf } from "../dist/rig.mjs";

const ROOT = join(import.meta.dirname, "..", "..");
const SEED = Number(process.env.SIM_SEED ?? 0xcafe);
const FUZZ = Number(process.env.SIM_FUZZ ?? 24);

/* ── the scenes, exactly as the previewer generator shapes them ── */
const PY = [join(ROOT, ".venv", "bin", "python"), "python3"].find((p) => p === "python3" || existsSync(p));
const DUMP = [
  "import sys, json, pathlib; sys.path.insert(0, 'tools')",
  "import gen_previewer as gp, yaml",
  "doc = yaml.safe_load(open('scenes/scenes.yaml'))",
  "mp = pathlib.Path('audio/markers.json')",
  "m = json.loads(mp.read_text()) if mp.exists() else {}",
  "out = []",
  "for i, s in enumerate(doc['scenes'], 1):",
  "    p = gp.to_previewer(s, i, '', m); p['audio_file'] = s.get('audio_file'); out.append(p)",
  "print(json.dumps({'scenes': out, 'zones': doc['zones']}))",
].join("\n");
const proc = spawnSync(PY, ["-c", DUMP], { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 28 });
if (proc.status !== 0) {
  console.error(proc.stderr);
  console.error("FAIL — could not dump scenes through gen_previewer.py");
  process.exit(1);
}
const { scenes, zones } = JSON.parse(proc.stdout);

let pass = 0;
const fails = [];
const ok = (cond, msg) => { if (cond) pass++; else if (fails.length < 400) fails.push(msg); };

/* Deterministic PRNG — Math.random is banned from parity work. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The rig as scenes.yaml declares it, by zone id. */
const rigLayout = {};
for (const z of zones) rigLayout[z.id] = layoutOf(fixture(z.fixture ?? "jewel7"), z.pixels);

const unit = (v) => Number.isFinite(v) && v >= 0 && v <= 1;

/**
 * Run one scene through the engine and check every invariant along the way.
 * Returns the state so the caller can add scene-specific checks.
 */
function simulate(sc, { layout, P, soft, latency, jitter, rnd, tag }) {
  const st = createState(sc, 0);
  for (const z of ZONE_IDS) st.layout[z] = layout[z];
  st.soft = soft;
  st.latency = latency;
  rebuildLightsAt(st, sc, 0);
  st.running = true;
  st.t0 = 0;

  const cueT = sc.cues.map((c) => c.t);
  ok(cueT.every((t, i) => i === 0 || t >= cueT[i - 1]), `${tag}: cue list is not time-sorted`);
  ok(cueT.every((t) => t >= 0 && t <= sc.dur), `${tag}: a cue lies outside 0..${sc.dur}`);

  let ended = 0, audio = 0, passes = 0, lastFiredT = -Infinity, prevElapsed = -1, frames = 0;
  let firedThisPass = 0;
  const seen = new Set();
  const horizon = sc.loop ? sc.dur * 2 + 400 : sc.dur + 1000;
  for (let now = 0; now <= horizon; now += jitter ? 1 + Math.floor(rnd() * 40) : 16) {
    frames++;
    const f = step(st, now, P, () => audio++, () => { ended++; st.running = false; });

    ok(Number.isFinite(f.elapsed) && f.elapsed >= 0 && f.elapsed <= sc.dur,
       `${tag}: elapsed ${f.elapsed} outside the scene at now=${now}`);
    ok(unit(f.flash), `${tag}: frame flash ${f.flash} at now=${now}`);
    ok(f.flashColor.length === 4 && f.flashColor.every(Number.isFinite),
       `${tag}: flash colour ${f.flashColor}`);
    for (const z of ZONE_IDS) {
      const out = f.zones[z];
      ok(out.pix.length === layout[z].n,
         `${tag}: zone ${z} rendered ${out.pix.length} px, rig has ${layout[z].n}`);
      ok(out.avg.every(unit), `${tag}: zone ${z} avg ${out.avg} at now=${now}`);
      for (const px of out.pix) {
        ok(px.length === 3 && px.every(unit), `${tag}: zone ${z} pixel ${px} at now=${now}`);
      }
      ok(unit(st.flash[z]), `${tag}: zone ${z} flash ${st.flash[z]} at now=${now}`);
    }

    // Cue bookkeeping: a loop wrap clears `fired`; within a pass, the newly
    // fired cues are later than the ones before and not from the future.
    if (f.elapsed < prevElapsed) {             // wrapped
      ok(sc.loop, `${tag}: the clock went backwards in a non-looping scene`);
      ok(firedThisPass === cueT.length,
         `${tag}: pass ${passes} fired ${firedThisPass} of ${cueT.length} cues`);
      passes++; firedThisPass = 0; lastFiredT = -Infinity; seen.clear();
    }
    for (const i of st.fired) {
      if (seen.has(i)) continue;
      seen.add(i);
      firedThisPass++;
      ok(cueT[i] <= f.elapsed + 1e-9, `${tag}: cue ${i} (t=${cueT[i]}) fired early at ${f.elapsed}`);
      ok(cueT[i] >= lastFiredT - 1e-9, `${tag}: cue ${i} (t=${cueT[i]}) fired after t=${lastFiredT}`);
      lastFiredT = Math.max(lastFiredT, cueT[i]);
    }
    prevElapsed = f.elapsed;
  }

  if (sc.loop) {
    ok(passes >= 1, `${tag}: looping scene never wrapped in ${horizon} ms`);
    ok(ended === 0, `${tag}: looping scene signalled an end`);
  } else {
    ok(ended === 1, `${tag}: finite scene ended ${ended} times`);
    ok(st.fired.size === cueT.length, `${tag}: ${st.fired.size}/${cueT.length} cues fired by the end`);
  }
  const audCues = sc.cues.filter((c) => c.bus === "AUD").length;
  if (!sc.loop) ok(audio === audCues, `${tag}: ${audio} audio callbacks for ${audCues} audio cues`);

  // Blackout: what the device's scene_stop does — effects off, strikes
  // cleared — must leave nothing lit on any fixture.
  for (const z of ZONE_IDS) {
    st.eff[z] = "off"; st.centerEff[z] = null; st.overlay[z] = 0;
    st.flash[z] = 0; st.flashTarget[z] = 0; st.flashRise[z] = 0;
  }
  const dark = renderZones(st, 1234.5, P);
  for (const z of ZONE_IDS) {
    ok(dark[z].pix.every((px) => px.every((v) => v === 0)), `${tag}: blackout left zone ${z} lit`);
  }
  return { st, frames };
}

/* ── every real scene, on the rig scenes.yaml declares ── */
const P0 = defaultParams();
let totalFrames = 0;
for (const sc of scenes) {
  const file = join(ROOT, "audio", sc.file);
  ok(existsSync(file), `${sc.id}: rendered audio ${sc.file} missing under audio/`);
  if (sc.audio_file) {
    ok(existsSync(join(ROOT, sc.audio_file)), `${sc.id}: audio_file ${sc.audio_file} missing`);
  }
  ok(ZONE_IDS.every((z) => z in sc.base), `${sc.id}: base is missing a zone`);
  const { frames } = simulate(sc, {
    layout: rigLayout, P: P0, soft: false, latency: 70, jitter: false, tag: sc.id,
  });
  totalFrames += frames;
}

/* ── fuzz: random rigs, params, jitter ── */
const rnd = mulberry32(SEED);
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
for (let i = 0; i < FUZZ; i++) {
  const sc = pick(scenes);
  const layout = {};
  for (const z of ZONE_IDS) {
    const fx = pick(FIXTURES);
    layout[z] = layoutOf(fx, fx.maxCount ? 1 + Math.floor(rnd() * fx.maxCount) : undefined);
  }
  const P = {
    ...defaultParams(),
    depth: rnd(), speed: 0.3 + rnd() * 2.7, bright: rnd(), hue: rnd(),
    soft: rnd() < 0.5, stops: rnd(),
  };
  // Long scenes at 1 ms jitter would take minutes; clip the horizon by
  // running a copy with a shorter duration but the same cue list prefix.
  const clipped = sc.dur > 40000
    ? { ...sc, dur: 40000, cues: sc.cues.filter((c) => c.t <= 40000) }
    : sc;
  simulate(clipped, {
    layout, P, soft: rnd() < 0.5, latency: Math.floor(rnd() * 400), jitter: true, rnd,
    tag: `fuzz#${i} ${sc.id} rig=${ZONE_IDS.map((z) => layout[z].n).join("/")} seed=${SEED}`,
  });
}

console.log(`show simulation: ${scenes.length} scenes, ${totalFrames} frames at 16 ms, `
  + `${FUZZ} fuzz runs (seed ${SEED}); ${pass} checks`);
if (fails.length) {
  console.error(`\nFAILED — ${fails.length} (seed ${SEED}):`);
  for (const m of fails.slice(0, 30)) console.error("  " + m);
  if (fails.length > 30) console.error(`  ...and ${fails.length - 30} more`);
  process.exit(1);
}
console.log("PASS");
