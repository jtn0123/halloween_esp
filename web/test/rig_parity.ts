/**
 * Cross-language fixture geometry: rig.ts against tools/rig_layout.py.
 *
 *     (runs bundled from web/dist — see package.json "test")
 *
 * There are two implementations of where a pixel sits, and there have to be:
 * the desk needs it in the browser, and the generator needs it to bake the
 * tables the firmware indexes (firmware/generated/rig.h). A third copy in C++
 * was the alternative, and this is the cheaper trade — but only while
 * something actually checks that the two agree.
 *
 * A drift here is not cosmetic. `core` decides which pixels a centre strike
 * lands on and `fall` decides where a meteor is; if the desk and the device
 * disagree, the preview is confidently wrong about the show.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { FIXTURES, layoutOf } from "../src/rig.js";

const PY = ["../.venv/bin/python", "../.venv/bin/python3", "python3"]
  .find(p => p.startsWith("python") || existsSync(p)) ?? "python3";

const run = spawnSync(PY, ["../tools/rig_layout.py"], { encoding: "utf8" });
if (run.status !== 0) {
  console.error(run.stderr || run.stdout);
  console.error("FAIL — rig_layout.py exited " + run.status);
  process.exit(1);
}
interface PyLayout {
  n: number; center: number; fallSteps: number;
  core: number[]; walk: number[]; fall: number[]; pos: [number, number][];
}
const theirs = JSON.parse(run.stdout) as Record<string, PyLayout | undefined>;

const r6 = (v: number): number => Math.round(v * 1e6) / 1e6;
let pass = 0;
const fails: string[] = [];
const eq = (a: unknown, b: unknown, msg: string): void => {
  if (JSON.stringify(a) === JSON.stringify(b)) pass++;
  else fails.push(`${msg}\n    ts: ${JSON.stringify(a)}\n    py: ${JSON.stringify(b)}`);
};

for (const fx of FIXTURES) {
  const mine = layoutOf(fx);
  const yours = theirs[fx.id];
  if (!yours) { fails.push(`python has no fixture ${fx.id}`); continue; }
  eq(mine.n, yours.n, `${fx.id}: pixel count`);
  eq(mine.center, yours.center, `${fx.id}: centre pixel`);
  eq(mine.fallSteps, yours.fallSteps, `${fx.id}: fall steps`);
  eq([...mine.core], yours.core, `${fx.id}: core mask`);
  eq(mine.walk.map(r6), yours.walk, `${fx.id}: chase path`);
  eq(mine.fall.map(r6), yours.fall, `${fx.id}: fall path`);
  eq(mine.pos.map(p => [r6(p[0]), r6(p[1])]), yours.pos, `${fx.id}: positions`);
}

console.log(`rig geometry parity: ${pass} checks across ${FIXTURES.length} fixtures`);
if (fails.length) {
  for (const f of fails) console.error("  FAIL " + f);
  process.exit(1);
}
console.log("PASS — the browser and the generator place every pixel identically");
