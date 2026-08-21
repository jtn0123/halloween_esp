/**
 * One track row, as HTML.
 *
 * Split from tracks.ts for the 500-line cap; the seam is "how a row LOOKS"
 * vs "what clicking it DOES" (tracks.ts keeps every handler). Pure string
 * building — everything this needs arrives in the context argument.
 */

import { BAND_HELP, bandSummary } from "./bands.js";
import { sendable } from "./track_send.js";
import type { TrackInfo } from "./tracks.js";

const ESCAPES: Record<string, string> =
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
export const esc = (s: unknown): string =>
  String(s).replace(/[&<>"]/g, c => ESCAPES[c] as string);

export interface RowCtx {
  selected: boolean;
  inShow: boolean;
  sounding: boolean;
  /** The card's copy vs this file: current (same bytes), stale (differs —
   *  re-imported since it was sent), absent; null = no castle answering,
   *  so the row stays quiet rather than claiming "not sent". */
  onCastle: "current" | "stale" | "absent" | null;
}

export function trackRowHtml(t: TrackInfo, ctx: RowCtx): string {
  const o = t.opts || {};
  const ext = (t.ext || o.format || "mp3").toLowerCase();
  // Bitrate is a property of the lossy encoders only; printing "?kbps"
  // next to a WAV says the import went wrong when it went fine.
  const lossless = ext === "wav" || ext === "flac";
  // Mono is not just a fact — it costs the show its left/right tower
  // ping-pong (pan routing needs two channels). Say so where the user is
  // looking, with the fix spelled out. Clicking the badge stages the fix
  // (round 2: a user pressed Re-import bare and watched 14 s change
  // nothing). In-show mono shouts louder: that is the track the audience
  // will actually hear.
  const mono = o.channels !== 2;
  const monoInShow = mono && ctx.inShow;
  const monoBadge = mono
    ? `<span class="trk__mono" style="color:${monoInShow
        ? "var(--err,#ff6b5a)" : "var(--warn,#e0a34a)"};cursor:pointer;`
      + `text-decoration:underline dotted" title="Mono import: the towers `
      + `cannot answer left/right without stereo.${monoInShow
        ? " THIS TRACK IS IN THE SHOW — the audience hears it mono." : ""} `
      + `Click here to set stereo, then press Re-import.">`
      + `mono ⚠${monoInShow ? " in the show" : ""}</span>`
    : "stereo";
  const fmt = [ext.toUpperCase(),
               lossless ? null : `${o.bitrate || "?"}kbps`,
               monoBadge,
               `${(o.sample_rate || 44100) / 1000}k`,
               o.normalize ? "normalised" : null].filter(Boolean).join(" · ");
  // Rate per zone, not raw counts — see bandSummary for why the counts are
  // the wrong number to put in front of someone.
  const onsets = bandSummary(t.onsets || {}, t.dur);
  // The remembered source. A link if it came from one, so you can go back
  // to where it came from without digging through history.
  const isUrl = /^https?:\/\//.test(t.source || "");
  const src = !t.source ? ""
    : isUrl
      ? `<a href="${esc(t.source)}" target="_blank" rel="noreferrer noopener">${esc(t.title || t.source).slice(0, 64)}</a>`
      : esc(t.source.replace(/^file:/, "").split("/").pop());
  const cls = ["trk", ctx.selected ? "sel" : "",
               ctx.sounding ? "playing" : ""].filter(Boolean).join(" ");
  // A track whose import failed has no bytes worth anything: no castle
  // button (it would PUT nothing over the card's good copy), and a badge
  // that says what happened instead of "stale" (pass 1, J1-2).
  const broken = !sendable(t);
  return `
  <div class="${cls}" data-id="${esc(t.id)}" title="Click to open the clip editor">
    <div class="trk__nm">${esc(t.id)}
      ${ctx.inShow ? `<span class="trk__badge" title="This track already has a scene in scenes.yaml">in the show</span>` : ""}
      ${ctx.onCastle === "current" ? `<span class="trk__badge" title="The castle's SD card holds this exact file — byte count verified">on castle ✓</span>` : ""}
      ${ctx.onCastle === "stale" ? `<span class="trk__badge" style="color:var(--warn,#e0a34a)" title="The card's copy is DIFFERENT bytes — this track changed since it was sent. Press Update castle.">stale on castle ⚠</span>` : ""}
      ${broken ? `<span class="trk__badge trk__broken" style="color:var(--err,#ff6b5a)" title="${esc(t.error || "The file is empty")} — re-import it (or delete it). It cannot be sent to the castle.">import failed ⚠</span>` : ""}
      <small>${t.dur ?? "?"}s · ${t.kb} KB · ${fmt}</small>
      <small title="${esc(BAND_HELP)}">${esc(onsets)}</small>
      ${src ? `<small class="trk__src">from ${src}</small>` : ""}
      ${t.notes ? `<small>${esc(t.notes)}</small>` : ""}
    </div>
    <div class="trk__act">
      <button data-act="play" class="${ctx.sounding ? "on" : ""}"
              title="Listen to the whole file — audio only, no lights. To see the light show, click the row and press Audition in the editor.">${ctx.sounding ? "Stop" : "Play"}</button>
      <button data-act="scene" title="${ctx.inShow ? "Rewrite this scene from the track as it is now" : "Add this track to the show as a new scene"}"
        >${ctx.inShow ? "Update scene" : "Make scene"}</button>
      ${t.source ? `<button data-act="refresh" title="Rebuild from the remembered source using the options above">Re-import</button>` : ""}
      ${ctx.onCastle !== null && !broken ? `<button data-act="send" title="Copy this file onto the castle's SD card over WiFi — it can then play on the castle with zero lag">${
        ctx.onCastle === "stale" ? "Update castle"
        : ctx.onCastle === "current" ? "Re-send" : "→ Castle"}</button>` : ""}
      <button data-act="del" class="danger">Delete</button>
    </div>
  </div>`;
}
