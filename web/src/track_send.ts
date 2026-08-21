/**
 * The castle side of the track library: what is on the SD card, and how a
 * local file gets there. Split from tracks.ts (500-line cap) at the seam
 * "this repo's library" vs "the card's copy of it".
 *
 * Everything here goes through the page's own origin — the studio relays
 * castle-shaped /api/* calls (tools/castle_link.py), and served from the
 * castle itself the same paths are simply local.
 */

// Type-only, so the tracks ↔ track_send cycle stays erased at build time.
import type { TrackInfo } from "./tracks.js";

/** The filename a track lands under on the card. */
export function cardName(t: TrackInfo): string {
  return `${t.id}.${(t.ext || t.opts?.format || "mp3").toLowerCase()}`;
}

/** Filename → size in bytes for everything on the castle's SD card, or null
 *  when no castle answers — absence of a castle must not read as "not on
 *  the card". */
export async function fetchCard(): Promise<Map<string, number> | null> {
  try {
    const r = await fetch("/api/files");
    if (!r.ok) throw new Error(String(r.status));
    const files = (await r.json()) as { name: string; size: number; dir: boolean }[];
    return new Map(files.filter(f => !f.dir).map(f => [f.name, f.size]));
  } catch {
    return null;
  }
}

/** PUT one blob to the card, reporting upload progress 0–100. XHR rather
 *  than fetch: a WiFi send to an ESP32 takes long enough that a frozen
 *  button reads as a dead one, and fetch cannot see upload progress. */
function putWithProgress(name: string, blob: Blob,
                         onProgress?: (pct: number) => void): Promise<boolean> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/files/${encodeURIComponent(name)}`);
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress?.(Math.round(100 * e.loaded / e.total));
    });
    xhr.addEventListener("load", () => resolve(xhr.status >= 200 && xhr.status < 300));
    xhr.addEventListener("error", () => resolve(false));
    xhr.send(blob);
  });
}

/** Copy one track's bytes onto the card. Returns the toastable outcome. */
export async function sendToCastle(t: TrackInfo,
                                   onProgress?: (pct: number) => void):
    Promise<{ ok: boolean; msg: string }> {
  try {
    const r = await fetch(`/api/track/${encodeURIComponent(t.id)}`);
    if (!r.ok) throw new Error(String(r.status));
    const ok = await putWithProgress(cardName(t), await r.blob(), onProgress);
    return ok
      ? { ok: true, msg: `“${t.id}” is on the castle's card.` }
      : { ok: false, msg: `Send of “${t.id}” failed — is the castle awake?` };
  } catch {
    return { ok: false, msg: "Send failed — no castle answering." };
  }
}
