/**
 * The one question the device bridge asks the network, and the shape of the
 * answer — split out of device.ts (500-line cap) on the seam "reaching the
 * castle" vs "showing what it said". Nothing here touches the DOM, so the
 * probe can be reasoned about (and its timings read) without the chip.
 *
 * The probe is also the mode switch for the whole desk: `/api/status`
 * answering from this page's own origin WITHOUT the studio's marker means a
 * real castle is listening. Opened as a plain file it simply fails, and the
 * desk is a pure simulator.
 */

import { api } from "./api.js";

export interface Status {
  version: string;
  /** Absent on the native-API fallback — render as unknown, never as "no SD"
   *  (dogfood 001: the fallback's missing field displayed as a lie). */
  sd_mounted?: boolean;
  /** KB free on the card — v5.23+; older firmware omits it. */
  sd_free_kb?: number;
  /** The card's size in KB — v5.23+. The SD budget reads it (JB1-11). */
  sd_total_kb?: number;
  volume?: number;
  scene?: string;
  track?: string;
  /** tools/studio.py answers the probe too (so it isn't a console error),
   *  marked with this so we don't mistake the laptop for the castle. */
  studio?: boolean;
  /** On the studio's marker answer: the castle host it is configured to
   *  relay to — present means "a castle is expected and not answering". */
  castle?: string;
  /** Comma-joined scene ids the firmware was BUILT with (v5.42+) — the
   *  desk diffs them against its own list to spot a stale board (C6). */
  scenes?: string;
}

/** Room for the studio to try two addresses (1 s connect each, castle_link
 *  PROBE_CONNECT_S) or one slow answer. A castle that is merely rebooting at
 *  page load is not lost either way — see RETRY_MS. */
export const PROBE_TIMEOUT_MS = 2500;
/** While no castle answers, re-probe this often: a castle that boots after
 *  the page loaded must still get its chip (pass 1, J1-3). */
export const RETRY_MS = 5000;
/** The slow poll once live; actions re-poll sooner via castleAct(). */
export const POLL_MS = 15000;

/** Set when the studio answered FOR a castle it could not reach: the host
 *  it was trying. That is the one no-castle case worth surfacing (C3) — a
 *  page opened from disk, or a studio with no castle configured, is a
 *  simulator on purpose and gets no placeholder. */
let expected: string | null = null;

/** The host the studio says it is relaying to, or null. Only meaningful
 *  after a probe has come back from a studio. */
export const expectedCastle = (): string | null => expected;

export async function probe(): Promise<Status | null> {
  try {
    const r = await api.castleProbe(PROBE_TIMEOUT_MS);
    if (!r.ok) return null;
    const s = (await r.json()) as Status;
    if (s.studio) {
      expected = typeof s.castle === "string" && s.castle ? s.castle : null;
      return null;
    }
    return s;
  } catch {
    return null;
  }
}
