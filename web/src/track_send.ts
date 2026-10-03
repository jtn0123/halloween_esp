/**
 * The castle side of the track library: what is on the SD card, and how a
 * local file gets there. Split from tracks.ts (500-line cap) at the seam
 * "this repo's library" vs "the card's copy of it".
 *
 * Everything here goes through the page's own origin — the studio relays
 * castle-shaped /api/* calls (tools/castle_link.py), and served from the
 * castle itself the same paths are simply local.
 */

import { api } from "./api.js";
import { KEY_REQUIRED } from "./castle_act.js";
import { castleBusy } from "./castle_bus.js";
import type { TrackInfo } from "./types.js";

/** Can this track go to the card at all? A failed import leaves a row with
 *  `error` set and no bytes; sending THAT would overwrite the card's good
 *  copy with nothing (pass 1, J1-2). */
export const sendable = (t: TrackInfo): boolean =>
  !t.error && (t.bytes === undefined || t.bytes > 0);

/** The filename a track lands under on the card. */
export function cardName(t: TrackInfo): string {
  return `${t.id}.${(t.ext || t.opts?.format || "mp3").toLowerCase()}`;
}

/** Filename → size in bytes for everything on the castle's SD card, or null
 *  when no castle answers — absence of a castle must not read as "not on
 *  the card". */
export async function fetchCard(): Promise<Map<string, number> | null> {
  try {
    const files = await api.castleFiles();
    return new Map(files.filter(f => !f.dir).map(f => [f.name, f.size]));
  } catch {
    return null;
  }
}

/** Is the card's copy of `t` the same bytes as the local file?
 *  "current" | "stale" | "absent"; null when no castle answers. Exact-size
 *  compare — an older studio without `bytes` degrades to presence-only. */
export type CardState = "current" | "stale" | "absent";
export function cardState(t: TrackInfo,
                          card: ReadonlyMap<string, number> | null): CardState | null {
  if (card === null) return null;
  if (!sendable(t)) return null;     // nothing to compare against
  const size = card.get(cardName(t));
  if (size === undefined) return "absent";
  if (t.bytes === undefined) return "current";
  return size === t.bytes ? "current" : "stale";
}

/** What a send needs to know before a byte moves: KB free on the card
 *  (null when the castle, or an older firmware without the field, can't
 *  say), and whether a keyed castle would refuse it. A castle answers a
 *  refused PUT only after the WHOLE body has arrived (the IDF server drains
 *  it), so a send to a castle that will say 401 is asked first: POST
 *  /api/key with nothing to change is 400 to the key the studio sends and
 *  401 to a wrong or missing one, and changes nothing either way. */
async function preflight(): Promise<{ free: number | null; refused: boolean }> {
  try {
    const s = await api.castleStatus();
    const free = typeof s.sd_free_kb === "number" && s.sd_free_kb > 0
      ? s.sd_free_kb : null;
    const refused = s.locked === true
      && (await api.castleAction("/api/key", "POST")).status === 401;
    return { free, refused };
  } catch {
    return { free: null, refused: false };
  }
}

/** PUT one blob to the card, reporting upload progress 0–100. XHR rather
 *  than fetch: a WiFi send to an ESP32 takes long enough that a frozen
 *  button reads as a dead one, and fetch cannot see upload progress.
 *  Resolves to the byte count the CASTLE says it wrote (-1 on failure,
 *  -2 unconfirmed, -3 refused for want of the castle key) — the caller
 *  compares it to what was sent, so ✓ means verified. */
function putWithProgress(name: string, blob: Blob,
                         onProgress?: (pct: number) => void): Promise<number> {
  return castleBusy(new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/files/${encodeURIComponent(name)}`);
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress?.(Math.round(100 * e.loaded / e.total));
    });
    xhr.addEventListener("load", () => {
      // 504: the castle took the bytes but never acked (castle_link) — the
      // file MAY be there; -2 lets the caller say so instead of "failed".
      if (xhr.status === 504) return resolve(-2);
      // 401: a keyed castle (v5.74) the studio holds no right key for.
      if (xhr.status === 401) return resolve(-3);
      if (xhr.status < 200 || xhr.status >= 300) return resolve(-1);
      try {
        resolve((JSON.parse(xhr.responseText) as { bytes?: number }).bytes ?? -1);
      } catch {
        resolve(-1);
      }
    });
    xhr.addEventListener("error", () => resolve(-1));
    xhr.send(blob);
  }));
}

/** Copy one track's bytes onto the card and VERIFY the castle got them all.
 *  Returns the toastable outcome. */
export async function sendToCastle(t: TrackInfo,
                                   onProgress?: (pct: number) => void):
    Promise<{ ok: boolean; msg: string }> {
  if (!sendable(t)) {
    return { ok: false, msg: `“${t.id}” has no usable audio (its import `
      + `failed) — nothing to send. Re-import it first.` };
  }
  try {
    const blob = await api.trackBytes(t.id);
    if (blob.size === 0) {
      return { ok: false, msg: `“${t.id}” is an empty file — nothing to send.` };
    }
    // Fail BEFORE the upload when the card can't hold it — a send that dies
    // at 99% after a minute of WiFi is the worst version of "no" — or when
    // the castle's key would refuse it at the end.
    const { free, refused } = await preflight();
    if (refused) return { ok: false, msg: `Send of “${t.id}” refused — ${KEY_REQUIRED}` };
    if (free !== null && blob.size / 1024 > free) {
      return { ok: false, msg: `No room on the card for “${t.id}” — it needs `
        + `${Math.round(blob.size / 1024)} KB and the card has ${free} KB free.` };
    }
    const wrote = await putWithProgress(cardName(t), blob, onProgress);
    if (wrote === blob.size) {
      return { ok: true, msg: `“${t.id}” is on the castle's card — `
        + `${Math.round(blob.size / 1024)} KB, verified.` };
    }
    if (wrote >= 0) {
      return { ok: false, msg: `Send of “${t.id}” landed short — the castle wrote `
          + `${wrote} of ${blob.size} bytes. Send it again.` };
    }
    if (wrote === -3) return { ok: false, msg: `Send of “${t.id}” refused — ${KEY_REQUIRED}` };
    return wrote === -2
      ? { ok: false, msg: `The castle took “${t.id}” but did not confirm in time — `
          + `it may well be on the card. The list below will say; do not `
          + `re-send until it does.` }
      : { ok: false, msg: `Send of “${t.id}” failed — is the castle awake?` };
  } catch {
    return { ok: false, msg: "Send failed — no castle answering." };
  }
}
