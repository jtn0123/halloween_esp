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
 * compares per channel. Verdicts are per CLASS, because the two languages
 * cannot agree the same way about everything:
 *
 *   SMOOTH   spirit, seance, chill, throb, soft strobe, chase — sines of t
 *            and palette mixes. Held to float32 rounding: strict (2e-4) for
 *            t under a minute, then a bound that grows with t exactly as a
 *            float32 phase argument does (ulp(t*w)).
 *   STEPPED  hard strobe, meteor — continuous but with a branch; held to the
 *            smooth bound EXCEPT within the float32 phase error of the edge,
 *            where the two sides may legitimately sit on different sides.
 *   NOISE    candle, ember, furnace, wisp, mansion, blood, the eyes blink,
 *            sparkle, scatter — every one goes through
 *            hashf(n) = frac(sin(n*127.1)*43758.5453), which float32 cannot
 *            compute to the same digits as a double (the product alone loses
 *            ~1e-4, and the 43758 turns that into a different fraction). So
 *            the desk and the castle draw the SAME DISTRIBUTION, not the same
 *            frame. These are held to distribution statistics (per-channel
 *            mean within 0.03, same range) and the measured per-frame
 *            divergence is printed, not hidden.
 *
 * Skips (exit 0, says so) when no host C++ compiler is present.
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EFFECTS, applyOverlay, flashGate, defaultParams } from "../dist/effects.mjs";
import { fixture, layoutOf } from "../dist/rig.mjs";

const ROOT = join(import.meta.dirname, "..", "..");
const SEED = Number(process.env.PARITY_SEED ?? 7);
const CASES = Number(process.env.PARITY_CASES ?? 3000);
const NAMES = ["off", "candle", "ember", "furnace", "spirit", "eyes", "seance",
               "wisp", "mansion", "chill", "throb", "strobe", "blood"];
const NOISE = new Set(["candle", "ember", "furnace", "wisp", "mansion", "blood", "eyes"]);
/** Highest angular frequency (rad/s of t) each smooth effect feeds a sine. */
const OMEGA = { spirit: 1.15, seance: 0.80, chill: 0.50, throb: 7.4, strobe: 44, off: 0 };
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
const zones = [...zoneBlock.matchAll(/\{id:\s*(\w+)[^}]*?fixture:\s*(\w+)[^}]*?(?:pixels:\s*(\d+))?[^}]*\}/g)]
  .map((m) => ({ id: m[1], fixture: m[2], pixels: m[3] ? Number(m[3]) : undefined }));
const layouts = zones.map((z) => layoutOf(fixture(z.fixture), z.pixels));

let pass = 0;
const fails = [];
const ok = (cond, msg) => { if (cond) pass++; else fails.push(msg); };
const stat = () => ({ n: 0, max: 0, sum: 0, sumA: [0, 0, 0, 0], sumB: [0, 0, 0, 0] });
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

/* ── noise primitives: attribute the drift to the layer it comes from ── */
const hash = (n) => { const s = Math.sin(n * 127.1) * 43758.5453; return s - Math.floor(s); };
const f32 = Math.fround;
const hash32 = (n) => {   // hashf as float32 would do it, given a 1-ulp sinf
  const s = f32(f32(Math.sin(f32(n * 127.1))) * f32(43758.5453));
  return f32(s - Math.floor(s));
};
const nd = { dbl: 0, emu: 0, ints: 0, nInts: 0, n: 0, maxDbl: 0 };
for (const r of rows.filter((r) => r.kind === "noise")) {
  const d = Math.abs(hash(r.x) - r.hash);
  nd.n++; nd.dbl += d > 0.01; nd.emu += Math.abs(hash32(r.x) - r.hash) > 0.01;
  nd.maxDbl = Math.max(nd.maxDbl, d);
  if (Number.isInteger(r.x) && r.x < 64) { nd.nInts++; nd.ints += d > 0.01; }
  ok(r.hash >= 0 && r.hash < 1 && r.vnoise >= 0 && r.vnoise <= 1 && r.fbm >= 0 && r.fbm <= 1,
     `noise at x=${r.x} out of range: ${r.hash} ${r.vnoise} ${r.fbm}`);
}

/* ── pixels ── */
const perEff = Object.fromEntries(NAMES.map((n) => [n, stat()]));
const perOv = [stat(), stat(), stat(), stat()];
const gates = { same: 0, total: 0, scatterFwOn: 0, scatterDeskOn: 0, scatterN: 0, edges: 0 };
const sparkle = { fwLit: 0, deskLit: 0, n: 0 };

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
  for (let c = 0; c < 4; c++) { s.sumA[c] += r.base[c]; s.sumB[c] += base[c]; }

  if (!NOISE.has(name)) {
    let tol = smoothTol(OMEGA[name], r.t);
    let skip = false;
    if (name === "strobe" && r.soft === 0) {
      // hard strobe: on/off by the sign of sin(44t + seed)
      const ph = r.t * 44 + r.seed;
      skip = nearEdge(ph, Math.abs(Math.sin(ph)));
      tol = 1e-6;
    }
    if (!skip) {
      ok(dBase <= tol, `${name} base t=${r.t} seed=${r.seed} pal=${r.pal} hue=${r.hue} `
        + `soft=${r.soft}: firmware ${r.base} desk ${base} (|d|=${f(dBase)} > ${f(tol)})`);
    } else gates.edges++;
  }

  // Overlays, applied by BOTH sides to the firmware's own base colour so the
  // overlay arithmetic is judged on its own and not through the base's noise.
  const ovl = applyOverlay(r.ov, r.base, r.t, r.p, r.zi, L);
  const dOvl = maxDiff(ovl, r.ovl);
  add(perOv[r.ov], dOvl);
  if (r.ov === 0) ok(dOvl === 0, `overlay none changed a pixel: ${r.ovl} vs ${r.base}`);
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
  if (r.ov === 1) {
    sparkle.n++;
    sparkle.fwLit += maxDiff(r.ovl, r.base) > 0;
    sparkle.deskLit += maxDiff(ovl, r.base) > 0;
  }

  // Gates: centre/ring/all are exact; scatter is a hash and is statistical.
  const gate = flashGate(r.mode, r.p, r.zi, r.epoch, L);
  gates.total++;
  if (Math.abs(gate - r.gate) < 1e-6) gates.same++;
  if (r.mode === 1) {
    gates.scatterN++; gates.scatterFwOn += r.gate === 1; gates.scatterDeskOn += gate === 1;
  } else {
    ok(Math.abs(gate - r.gate) < 1e-6,
       `gate mode ${r.mode} p=${r.p} zi=${r.zi}: firmware ${r.gate} desk ${gate}`);
  }
}

/* ── distribution checks for the noise class ── */
for (const name of NOISE) {
  const s = perEff[name];
  if (!s.n) continue;
  for (let c = 0; c < 4; c++) {
    const d = Math.abs(s.sumA[c] - s.sumB[c]) / s.n;
    ok(d <= 0.03, `${name} ch${c}: firmware mean differs from desk mean by ${f(d)} `
      + `over ${s.n} frames — not the same distribution`);
  }
}
if (sparkle.n) {
  const a = sparkle.fwLit / sparkle.n, b = sparkle.deskLit / sparkle.n;
  ok(Math.abs(a - b) <= 0.05, `sparkle glint rate: firmware ${a.toFixed(3)} desk ${b.toFixed(3)}`);
}
if (gates.scatterN) {
  const a = gates.scatterFwOn / gates.scatterN, b = gates.scatterDeskOn / gates.scatterN;
  ok(Math.abs(a - b) <= 0.08, `scatter hit rate: firmware ${a.toFixed(3)} desk ${b.toFixed(3)}`);
}

/* ── report ── */
const pct = (k, n) => n ? `${(100 * k / n).toFixed(1)}%` : "-";
console.log(`firmware parity: seed ${SEED}, ${rows.length} rows, ${cxx}`);
console.log(`  hashf(float32) vs hash(double): ${pct(nd.dbl, nd.n)} of probes differ by >0.01 `
  + `(max ${f(nd.maxDbl)}; integer args 0..63: ${pct(nd.ints, nd.nInts)}); `
  + `vs a float32-emulated hash: ${pct(nd.emu, nd.n)}`);
console.log("  effect      n    max|d|    mean|d|   class");
for (const name of NAMES) {
  const s = perEff[name];
  if (!s.n) continue;
  console.log(`  ${name.padEnd(9)} ${String(s.n).padStart(4)}  ${f(s.max)}  ${f(s.sum / s.n)}  `
    + (NOISE.has(name) ? "noise (statistical)" : "smooth"));
}
console.log("  overlays on the firmware base: " + perOv.map((s, i) =>
  `ov${i} n=${s.n} max|d| ${f(s.max)}`).join(", "));
console.log(`  gates: ${gates.same}/${gates.total} identical; scatter on-rate firmware `
  + `${pct(gates.scatterFwOn, gates.scatterN)} desk ${pct(gates.scatterDeskOn, gates.scatterN)}; `
  + `sparkle glint rate firmware ${pct(sparkle.fwLit, sparkle.n)} desk ${pct(sparkle.deskLit, sparkle.n)}`);
console.log(`  ${gates.edges} frames sat within float32 error of a branch edge and were not judged`);

if (fails.length) {
  console.error(`\nFAILED — ${fails.length} of ${fails.length + pass} checks (seed ${SEED}):`);
  for (const m of fails.slice(0, 25)) console.error("  " + m);
  if (fails.length > 25) console.error(`  ...and ${fails.length - 25} more`);
  process.exit(1);
}
console.log(`PASS — ${pass} checks`);
