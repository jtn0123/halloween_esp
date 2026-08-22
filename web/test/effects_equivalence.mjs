/**
 * Prove the TypeScript effect port is numerically identical to the JavaScript
 * it replaced.
 *
 *     node test/effects_equivalence.mjs
 *
 * A type checker cannot catch a transposed digit or a dropped term, and the
 * effect maths is the one part of this project that is also implemented twice
 * more (in firmware/castle_effects.h and, by eye, in the scene design). A
 * silent numeric drift here would show up as "the castle looks slightly wrong"
 * weeks later, with nothing to point at.
 *
 * legacy_effects.mjs is the pre-migration code extracted verbatim. It is the
 * reference, not a reimplementation, which is what makes this test meaningful.
 * One primitive in it was re-pinned on purpose (see its header): the lattice
 * hash became the integer mix the firmware runs, so that the desk and the
 * porch agree frame for frame (web/test/firmware_parity.mjs). Everything
 * built on top of that hash is still the original.
 */

import { EFFECTS as LEGACY, toScreen as legacyToScreen, P as legacyP } from "./legacy_effects.mjs";
import { EFFECTS as PORTED, toScreen as portedToScreen, defaultParams } from "../dist/effects.mjs";

const NAMES = Object.keys(LEGACY);
const EPS = 1e-12;          // identical maths should be bit-identical, not close

let checks = 0;
const failures = [];

/** Sweep the parameters the effects actually read, not just the defaults. */
const PARAM_SETS = [
  { depth: 0.55, speed: 1.4, bright: 0.85, hue: 0.5, soft: false, stops: 0.42 },
  { depth: 0.10, speed: 0.3, bright: 1.00, hue: 0.0, soft: false, stops: 0.10 },
  { depth: 1.00, speed: 3.0, bright: 0.20, hue: 1.0, soft: true, stops: 0.90 },
];

for (const params of PARAM_SETS) {
  // The legacy code reads a module-level P; mutate it to match.
  Object.assign(legacyP, params);
  const ported = { ...defaultParams(), ...params };

  for (const name of NAMES) {
    const a = LEGACY[name];
    const b = PORTED[name];
    if (typeof b !== "function") {
      failures.push(`${name}: missing from the ported EFFECTS table`);
      continue;
    }
    // Times chosen to cross the interesting boundaries: t=0, the strobe's
    // sign flip, the eyes blink threshold, and a long way out where the
    // noise has wandered.
    for (const t of [0, 0.017, 0.5, 1.0, 3.14159, 7.5, 22.7, 61.3, 137.9]) {
      for (let px = 0; px < 7; px++) {
        for (let z = 0; z < 3; z++) {
          const seed = z * 4.7 + px * 1.31;
          const x = a(t, seed);
          const y = b(t, seed, ported);
          checks++;
          for (let i = 0; i < 4; i++) {
            const dx = x[i] ?? 0, dy = y[i] ?? 0;
            if (Math.abs(dx - dy) > EPS) {
              failures.push(
                `${name} t=${t} seed=${seed.toFixed(2)} ch${i}: ` +
                `legacy ${dx} vs ported ${dy}`);
            }
          }
        }
      }
    }
  }
}

// toScreen matters just as much — it is what turns RGBW into what you look at.
for (const c of [[0, 0, 0, 0], [1, 1, 1, 1], [0.34, 0.05, 0, 1], [0.2, 0.9, 0.4, 0.3]]) {
  const x = legacyToScreen(c), y = portedToScreen(c);
  checks++;
  for (let i = 0; i < 3; i++) {
    if (Math.abs((x[i] ?? 0) - (y[i] ?? 0)) > EPS) {
      failures.push(`toScreen(${c}) ch${i}: ${x[i]} vs ${y[i]}`);
    }
  }
}

const missing = NAMES.filter(n => !(n in PORTED));
const extra = Object.keys(PORTED).filter(n => !NAMES.includes(n));
if (missing.length) failures.push(`effects lost in the port: ${missing.join(", ")}`);

console.log(`effect equivalence: ${checks} comparisons across ` +
            `${NAMES.length} effects x ${PARAM_SETS.length} parameter sets`);
if (extra.length) console.log(`  note: ported table adds ${extra.join(", ")}`);

if (failures.length) {
  console.error(`\nFAILED — ${failures.length} mismatch(es):`);
  for (const f of failures.slice(0, 20)) console.error("  " + f);
  if (failures.length > 20) console.error(`  ...and ${failures.length - 20} more`);
  process.exit(1);
}
console.log("PASS — the port is numerically identical to the original");
