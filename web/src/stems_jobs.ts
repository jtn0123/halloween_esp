/**
 * Which tracks have a Demucs split running — the stems panel's job registry.
 *
 * This used to be a bare `Map` at the top of stems_view.ts, and it was the
 * site of a real bug (JB1-10): switching tracks abandoned the poll and
 * re-enabled "Split voices", so a SECOND Demucs run on the same track was
 * one click away. The map has to outlive any one view of it — that is the
 * whole point — but a module-level `let` is also the one piece of the
 * panel's state no test could construct, reset or assert.
 *
 * So the registry is a value now: `createStemsJobs()` for a test, the
 * shared `stemsJobs` for the app, injected through `StemsDeps.jobs`.
 * Nothing here touches the DOM or the network, so `web/test/stems_jobs.ts`
 * drives it under plain node.
 *
 * The claim is deliberately SYNCHRONOUS and comes before the request: the
 * panel claims the track, then asks the studio to split it. Setting the
 * entry after the await would leave exactly the window this guard exists
 * to close.
 */

import type { EtaHandle } from "./eta.js";

/** A split the studio accepted: its job id, and the ETA line being learned. */
export interface StemsJob {
  jobId: string;
  eta: EtaHandle;
}

export interface StemsJobs {
  /** True from `claim` until `release` — claimed but not yet answered
   *  counts, which is what makes the guard airtight across the await. */
  busy(trackId: string): boolean;
  /** The studio's answer for a track, once `attach` has it. */
  running(trackId: string): StemsJob | undefined;
  /** Reserve a track for a split. False when one is already in flight. */
  claim(trackId: string): boolean;
  /** Record what the studio gave back. Ignored for an unclaimed track —
   *  a release (a track deleted mid-request) must not resurrect the job. */
  attach(trackId: string, jobId: string, eta: EtaHandle): void;
  /** Finished, failed, refused or given up on. Safe to call twice. */
  release(trackId: string): void;
  /** How many splits are in flight — for tests and for the panel's own
   *  "is anything running at all" questions. */
  readonly size: number;
}

export function createStemsJobs(): StemsJobs {
  const jobs = new Map<string, StemsJob | null>();
  return {
    busy: (id) => jobs.has(id),
    running: (id) => jobs.get(id) ?? undefined,
    claim(id) {
      if (jobs.has(id)) return false;
      jobs.set(id, null);
      return true;
    },
    attach(id, jobId, eta) {
      if (jobs.has(id)) jobs.set(id, { jobId, eta });
    },
    release(id) {
      jobs.delete(id);
    },
    get size() {
      return jobs.size;
    },
  };
}

/** The app's one registry. A test builds its own instead. */
export const stemsJobs: StemsJobs = createStemsJobs();
