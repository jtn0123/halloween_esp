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

/** Filenames on the castle's SD card, or null when no castle answers —
 *  absence of a castle must not read as "not on the card". */
export async function fetchCardSet(): Promise<Set<string> | null> {
  try {
    const r = await fetch("/api/files");
    if (!r.ok) throw new Error(String(r.status));
    const files = (await r.json()) as { name: string; dir: boolean }[];
    return new Set(files.filter(f => !f.dir).map(f => f.name));
  } catch {
    return null;
  }
}

/** Copy one track's bytes onto the card. Returns the toastable outcome. */
export async function sendToCastle(t: TrackInfo):
    Promise<{ ok: boolean; msg: string }> {
  try {
    const blob = await (await fetch(`/api/track/${encodeURIComponent(t.id)}`)).blob();
    const r = await fetch(`/api/files/${encodeURIComponent(cardName(t))}`,
                          { method: "PUT", body: blob });
    return r.ok
      ? { ok: true, msg: `\u201c${t.id}\u201d is on the castle's card.` }
      : { ok: false, msg: `Send failed (${r.status}) \u2014 is the castle awake?` };
  } catch {
    return { ok: false, msg: "Send failed \u2014 no castle answering." };
  }
}
