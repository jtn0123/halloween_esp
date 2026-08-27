/**
 * Cross-language dynamics fuzz: random onset streams through all THREE
 * copies of the pulse arithmetic.
 *
 *     (runs bundled from web/dist — see package.json "test")
 *     FUZZ_SEED=123 FUZZ_CASES=500 … to go hunting
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
} from "../src/track_lights.js";
import { sceneYaml } from "../src/track_scene.js";
import type { BandName } from "../src/bands.js";

const SEED = Number(process.env.FUZZ_SEED ?? 0xc0ffee);
const N = Number(process.env.FUZZ_CASES ?? 250);
const BANDS: readonly BandName[] = ["onset_low", "onset_mid", "onset_high"];
const NOTES = ["hush", "verse", "chorus", "silence"];

type FHit = [number, number] | [number, number, number];

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

/* Velocities that sit ON the shared thresholds get extra visits: the
 * strict-vs-inclusive bugs live there. */
const EDGE_VELS = [0.4, 0.55, 0.72, 0.85, 0.25, 1.0];
// Both sides of the decisive threshold (0.10), plus the old 0.25 edges so a
// regression back to the original constant would still be caught.
const EDGE_PANS = [0.1, -0.1, 0.09, -0.09, 0.25, -0.25, 1.0, -1.0, 0.0];

function genHits(): FHit[] {
  const n = Math.floor(rnd() * 25); // 0..24, straddles the 8-hit tempo gate
  const mode = rnd();
  let t = Math.floor(rnd() * 2000);
  const hits: FHit[] = [];
  for (let i = 0; i < n; i++) {
    const vel = rnd() < 0.25 ? pick(EDGE_VELS) : r3(rnd());
    const hit: FHit = [t, vel];
    if (rnd() < 0.5) (hit as number[]).push(rnd() < 0.3 ? pick(EDGE_PANS) : r3(rnd() * 2 - 1));
    hits.push(hit);
    // Uniform gaps force exact median ties; mixed gaps scatter them.
    t += mode < 0.3 ? 250
      : mode < 0.6 ? 80 + Math.floor(rnd() * 250)
      : 400 + Math.floor(rnd() * 1200);
  }
  return hits;
}

interface GateCue { t: number; op: string; zone: string; effect: string; level: number; note: string }
function genGates(durMs: number): GateCue[] {
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
const HOME_ZONE: Record<string, string> = { onset_low: "door", onset_mid: "towerL", onset_high: "towerR" };

type Style = ReturnType<typeof styleFor>;

/** The pulse block sceneYaml would write for this band — same field map. */
function cfgFor(band: BandName, s: Style, pinnedZone: string | undefined,
                flav: { drift: boolean; takeover: boolean }): Record<string, unknown> {
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

type Strikes = ReturnType<typeof bandStrikes>;

/* ── generate ── */
const cases: Record<string, unknown>[] = [];
const expected: Strikes[] = [];
for (let i = 0; i < N; i++) {
  const band = pick(BANDS);
  const s = styleFor(band);
  const hits = genHits();
  const durMs = (hits.length ? hits[hits.length - 1]![0] : 0) + 1000;
  const gateCues = genGates(durMs);
  const flav = { drift: rnd() < 0.35, takeover: rnd() < 0.35 };
  // Occasionally pin the band to one zone, like the band editor would.
  const pinnedZone = rnd() < 0.15
    ? pick(["door", "towerL", "towerR"] as const) : undefined;
  cases.push({
    dur_ms: durMs,
    cfg: cfgFor(band, s, pinnedZone, flav),
    hits,
    gate_cues: gateCues,
  });
  setFlavor("drift", flav.drift);
  setFlavor("takeover", flav.takeover);
  const gates: [number, string][] = gateCues.map((c) => [c.t, c.note]);
  const hitsSec = hits.map(([t, v, p]): FHit =>
    p === undefined ? [t / 1000, v] : [t / 1000, v, p]);
  expected.push(bandStrikes(band, hitsSec, 0, durMs / 1000, pinnedZone, gates));
  resetFlavors();
}

/* ── D5: the DEFAULT styles through the REAL sceneYaml handover ──
   The random cases above pass cfg dicts built by cfgFor, a MIRROR of
   sceneYaml's field map — which proves the arithmetic but not the map. A
   TS default that sceneYaml forgot to emit (or spelt differently) would
   sail through with a green fuzz. These cases generate the actual YAML
   block the desk writes into scenes.yaml, let the Python side parse it
   for real, and pin the whole default vocabulary end to end. */
const ONLY_SETS: readonly (readonly BandName[])[] =
  [["onset_low"], ["onset_mid"], ["onset_high"], BANDS];
for (const only of ONLY_SETS) {
  const hitsBySynth: Record<string, FHit[]> = {};
  const counts: Record<string, number> = {};
  let durMs = 0;
  for (const band of only) {
    const hits = genHits();
    hitsBySynth[band] = hits;
    counts[band] = hits.length || 1;
    durMs = Math.max(durMs, (hits.length ? hits[hits.length - 1]![0] : 0) + 1000);
  }
  resetFlavors();
  cases.push({
    dur_ms: durMs,
    scene_yaml: sceneYaml("fuzzyaml", durMs / 1000, counts, "mp3"),
    hits_by_synth: hitsBySynth,
  });
  // sceneYaml emits pulse streams in BANDS order; the generators expand
  // them in that order too, so the expected list is the same concat.
  expected.push(BANDS.flatMap((band) => {
    const hits = hitsBySynth[band];
    if (!hits) return [];
    const hitsSec = hits.map(([t, v, p]): FHit =>
      p === undefined ? [t / 1000, v] : [t / 1000, v, p]);
    return bandStrikes(band, hitsSec, 0, durMs / 1000, undefined, []);
  }));
}

/* ── the Python side, on the same cases ── */
const py = ["../.venv/bin/python", "python3"].find((p) =>
  p === "python3" || existsSync(p))!;
const proc = spawnSync(py, ["../tools/fuzz_check.py"], {
  input: JSON.stringify({ cases }), encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
});
if (proc.status !== 0) {
  console.error(proc.stderr);
  console.error(`FAIL — fuzz_check.py exited ${proc.status}`);
  process.exit(1);
}
interface NormCue {
  t: number; targets: string[]; ms: number; intensity: number;
  color: number[]; decay: number; attack: number; pixels: string;
}
const { results } = JSON.parse(proc.stdout) as
  { results: { esphome: NormCue[]; previewer: NormCue[] }[] };

/* ── compare all three ── */
const normTs = (c: Strikes[number]): NormCue => ({
  t: c.t,
  targets: [...c.targets!],
  ms: c.ms!,
  // TS keeps full float colours for the audition; the generators write
  // 3-decimal YAML. Rounding here must land on the same digits.
  intensity: c.intensity!,
  color: c.color!.map(r3),
  decay: c.decay!,
  attack: c.attack ?? 0,
  pixels: c.pixels ?? "all",
});

let pass = 0;
const fails: string[] = [];
const ties: string[] = [];
const close = (a: number, b: number): boolean => Math.abs(a - b) <= 0.0011;

function diffCue(a: NormCue, b: NormCue): string | null {
  for (const k of ["t", "ms", "decay", "attack", "pixels"] as const) {
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
      const kind = close(a.color[c]!, b.color[c]!) ? "rounding-tie " : "";
      return `${kind}color[${c}]: ${a.color[c]} vs ${b.color[c]}`;
    }
  }
  return null;
}

/* The previewer sorts its cue list by time for playback (a legitimate
   difference in ORDER, not content), while pulse_cues and bandStrikes emit
   per-stream. The multi-band yaml cases interleave streams, so those are
   compared as canonically-sorted multisets; single-stream cases stay
   order-exact. */
const canon = (list: NormCue[]): NormCue[] => [...list].sort((a, b) =>
  JSON.stringify(a) < JSON.stringify(b) ? -1
    : JSON.stringify(a) > JSON.stringify(b) ? 1 : 0);

for (let i = 0; i < cases.length; i++) {
  const multi = "scene_yaml" in cases[i]!;
  const ts0 = expected[i]!.map(normTs);
  const ts = multi ? canon(ts0) : ts0;
  for (const [name, got0] of [["esphome", results[i]!.esphome],
                              ["previewer", results[i]!.previewer]] as const) {
    const got = multi ? canon(got0) : got0;
    if (got.length !== ts.length) {
      fails.push(`case ${i} ${name}: ${got.length} strikes, TS has ${ts.length}`);
      continue;
    }
    let bad = false;
    for (let j = 0; j < ts.length; j++) {
      const d = diffCue(ts[j]!, got[j]!);
      if (d) {
        const line = `case ${i} ${name} cue ${j} (t=${ts[j]!.t}): ${d}`;
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
