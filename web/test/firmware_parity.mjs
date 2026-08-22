/**
 * Cross-language NUMERIC parity: firmware/castle_effects.h (C++, float32,
 * compiled here with the host compiler) against web/src/effects.ts (the
 * double-precision port the cue desk draws with).
 *
 *     node test/firmware_parity.mjs                  (from web/; dist built)
 *     PARITY_SEED=9 PARITY_CASES=20000 node test/firmware_parity.mjs
 *
 * tests/cxx/parity_dump.cpp renders a seeded corpus of (effect, palette, hue,
 * soft, t, zone, pixel, overlay, mask, epoch) and prints the firmware's base
 * colour, overlaid colour and strike gate as JSON lines. This recomputes the
 * same frames with the TypeScript, from the identical float32 inputs, and
 * compares per channel. EVERY frame is judged — the desk is meant to be the
 * porch's twin, frame for frame — and the only slack allowed is what float32
 * arithmetic itself costs:
 *
 *   SMOOTH   spirit, seance, chill, throb, soft strobe, chase — sines of t
 *            and palette mixes. Held to float32 rounding: strict (2e-4) for
 *            t under a minute, then a bound that grows with t exactly as a
 *            float32 phase argument does (ulp(t*w)).
 *   NOISE    candle, ember, furnace, wisp, mansion, blood — value noise over
 *            an integer lattice. The lattice hash is an integer mix that is
 *            BIT-IDENTICAL on both sides (see hashi/hash3 below), so the
 *            only drift left is the float32 fraction between lattice points,
 *            which is ulp(argument) times the noise's slope: the bound is
 *            the same shape as the smooth one, scaled by each effect's gain.
 *   STEPPED  hard strobe, the eyes blink, sparkle's time cell, meteor,
 *            scatter — continuous or exact but with a branch; held to the
 *            bound above EXCEPT within the float32 error of the edge, where
 *            the two sides may legitimately sit on different sides. Scatter
 *            and sparkle's glint value have no float in their inputs at all
 *            and are held EXACT.
 *
 * Before v5.24 the noise went through frac(sin(n*127.1)*43758.5453), which
 * float32 cannot compute to the same digits as a double, and this file could
 * only hold the noise effects to "the same distribution". That class is
 * gone; if it ever comes back, so has the bug.
 *
 * Skips (exit 0, says so) when no host C++ compiler is present.
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  EFFECTS, applyOverlay, flashGate, defaultParams, hashi, hash3, vnoise, fbm,
} from "../dist/effects.mjs";
import { fixture, layoutOf } from "../dist/rig.mjs";

const ROOT = join(import.meta.dirname, "..", "..");
const SEED = Number(process.env.PARITY_SEED ?? 7);
const CASES = Number(process.env.PARITY_CASES ?? 3000);
const NAMES = ["off", "candle", "ember", "furnace", "spirit", "eyes", "seance",
               "wisp", "mansion", "chill", "throb", "strobe", "blood"];
/** Highest angular frequency (rad/s of t) each effect feeds a sine. */
const OMEGA = { spirit: 1.15, seance: 0.80, chill: 0.50, throb: 7.4, strobe: 44,
                mansion: 0.38, eyes: 3.1, off: 0 };
/** Noise effects: the fbm argument is t*w + seed*k, and `gain` is how much
 *  of one unit of noise reaches a channel (the brightest channel's slope). */
const NOISE = {
  candle: { w: 1.4, k: 3.7, gain: 0.55 },
  ember: { w: 0.63, k: 2.2, gain: 0.16 * 0.85 },
  furnace: { w: 2.5, k: 0.9, gain: 0.20 },
  wisp: { w: 2.1, k: 5.3, gain: 0.82 },
  mansion: { w: 1.05, k: 2.7, gain: 0.16 * 0.62 },
  blood: { w: 0.35, k: 1.7, gain: 0.05 },
};
const F32_EPS = 2 ** -23;

/* ── build + run the firmware dump ── */
const cxx = ["clang++", "g++"].find((c) => spawnSync(c, ["--version"]).status === 0);
if (!cxx) {
  console.log("firmware parity: SKIP — no host C++ compiler");
  process.exit(0);
}
const bin = join(mkdtempSync(join(tmpdir(), "castle-parity-")), "parity_dump");
const build = spawnSync(cxx, ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
  "-I", join(ROOT, "firmware"), join(ROOT, "tests", "cxx", "parity_dump.cpp"), "-o", bin],
  { encoding: "utf8" });
if (build.status !== 0) {
  console.error(build.stderr);
  console.error("FAIL — parity_dump.cpp did not compile");
  process.exit(1);
}
const run = spawnSync(bin, [String(SEED), String(CASES)], { encoding: "utf8", maxBuffer: 1 << 28 });
if (run.status !== 0) { console.error(run.stderr); process.exit(1); }
const rows = run.stdout.trim().split("\n").map((l) => JSON.parse(l));

/* ── which fixture each firmware zone index holds: scenes.yaml is what the
      generator baked rig.h from, so read the same block it read. ── */
const scenesYaml = readFileSync(join(ROOT, "scenes", "scenes.yaml"), "utf8");
const zoneBlock = scenesYaml.split(/^zones:\s*$/m)[1].split(/^\S/m)[0];
// One entry at a time, then each field out of it: three lazy `[^}]*?` runs in
// a single pattern can hand characters back and forth, which is the shape that
// goes super-linear on an entry that never closes (Sonar S5852). A brace-free
// body, matched once, cannot backtrack at all.
const zones = [...zoneBlock.matchAll(/\{([^{}]*)\}/g)].map((m) => {
  const body = m[1];
  const id = /\bid:\s*(\w+)/.exec(body);
  const fixture = /\bfixture:\s*(\w+)/.exec(body);
  const pixels = /\bpixels:\s*(\d+)/.exec(body);
  return id && fixture
    ? { id: id[1], fixture: fixture[1], pixels: pixels ? Number(pixels[1]) : undefined }
    : null;
}).filter((z) => z !== null);
const layouts = zones.map((z) => layoutOf(fixture(z.fixture), z.pixels));

let pass = 0;
const fails = [];
const ok = (cond, msg) => { if (cond) pass++; else fails.push(msg); };
const stat = () => ({ n: 0, max: 0, sum: 0 });
const add = (s, d) => { s.n++; s.sum += d; if (d > s.max) s.max = d; };
const maxDiff = (a, b) => Math.max(...[0, 1, 2, 3].map((c) => Math.abs(a[c] - b[c])));
const f = (v) => v.toExponential(2);

/* geometry the dump rendered against must be the geometry the desk uses */
for (const r of rows.filter((r) => r.kind === "zone")) {
  const L = layouts[r.zi];
  ok(L && L.n === r.n, `zone ${r.zi}: firmware n=${r.n}, desk n=${L?.n}`);
  ok(L && (L.center ?? -1) === r.center, `zone ${r.zi}: centre ${r.center} vs ${L?.center}`);
  ok(L && L.fallSteps === r.fall_steps, `zone ${r.zi}: fall steps ${r.fall_steps} vs ${L?.fallSteps}`);
}

/* ── noise primitives ──
   hashi / hash3 are integer mixes with a 24-bit result: the firmware's float
   and the desk's double must be the SAME number, not close. vnoise/fbm at a
   real argument carry float32's fraction error and are bounded by slope. */
/** Float32 bound on a value-noise evaluation at argument x: the fraction
 *  between lattice points is off by ulp(x), the smoothstep slope is 1.5, and
 *  fbm's three octaves weigh 0.55·1 + 0.30·2.13 + 0.15·4.31 of that. */
const noiseTol = (x, gain = 1) => 2e-4 + 12 * Math.abs(x) * F32_EPS * gain;
const nd = { n: 0, exact: 0, maxV: 0, maxF: 0 };
for (const r of rows.filter((r) => r.kind === "noise")) {
  nd.n++;
  const hi = hashi(r.k), h3 = hash3(r.a, r.b, r.c);
  ok(hi === r.hashi, `hashi(${r.k}): firmware ${r.hashi} desk ${hi} — not bit-identical`);
  ok(h3 === r.hash3, `hash3(${r.a},${r.b},${r.c}): firmware ${r.hash3} desk ${h3}`);
  nd.exact += hi === r.hashi && h3 === r.hash3;
  const dv = Math.abs(vnoise(r.x) - r.vnoise), df = Math.abs(fbm(r.x) - r.fbm);
  nd.maxV = Math.max(nd.maxV, dv); nd.maxF = Math.max(nd.maxF, df);
  ok(dv <= noiseTol(r.x), `vnoise(${r.x}): firmware ${r.vnoise} desk ${vnoise(r.x)} (|d|=${f(dv)})`);
  ok(df <= noiseTol(r.x * 4.31 + 27.7), `fbm(${r.x}): firmware ${r.fbm} desk ${fbm(r.x)} (|d|=${f(df)})`);
  ok(r.hashi >= 0 && r.hashi < 1 && r.hash3 >= 0 && r.hash3 < 1
     && r.vnoise >= 0 && r.vnoise <= 1 && r.fbm >= 0 && r.fbm <= 1,
     `noise out of range: ${r.hashi} ${r.hash3} ${r.vnoise} ${r.fbm}`);
}
/* The mix must still be NOISE: uniform over 0..1 on the inputs the effects
   feed it — consecutive lattice cells, and the small (cell, pixel, zone)
   triples — with no stripe from the low bits. 16 buckets over 65536 samples:
   the expected 4096 per bucket has a sigma of ~62, so 8 % is ~5 sigma. */
const uniform = (label, sample, n = 65536) => {
  const buckets = new Array(16).fill(0);
  let sum = 0;
  for (let i = 0; i < n; i++) { const v = sample(i); sum += v; buckets[Math.floor(v * 16)]++; }
  const worst = Math.max(...buckets.map((b) => Math.abs(b / (n / 16) - 1)));
  ok(worst <= 0.08, `${label}: a bucket is ${(100 * worst).toFixed(1)}% off uniform`);
  ok(Math.abs(sum / n - 0.5) <= 0.01, `${label}: mean ${(sum / n).toFixed(4)}`);
  return worst;
};
const uni = [
  uniform("hashi over consecutive cells", (i) => hashi(i)),
  uniform("hashi over cells around 10^6", (i) => hashi(1000000 + i)),
  uniform("hash3 over (cell, pixel<16, zone<4)", (i) => hash3(i >> 6, (i >> 2) & 15, i & 3)),
  uniform("hash3 over (pixel<16, zone<4, epoch)", (i) => hash3((i >> 2) & 15, i & 3, i >> 6)),
];

/* ── pixels ── */
const perEff = Object.fromEntries(NAMES.map((n) => [n, stat()]));
const perOv = [stat(), stat(), stat(), stat()];
const gates = { same: 0, total: 0, scatterN: 0, edges: 0 };
const sparkle = { fwLit: 0, deskLit: 0, n: 0, both: 0 };

/** Float32 bound on |sin(w*t + k)| evaluation error: ulp of the phase, with
 *  a margin for the two products, plus a floor for the mix arithmetic. */
const smoothTol = (w, t) => (t < 60 ? 2e-4 : 2e-4 + 4 * Math.abs(w * t) * F32_EPS);
/** True when the phase sits within float32 error of a branch edge. */
const nearEdge = (phase, edgeDist) => edgeDist <= 8 * Math.abs(phase) * F32_EPS + 1e-6;

for (const r of rows.filter((r) => r.kind === "px")) {
  const name = NAMES[r.eff];
  const P = { ...defaultParams(), hue: r.hue, soft: r.soft === 1, pal: r.pal };
  const L = layouts[r.zi];
  const base = EFFECTS[name](r.t, r.seed, P);
  for (let c = 0; c < 4; c++) {
    ok(Number.isFinite(base[c]) && base[c] >= 0 && base[c] <= 1,
       `${name} t=${r.t} ch${c}: desk value ${base[c]} out of range`);
  }
  const s = perEff[name];
  const dBase = maxDiff(base, r.base);
  add(s, dBase);

  let tol = smoothTol(OMEGA[name] ?? 0, r.t);
  let skip = false;
  if (NOISE[name]) {
    const { w, k, gain } = NOISE[name];
    tol += noiseTol((r.t * w + r.seed * k) * 4.31 + 27.7, gain);
  }
  if (name === "strobe" && r.soft === 0) {
    // hard strobe: on/off by the sign of sin(44t + seed)
    const ph = r.t * 44 + r.seed;
    skip = nearEdge(ph, Math.abs(Math.sin(ph)));
    tol = 1e-6;
  }
  if (name === "eyes") {
    // blink: a branch on vnoise(1.9t + 0.55 seed) > 0.82 — exact lattice
    // values, so only the float32 fraction can put the two sides apart.
    const x = r.t * 1.9 + r.seed * 0.55;
    skip = Math.abs(vnoise(x) - 0.82) <= noiseTol(x);
  }
  if (!skip) {
    ok(dBase <= tol, `${name} base t=${r.t} seed=${r.seed} pal=${r.pal} hue=${r.hue} `
      + `soft=${r.soft}: firmware ${r.base} desk ${base} (|d|=${f(dBase)} > ${f(tol)})`);
  } else gates.edges++;

  // Overlays, applied by BOTH sides to the firmware's own base colour so the
  // overlay arithmetic is judged on its own and not through the base.
  const ovl = applyOverlay(r.ov, r.base, r.t, r.p, r.zi, L);
  const dOvl = maxDiff(ovl, r.ovl);
  add(perOv[r.ov], dOvl);
  if (r.ov === 0) ok(dOvl === 0, `overlay none changed a pixel: ${r.ovl} vs ${r.base}`);
  if (r.ov === 1) {
    // sparkle: the glint is hash3(cell, p, zi) — exact — and the only float
    // in sight is the time cell floor(7t), a branch like any other.
    const ph = r.t * 7, frac = ph - Math.floor(ph);
    sparkle.n++;
    const fwLit = maxDiff(r.ovl, r.base) > 0, deskLit = maxDiff(ovl, r.base) > 0;
    sparkle.fwLit += fwLit; sparkle.deskLit += deskLit; sparkle.both += fwLit === deskLit;
    if (nearEdge(ph, Math.min(frac, 1 - frac))) gates.edges++;
    else ok(dOvl <= 1e-5 && fwLit === deskLit,
            `sparkle p=${r.p} zi=${r.zi} t=${r.t}: firmware ${r.ovl} desk ${ovl} (|d|=${f(dOvl)})`);
  }
  if (r.ov === 2) {
    const ph = r.t * 0.45 + r.zi * 0.37;
    // The head's width is set in pixels, so the phase error is amplified by
    // the pixel count (loop_dist * span * 0.9) before it reaches the colour.
    ok(dOvl <= 2e-4 + 4 * L.n * Math.abs(ph) * F32_EPS + 1e-3 * (r.t >= 60),
       `chase p=${r.p} zi=${r.zi} t=${r.t}: firmware ${r.ovl} desk ${ovl} (|d|=${f(dOvl)}, ph=${ph})`);
  }
  if (r.ov === 3) {
    const ph = r.t / 2.6 + r.zi * 0.41;
    const frac = ph - Math.floor(ph);
    const edge = Math.min(frac, 1 - frac, Math.abs(frac - 0.12));
    if (nearEdge(ph, edge)) gates.edges++;
    // The forming flash amplifies the phase by 0.8/0.12, so the bound does too.
    else ok(dOvl <= 2e-4 + 10 * Math.abs(ph) * F32_EPS + 1e-3 * (r.t >= 60),
            `meteor p=${r.p} zi=${r.zi} t=${r.t}: firmware ${r.ovl} desk ${ovl} (|d|=${f(dOvl)})`);
  }

  // Gates: centre/ring/all are table lookups and scatter is hash3 over three
  // integers — every one is exact, frame for frame.
  // (Math.fround: the dump prints the firmware's float32 0.15f, which is
  // not the double 0.15 — same constant, different precision.)
  const gate = flashGate(r.mode, r.p, r.zi, r.epoch, L);
  gates.total++;
  if (Math.fround(gate) === Math.fround(r.gate)) gates.same++;
  if (r.mode === 1) gates.scatterN++;
  ok(Math.fround(gate) === Math.fround(r.gate), `gate mode ${r.mode} p=${r.p} zi=${r.zi} `
    + `epoch=${r.epoch}: firmware ${r.gate} desk ${gate}`);
}

/* ── report ── */
const pct = (k, n) => n ? `${(100 * k / n).toFixed(1)}%` : "-";
console.log(`firmware parity: seed ${SEED}, ${rows.length} rows, ${cxx}`);
console.log(`  hashi/hash3 bit-identical on ${nd.exact}/${nd.n} probes; vnoise max|d| ${f(nd.maxV)}, `
  + `fbm max|d| ${f(nd.maxF)}; worst uniformity bucket ${(100 * Math.max(...uni)).toFixed(1)}% off`);
console.log("  effect      n    max|d|    mean|d|   class");
for (const name of NAMES) {
  const s = perEff[name];
  if (!s.n) continue;
  console.log(`  ${name.padEnd(9)} ${String(s.n).padStart(4)}  ${f(s.max)}  ${f(s.sum / s.n)}  `
    + (NOISE[name] ? "noise (frame-exact)" : name === "eyes" ? "noise+branch" : "smooth"));
}
console.log("  overlays on the firmware base: " + perOv.map((s, i) =>
  `ov${i} n=${s.n} max|d| ${f(s.max)}`).join(", "));
console.log(`  gates: ${gates.same}/${gates.total} identical (${gates.scatterN} scatter); `
  + `sparkle lit on both sides ${sparkle.both}/${sparkle.n} frames, glint rate firmware `
  + `${pct(sparkle.fwLit, sparkle.n)} desk ${pct(sparkle.deskLit, sparkle.n)}`);
console.log(`  ${gates.edges} frames sat within float32 error of a branch edge and were not judged`);

if (fails.length) {
  console.error(`\nFAILED — ${fails.length} of ${fails.length + pass} checks (seed ${SEED}):`);
  for (const m of fails.slice(0, 25)) console.error("  " + m);
  if (fails.length > 25) console.error(`  ...and ${fails.length - 25} more`);
  process.exit(1);
}
console.log(`PASS — ${pass} checks`);
