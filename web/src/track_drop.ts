/**
 * Dropping a file on the Tracks panel, and what the STATIC desk does with
 * it. Split out of tracks.ts (500-line cap) along the seam that was already
 * there: the studio branch of an import is one api call; everything long
 * is the no-server fallback — decode in the browser, find the onsets, hand
 * back a pasteable scene block — and the drop-zone plumbing around both.
 */

import type { BandEditor } from "./band_editor.js";
import { detectOnsets, loudnessEnvelope } from "./onsets.js";
import { sceneYaml } from "./track_scene.js";

/** Drag-drop and click-to-pick on the drop zone; every file goes to `take`. */
export function wireDrop(drop: HTMLElement, take: (f: File) => void): void {
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("click", () => {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "audio/*";
    inp.onchange = () => { const f = inp.files?.[0]; if (f) take(f); };
    inp.click();
  });
  drop.addEventListener("drop", e => {
    const f = e.dataTransfer?.files[0];
    if (f) take(f);
  });
}

export interface LocalAnalysis {
  id: string;
  ext: string;
  /** The pasteable scene block. */
  block: string;
  /** What to tell the user: length, size estimate, onsets per band. */
  summary: string;
}

/** Static mode: no server can convert anything, so analyse right here and
 *  point the scene at the container the file already is. Throws on a file
 *  the browser cannot decode. */
export async function analyseLocally(file: File, wantId: string,
                                     bands: BandEditor | undefined): Promise<LocalAnalysis> {
  const buf = await file.arrayBuffer();
  // Safari still only has the prefixed constructor on some versions, and
  // decoding is the one thing static mode cannot do without.
  const Offline = window.OfflineAudioContext
    || (window as unknown as { webkitOfflineAudioContext: typeof OfflineAudioContext })
         .webkitOfflineAudioContext;
  const ctx = new Offline(1, 44100, 44100);
  const audio = await ctx.decodeAudioData(buf);
  const marks = await detectOnsets(audio);
  const id = (wantId || file.name.replace(/\.[^.]+$/, "")
             ).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32);
  const counts: Record<string, number> =
    Object.fromEntries(Object.entries(marks).map(([k, v]) => [k, v.length]));
  // Static mode cannot convert anything, so the scene has to point at the
  // container the file already is — not at the .mp3 the studio would have
  // made of it.
  const ext = (file.name.match(/\.([a-z0-9]+)$/i)?.[1] || "mp3").toLowerCase();
  const env = loudnessEnvelope(audio.getChannelData(0), audio.sampleRate);
  const block = sceneYaml(id, audio.duration, counts, ext, bands, env);
  const kb = Math.round(audio.duration * 96 * 1000 / 8 / 1024);
  const summary = `${file.name}: ${audio.duration.toFixed(1)}s, ~${kb} KB at 96 kbps. `
    + Object.entries(counts).map(([k, n]) => `${k.replace("onset_", "")} ${n}`).join(" · ")
    + `. Copy the block below, save the file into tracks/${id}.${ext}, then run make audio.`;
  return { id, ext, block, summary };
}
