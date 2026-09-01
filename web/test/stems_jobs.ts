/**
 * The two pieces of desk state that used to be module-level `let`s.
 *
 * Both sat outside the tested state machine, and one of them was the site of
 * a real bug: JB1-10, where switching tracks abandoned the stems poll and
 * re-enabled "Split voices", putting a SECOND Demucs run on the same track
 * one click away. That guard now lives in `createStemsJobs()`, and this is
 * the file that holds it — no DOM, no fetch, plain node.
 *
 * `createCastleBus()` is here for the same reason: the presence/busy signals
 * are a value a test can build fresh, so "a poll landing mid-upload must not
 * flip the masthead to 'not answering'" is an assertion rather than a story.
 */

import { createStemsJobs } from "../src/stems_jobs.js";
import { createCastleBus } from "../src/castle_bus.js";
import type { EtaHandle } from "../src/eta.js";

let pass = 0;
const fails: string[] = [];
const ok = (c: boolean, m: string): void => { if (c) pass++; else fails.push(m); };

/** An ETA handle with no clock in it — the registry only ever stores it. */
const eta = (line: string): EtaHandle =>
  ({ line: () => line, stop: () => {} }) as unknown as EtaHandle;

/* ── The double-Demucs guard (JB1-10) ───────────────────────────────── */
{
  const jobs = createStemsJobs();
  ok(jobs.size === 0, "a fresh registry is empty");
  ok(jobs.busy("ballad") === false, "an untouched track is not busy");

  ok(jobs.claim("ballad") === true, "the first claim on a track is granted");
  ok(jobs.claim("ballad") === false,
     "a SECOND claim on the same track is refused — this is JB1-10");
  ok(jobs.busy("ballad") === true, "a claimed track is busy before the studio answers");
  ok(jobs.running("ballad") === undefined,
     "a claimed-but-unanswered track has no job to show yet");
  ok(jobs.claim("citizens") === true, "another track is unaffected");
  ok(jobs.size === 2, `two tracks in flight, got ${jobs.size}`);

  jobs.attach("ballad", "job-7", eta("about 20 s left"));
  ok(jobs.running("ballad")?.jobId === "job-7", "the studio's job id is kept");
  ok(jobs.running("ballad")?.eta.line() === "about 20 s left", "the ETA is kept");
  ok(jobs.claim("ballad") === false, "an ANSWERED track still refuses a second split");

  jobs.release("ballad");
  ok(jobs.busy("ballad") === false, "a released track is free again");
  ok(jobs.running("ballad") === undefined, "and has no job");
  ok(jobs.claim("ballad") === true, "and may be split again");
  jobs.release("ballad");
  jobs.release("ballad");
  ok(jobs.size === 1, "releasing twice is safe");

  // The window the synchronous claim exists to close: a track released
  // while its request was still out must not be resurrected by the answer.
  const late = createStemsJobs();
  late.claim("ballad");
  late.release("ballad");
  late.attach("ballad", "job-9", eta("stale"));
  ok(late.busy("ballad") === false,
     "an answer arriving after a release does not re-enter the registry");
  ok(late.size === 0, "and leaves nothing behind");
}

/* ── The castle bus ─────────────────────────────────────────────────── */
{
  const bus = createCastleBus();
  const seen: boolean[] = [];
  bus.onPresence(v => seen.push(v));
  ok(bus.isLive() === false, "a fresh bus has not seen a castle");
  bus.setLive(false);
  ok(seen.length === 0, "no-change presence fires nothing");
  bus.setLive(true);
  bus.setLive(true);
  bus.setLive(false);
  ok(seen.join(",") === "true,false", `presence edges only, got ${seen.join(",")}`);

  let changes = 0;
  bus.onChanged(() => changes++);
  bus.changed();
  bus.changed();
  ok(changes === 2, "every castleChanged reaches its listener");

  let cards = 0;
  bus.onCardChanged(() => cards++);
  bus.cardChanged();
  ok(cards === 1 && changes === 2, "the card signal is its own channel");

  // A send in flight counts as liveness — J1-8: a status poll landing
  // mid-upload used to flip the masthead to "castle not answering".
  ok(bus.isBusy() === false, "idle bus is not busy");
  let finish = (): void => {};
  const p = bus.busy(new Promise<void>(res => { finish = res; }));
  ok(bus.isBusy() === true, "a transfer in flight reads as busy");
  const q = bus.busy(Promise.resolve());
  finish();
  await Promise.all([p, q]);
  ok(bus.isBusy() === false, "both transfers settled, the bus is idle again");

  const boom = bus.busy(Promise.reject(new Error("upload died")));
  await boom.catch(() => {});
  ok(bus.isBusy() === false, "a FAILED transfer still clears the busy count");

  const fresh = createCastleBus();
  ok(fresh.isLive() === false && fresh.isBusy() === false,
     "a second bus shares no state with the first");
}

console.log(`stems jobs + castle bus: ${pass} assertions`);
if (fails.length) {
  console.error(`\nFAILED — ${fails.length}:`);
  for (const f of fails) console.error("  " + f);
  process.exit(1);
}
console.log("PASS");
