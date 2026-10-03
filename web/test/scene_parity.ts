/**
 * The desk's scene builder against its Python twin, text for text.
 *
 *     (runs bundled from web/dist — see package.json "test")
 *     FUZZ_SEED=123 FUZZ_CASES=2000 … to go hunting
 *
 * A song nobody has edited gets its light show from `sceneYaml` — in the
 * desk when it is spliced, and headless when Castle Radio imports it or
 * `make cues` renders it. The headless copy is tools/track_scene.py, so a
 * buyer's machine needs neither node nor esbuild (2026-10-03, found by
 * tests/install_smoke.py). This throws seeded envelopes and band counts at
 * both and wants the SAME STRING back: section boundaries, the pre-chorus
 * dip, held silences, every number's spelling, the title-casing of an id.
 *
 * The envelopes are built to cross every edge the section logic has: flat
 * songs (one held look), stepped ones with long and short sections, values
 * sitting on the tier clamps and the silence level, real silences and
 * breaths too short to count, sparse points the nearest-neighbour lookup
 * has to bridge, and songs shorter than one smoothing window.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resetFlavors, resetStyleTweaks, setStyleVariant } from "../src/track_lights.js";
import { sceneYaml } from "../src/track_scene.js";

const SEED = Number(process.env.FUZZ_SEED ?? 0x5ce7e);
const N = Number(process.env.FUZZ_CASES ?? 300);

/* Deterministic PRNG — Math.random is banned from parity work. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(SEED);
const pick = <X,>(arr: readonly X[]): X => arr[Math.floor(rnd() * arr.length)]!;
const r3 = (x: number): number => Math.round(x * 1000) / 1000;

type Env = [number, number][];
interface Case {
  id: string; dur: number; counts: Record<string, number>; ext: string;
  env?: Env;
}

/* Ids as Castle Radio and the importer make them, plus the shapes the
 * title-casing regex treats specially: digits, a leading underscore, and
 * letters outside ASCII (a JS regex without /u does not call them words). */
const IDS = ["radio_a1b2c3", "fuzz_song", "the_ballad_of_x", "a", "x2_y_3z",
             "_lead", "radio_üñï_çödé", "song_ß_end"];
/* Levels a section sits at: silence, the silence edge, the tier clamps
 * (0.25/0.55, then +0.15 and 0.80) and the hysteresis width around them. */
const LEVELS = [0, 0.02, 0.04, 0.039, 0.1, 0.25, 0.33, 0.4, 0.47, 0.55, 0.6,
                0.63, 0.7, 0.72, 0.8, 0.88, 0.95, 1];

function genEnv(dur: number): Env | undefined {
  const shape = rnd();
  if (shape < 0.06) return undefined;
  if (shape < 0.1) return [];
  const step = pick([0.05, 0.1, 0.23, 0.25, 0.5, 0.9]);
  const env: Env = [];
  if (shape < 0.25) {                       // flat: barely moves
    const base = pick(LEVELS);
    for (let t = 0; t < dur; t += step) env.push([r3(t), r3(base + rnd() * 0.1)]);
    return env;
  }
  // Stepped: sections of random length and level, some long, some short.
  let t = rnd() < 0.3 ? r3(rnd() * 2) : 0;  // sometimes nothing at the start
  while (t < dur) {
    const level = pick(LEVELS);
    const len = pick([0.4, 0.9, 1.0, 1.75, 2.0, 2.25, 4, 8, 20]) * (0.5 + rnd());
    const jitter = rnd() < 0.5 ? 0 : 0.06;
    // The analyzer's own points carry every digit a double has; rounded
    // ones land exactly ON the edges. Both, section by section.
    const keep = rnd() < 0.3 ? (x: number): number => x : r3;
    for (const end = Math.min(dur, t + len); t < end; t += step) {
      const v = keep(Math.max(0, level + (rnd() - 0.5) * jitter));
      // The analyzer omits near-silence; sometimes so does this.
      if (v < 0.01 && rnd() < 0.7) continue;
      env.push([keep(t), v]);
    }
  }
  return env;
}

function genCounts(): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const band of ["onset_low", "onset_mid", "onset_high"]) {
    const r = rnd();
    if (r < 0.15) continue;                 // a band the song does not have
    counts[band] = r < 0.25 ? 0 : 1 + Math.floor(rnd() * 900);
  }
  if (rnd() < 0.2) counts.level_low = 3;    // not a band: never a stream
  return counts;
}

const cases: Case[] = [];
for (let i = 0; i < N; i++) {
  const dur = pick([0.3, 1.2, 2.6, 7.9, 10, 31.337, 95.5, 187.4321, 412.05])
    * (0.8 + rnd() * 0.4);
  const c: Case = { id: pick(IDS), dur: r3(dur) || 0.001, counts: genCounts(),
                    ext: pick(["mp3", "wav", "m4a"]) };
  const env = genEnv(c.dur);
  if (env !== undefined) c.env = env;
  cases.push(c);
}

// The headless desk: no knob turned, every flavour off, variant A — what
// scene_cli.ts and Castle Radio's import have always asked for.
resetFlavors();
resetStyleTweaks();
setStyleVariant("current");
const expected = cases.map((c) =>
  sceneYaml(c.id, c.dur, c.counts, c.ext, undefined, c.env));

/* ── the Python twin, on the same cases ── */
const py = ["../.venv/bin/python", "python3"].find((p) =>
  p === "python3" || existsSync(p))!;
const proc = spawnSync(py, ["../tools/track_scene.py", "--check"], {
  input: JSON.stringify({ cases }), encoding: "utf8",
  maxBuffer: 256 * 1024 * 1024,
});
if (proc.status !== 0) {
  console.error(proc.stderr);
  console.error(`FAIL — track_scene.py exited ${proc.status}`);
  process.exit(1);
}
const got = (JSON.parse(proc.stdout) as { yaml: string[] }).yaml;

const fails: string[] = [];
let sections = 0, predims = 0, silences = 0;
for (let i = 0; i < cases.length; i++) {
  const want = expected[i]!, have = got[i];
  sections += (want.match(/op: set/g) ?? []).length;
  predims += (want.match(/note: predim/g) ?? []).length;
  silences += (want.match(/note: silence/g) ?? []).length;
  if (have === want) continue;
  const a = want.split("\n"), b = (have ?? "").split("\n");
  const k = a.findIndex((line, j) => line !== b[j]);
  fails.push(`case ${i} (${cases[i]!.id}, ${cases[i]!.dur} s), line ${k}:\n`
    + `    desk:   ${a[k]}\n    python: ${b[k]}`);
}

console.log(`scene parity: seed ${SEED}, ${N} cases, ${sections} section cues `
  + `(${predims} pre-chorus dips, ${silences} silences)`);
// A fuzz that never reached the interesting branches proves nothing.
if (!predims || !silences) {
  console.error("FAIL — the corpus never produced a pre-chorus dip or a silence");
  process.exit(1);
}
if (fails.length) {
  for (const f of fails.slice(0, 10)) console.error(`  ${f}`);
  console.error(`FAIL — ${fails.length}/${N} scenes differ`);
  process.exit(1);
}
console.log("PASS");
