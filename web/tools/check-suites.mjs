// The suite lists in package.json, held to themselves and to the directory.
//
// `npm test` names every suite TWICE — once as an esbuild input, once as a
// `node dist/<name>.mjs` in the run chain — and nothing connected the two
// halves (grade report 2026-09-01 D5). A file added to the bundle list and
// forgotten in the chain compiles on every run and never executes: a green
// suite that tests nothing, which is the worst failure mode a test harness
// has. A file in neither is worse still — it is invisible.
//
// Three assertions, run as the first step of `npm test` so the answer arrives
// before the compile rather than after it:
//
//   1. within each script, the bundled set and the executed set are equal;
//   2. no name is bundled or run twice;
//   3. every test/*.ts that is not a named helper appears in exactly one
//      script — so adding a suite file and no script line is a failure, not
//      a silent omission.
//
// Kept as a check rather than a glob-driven runner on purpose: the ORDER of
// the chain is meaningful (the cheap logic suites run before the slow parity
// ones, so a broken build fails in seconds), and a glob would quietly decide
// it. This way package.json stays the readable list and cannot go stale.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Modules under test/ that are NOT suites: imported by suites, never run on
// their own. Named here — one short list — so "not a suite" is a decision
// somebody wrote down rather than a property of a filename.
const HELPERS = new Set(["desk_harness.ts"]);

/** The `test/<name>.ts` inputs an esbuild invocation compiles. */
function bundled(script) {
  return [...script.matchAll(/test\/([A-Za-z0-9_]+)\.ts/g)].map((m) => m[1]);
}

/** The `dist/<name>.mjs` bundles a script then executes with node. */
function executed(script) {
  return [...script.matchAll(/node dist\/([A-Za-z0-9_]+)\.mjs/g)].map((m) => m[1]);
}

function dupes(names) {
  const seen = new Set();
  return names.filter((n) => (seen.has(n) ? true : (seen.add(n), false)));
}

const pkg = JSON.parse(readFileSync(resolve(WEB, "package.json"), "utf8"));
const problems = [];
const claimed = new Set();

for (const name of ["test", "test:desk"]) {
  const script = pkg.scripts[name];
  if (!script) {
    problems.push(`package.json has no "${name}" script`);
    continue;
  }
  const build = bundled(script);
  const run = executed(script);
  for (const n of [...build, ...run]) claimed.add(n);

  const notRun = build.filter((n) => !run.includes(n));
  const notBuilt = run.filter((n) => !build.includes(n));
  if (notRun.length)
    problems.push(
      `"${name}": bundled but never run — ${notRun.join(", ")}. ` +
        `Add \`&& node dist/<name>.mjs\` to the chain.`,
    );
  if (notBuilt.length)
    problems.push(
      `"${name}": run but never bundled — ${notBuilt.join(", ")}. ` +
        `Add \`test/<name>.ts\` to the esbuild inputs.`,
    );
  const dupBuild = dupes(build);
  const dupRun = dupes(run);
  if (dupBuild.length) problems.push(`"${name}": bundled twice — ${dupBuild.join(", ")}`);
  if (dupRun.length) problems.push(`"${name}": run twice — ${dupRun.join(", ")}`);
  if (!build.length) problems.push(`"${name}": no suites found — did the script change shape?`);
}

const onDisk = readdirSync(resolve(WEB, "test"))
  .filter((f) => f.endsWith(".ts") && !f.endsWith(".d.ts") && !HELPERS.has(f))
  .map((f) => f.slice(0, -3));
const orphans = onDisk.filter((n) => !claimed.has(n));
if (orphans.length)
  problems.push(
    `web/test/${orphans.join(".ts, web/test/")}.ts is in neither "test" nor ` +
      `"test:desk" — add it to a suite list, or to HELPERS in ` +
      `web/tools/check-suites.mjs if it is not a suite.`,
  );

if (problems.length) {
  console.error("suite lists out of step:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log(`suite lists ok: ${claimed.size} suites, bundled and run`);
