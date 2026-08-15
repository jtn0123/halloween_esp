/**
 * Cross-language dynamics fuzz: random onset streams through all THREE
 * copies of the pulse arithmetic.
 *
 *     node test/fuzz_parity.mjs            (from web/; dist must be built)
 *     FUZZ_SEED=123 FUZZ_CASES=500 node test/fuzz_parity.mjs
 *
 * The hand-written parity tests pin known values; this throws seeded random
 * streams — boundary velocities, median ties, decisive-pan edges, gate
 * timelines, flavour combinations — at bandStrikes (TS), then ships the
 * identical cases to tools/fuzz_check.py, which runs the real
 * gen_esphome.pulse_cues and gen_previewer.to_previewer. All three answers
 * must agree to the digit.
 *
 * Deterministic by default (fixed seed) so a red run is reproducible; the
 * env knobs exist for going hunting.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import {
  bandStrikes, styleFor, setFlavor, resetFlavors,
} from "../dist/track_lights.mjs";

const SEED = Number(process.env.FUZZ_SEED ?? 0xc0ffee);
const N = Number(process.env.FUZZ_CASES ?? 250);
const BANDS = ["onset_low", "onset_mid", "onset_high"];
const NOTES = ["hush", "verse", "chorus", "silence"];

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
const rnd = mulberry32(SEED);
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
const r3 = (x) => Math.round(x * 1000) / 1000;

/* Velocities that sit ON the shared thresholds get extra visits: the
 * strict-vs-inclusive bugs live there. */
const EDGE_VELS = [0.4, 0.55, 0.72, 0.85, 0.25, 1.0];
// Both sides of the decisive threshold (0.10), plus the old 0.25 edges so a
// regression back to the original constant would still be caught.
const EDGE_PANS = [0.1, -0.1, 0.09, -0.09, 0.25, -0.25, 1.0, -1.0, 0.0];

function genHits() {
  const n = Math.floor(rnd() * 25); // 0..24, straddles the 8-hit tempo gate
  const mode = rnd();
  let t = Math.floor(rnd() * 2000);
  const hits = [];
  for (let i = 0; i < n; i++) {
    const vel = rnd() < 0.25 ? pick(EDGE_VELS) : r3(rnd());
    const hit = [t, vel];
    if (rnd() < 0.5) hit.push(rnd() < 0.3 ? pick(EDGE_PANS) : r3(rnd() * 2 - 1));
    hits.push(hit);
    // Uniform gaps force exact median ties; mixed gaps scatter them.
    t += mode < 0.3 ? 250
      : mode < 0.6 ? 80 + Math.floor(rnd() * 250)
      : 400 + Math.floor(rnd() * 1200);
  }
  return hits;
}

function genGates(durMs) {
  const n = Math.floor(rnd() * 5); // 0..4 section boundaries
  const ts = [...new Set(Array.from({ length: n },
    () => Math.floor(rnd() * durMs)))].sort((a, b) => a - b);
  return ts.map((t) => ({
    t, op: "set", zone: "towerL", effect: "candle", level: 1,
    note: pick(NOTES),
  }));
}

/* A pin only counts when it moves the band OFF its home zone — the same
 * rule bandStrikes and sceneYaml apply (zoneOverride !== band.zone). */
const HOME_ZONE = { onset_low: "door", onset_mid: "towerL", onset_high: "towerR" };

/** The pulse block sceneYaml would write for this band — same field map. */
function cfgFor(band, s, pinnedZone, flav) {
  const pinned = pinnedZone !== undefined && pinnedZone !== HOME_ZONE[band];
  return {
    synth: band,
    zones: pinned ? [pinnedZone] : [...s.zones],
    ...(s.alternate && !pinned ? { alternate: true } : {}),
    intensity: s.intensity, decay: s.decay, ms: s.ms,
    ...(s.attackMs ? { attack_ms: s.attackMs } : {}),
    ...(flav.drift ? { drift: true } : {}),
    ...(flav.takeover ? { takeover: true } : {}),
    colors: s.colors.map((c) => [...c]),
    color_hot: [...s.colorHot],
    ...(s.pixelsByVel ? { pixels_by_vel: true } : {}),
    ...(s.pixels ? { pixels: s.pixels } : {}),
    ...(s.boostAt !== undefined
      ? { boost_at: s.boostAt, boost_targets: [...(s.boostTargets ?? [])] }
      : {}),
  };
}

/* ── generate ── */
const cases = [];
const expected = [];
for (let i = 0; i < N; i++) {
  const band = pick(BANDS);
  const s = styleFor(band);
  const hits = genHits();
  const durMs = (hits.length ? hits[hits.length - 1][0] : 0) + 1000;
  const gateCues = genGates(durMs);
  const flav = { drift: rnd() < 0.35, takeover: rnd() < 0.35 };
  // Occasionally pin the band to one zone, like the band editor would.
  const pinnedZone = rnd() < 0.15 ? pick(["door", "towerL", "towerR"]) : undefined;
  cases.push({
    dur_ms: durMs,
    cfg: cfgFor(band, s, pinnedZone, flav),
    hits,
    gate_cues: gateCues,
  });
  setFlavor("drift", flav.drift);
  setFlavor("takeover", flav.takeover);
  const gates = gateCues.map((c) => [c.t, c.note]);
  const hitsSec = hits.map(([t, v, p]) =>
    p === undefined ? [t / 1000, v] : [t / 1000, v, p]);
  expected.push(bandStrikes(band, hitsSec, 0, durMs / 1000, pinnedZone, gates));
  resetFlavors();
}

/* ── the Python side, on the same cases ── */
const py = ["../.venv/bin/python", "python3"].find((p) =>
  p === "python3" || existsSync(p));
const proc = spawnSync(py, ["../tools/fuzz_check.py"], {
  input: JSON.stringify({ cases }), encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
});
if (proc.status !== 0) {
  console.error(proc.stderr);
  console.error(`FAIL — fuzz_check.py exited ${proc.status}`);
  process.exit(1);
}
const { results } = JSON.parse(proc.stdout);

/* ── compare all three ── */
const normTs = (c) => ({
  t: c.t,
  targets: [...c.targets],
  ms: c.ms,
  // TS keeps full float colours for the audition; the generators write
  // 3-decimal YAML. Rounding here must land on the same digits.
  intensity: c.intensity,
  color: c.color.map(r3),
  decay: c.decay,
  attack: c.attack ?? 0,
  pixels: c.pixels ?? "all",
});

let pass = 0;
const fails = [];
const ties = [];
const close = (a, b) => Math.abs(a - b) <= 0.0011;

function diffCue(where, i, j, a, b) {
  for (const k of ["t", "ms", "decay", "attack", "pixels"]) {
    if (a[k] !== b[k]) return `${k}: ${a[k]} vs ${b[k]}`;
  }
  if (JSON.stringify(a.targets) !== JSON.stringify(b.targets)) {
    return `targets: ${JSON.stringify(a.targets)} vs ${JSON.stringify(b.targets)}`;
  }
  if (a.intensity !== b.intensity) {
    const kind = close(a.intensity, b.intensity) ? "rounding-tie " : "";
    return `${kind}intensity: ${a.intensity} vs ${b.intensity}`;
  }
  for (let c = 0; c < 4; c++) {
    if (a.color[c] !== b.color[c]) {
      const kind = close(a.color[c], b.color[c]) ? "rounding-tie " : "";
      return `${kind}color[${c}]: ${a.color[c]} vs ${b.color[c]}`;
    }
  }
  return null;
}

for (let i = 0; i < N; i++) {
  const ts = expected[i].map(normTs);
  for (const [name, got] of [["esphome", results[i].esphome],
                             ["previewer", results[i].previewer]]) {
    if (got.length !== ts.length) {
      fails.push(`case ${i} ${name}: ${got.length} strikes, TS has ${ts.length}`);
      continue;
    }
    let bad = false;
    for (let j = 0; j < ts.length; j++) {
      const d = diffCue(name, i, j, ts[j], got[j]);
      if (d) {
        const line = `case ${i} ${name} cue ${j} (t=${ts[j].t}): ${d}`;
        (d.startsWith("rounding-tie") ? ties : fails).push(line);
        bad = true;
      }
    }
    if (!bad) pass++;
  }
}

const cues = expected.reduce((a, e) => a + e.length, 0);
console.log(`dynamics fuzz: seed ${SEED}, ${N} cases, ${cues} strikes, `
  + `${pass}/${N * 2} generator comparisons clean`);
if (ties.length) {
  console.log(`rounding ties (banker's vs half-up — real, sub-visible):`);
  for (const t of ties.slice(0, 10)) console.log(`  ${t}`);
}
if (fails.length) {
  for (const f of fails.slice(0, 20)) console.error(`  ${f}`);
  console.error(`FAIL — ${fails.length} divergences`);
  process.exit(1);
}
if (ties.length) {
  console.error(`FAIL — ${ties.length} rounding ties (align the rounding)`);
  process.exit(1);
}
console.log("PASS");
