/**
 * The import options row — start, length, format, bitrate, sensitivity.
 *
 * Split out of tracks.ts at the 500-line cap, along the seam that was already
 * there: none of this knows about the server, the track list or the clip
 * editor. It reads seven inputs and writes two readouts.
 *
 * The arithmetic and the formatting are exported as plain functions so the
 * tests can call the shipped code rather than a copy of it that has to be kept
 * in step by hand — which is what test/tracks_logic.mjs was reduced to while
 * all of this lived inside a DOM-bound closure.
 */

import type { TrackInfo } from "./tracks.js";

/**
 * The option row as it goes out to the server. Deliberately strings, not
 * numbers: blank means "leave it as it was", and only a string can carry that
 * distinction — `+""` would arrive as a very definite zero.
 */
export interface ImportOpts {
  id: string;
  start: string;
  take: string;
  sensitivity: string;
  bitrate: string;
  sample_rate: string;
  channels: string;
  /** Container: mp3 | wav | flac | opus. Blank keeps whatever was used last. */
  format: string;
  normalize: boolean;
  /** Seconds of ramp at the head and tail. Blank means none. */
  fade_in: string;
  fade_out: string;
}

/** Valid MPEG-1 Layer III range. Outside it the encoder has nothing to do
 *  with the number, so neither should the readout. */
export const MIN_KBPS = 32, MAX_KBPS = 320;

/** Containers with no bitrate to set. */
export const LOSSLESS = ["wav", "flac"];

/**
 * A bitrate the encoder would actually accept.
 *
 * `+raw || 96` is the obvious version and it is wrong: -5 is truthy, so a
 * negative went straight through and the capacity readout rendered it as
 * "-47:-47". Everything out of range lands on a value ffmpeg would take.
 */
export const clampKbps = (raw: string): number => {
  const t = +raw;
  return Number.isFinite(t) && t > 0
    ? Math.min(MAX_KBPS, Math.max(MIN_KBPS, t))
    : 96;
};

/** Stereo only when it says exactly 2; everything else is mono. */
export const channelsOf = (raw: string): 1 | 2 => (+raw === 2 ? 2 : 1);

export const mmss = (s: number): string =>
  `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/* One real ceiling left, and the readout shows the arithmetic:

     ~2.9 MB  flash left for ALL scenes after the firmware. This is what the
              flash build (firmware/castle_flash.yaml) has to fit the show in.

   The SD build has no per-track cap anymore. This line spent one evening
   claiming "streamed from SD: no limit" when streaming did not exist, and the
   next claiming "under 4 min" after the loopback stream removed the PSRAM
   ceiling — wrong in both directions, each verified against the device. The
   truth today: /api/play and the scene scripts hand the media pipeline a
   http://127.0.0.1/sd/... URL (firmware/sd_web_site.h) and it streams; length
   is bounded by the card. */
const FLASH_FREE = 2.9 * 1024 * 1024;

/**
 * The capacity line, as HTML.
 *
 * @param usedBytes what the scenes already in the show cost in flash.
 */
export function capacityHtml(bitrate: string, channels: string,
                             usedBytes: number): string {
  const kbps = clampKbps(bitrate);
  const ch = channelsOf(channels);
  const bps = kbps * 1000 / 8;          // the bitrate already covers channels
  const left = Math.max(0, (FLASH_FREE - usedBytes) / bps);
  return `<b>${kbps} kbps ${ch === 2 ? "stereo" : "mono"}</b> = ${(bps / 1024).toFixed(1)} KB/s &nbsp;·&nbsp; `
    + `<span title="firmware/castle_flash.yaml — every scene shares this">`
    + `flash build, alongside the current show: <b>${mmss(left)}</b></span> &nbsp;·&nbsp; `
    + `<span title="firmware/castle_sd.yaml — tracks stream off the card through `
    + `the device's own web server, so length is bounded by the 32 GB card, `
    + `not by memory">`
    + `SD build: <span class="ok">any length (streams)</span></span>`;
}

/**
 * The one-line summary on the collapsed Options panel.
 *
 * A collapsed panel must still say what it is about to do — otherwise
 * "Options" is a box you have to open to find out whether you care.
 */
export function optsHint(o: ImportOpts): string {
  const bits: string[] = [];
  if (o.take) {
    bits.push(`${o.take}s${o.start && o.start !== "0:00" ? ` from ${o.start}` : ""}`);
  }
  const fmt = (o.format || "mp3").toLowerCase();
  bits.push(LOSSLESS.includes(fmt)
    ? fmt.toUpperCase()
    : `${fmt.toUpperCase()} ${clampKbps(o.bitrate)}k`);
  if (channelsOf(o.channels) === 2) bits.push("stereo");
  if (o.normalize) bits.push("loudness matched");
  // Fades change what you hear at the seam of a loop, so they belong in a
  // summary whose job is to say what the import will do.
  const fi = +o.fade_in, fo = +o.fade_out;
  if (fi > 0 || fo > 0) {
    bits.push(`fade ${fi > 0 ? `${fi}s in` : ""}${fi > 0 && fo > 0 ? "/" : ""}`
            + `${fo > 0 ? `${fo}s out` : ""}`);
  }
  return bits.length ? `— ${bits.join(", ")}` : "";
}

export interface OptsForm {
  /** Everything the inputs currently say. */
  values: () => ImportOpts;
  /** Redraw the hint and the capacity line — after a programmatic change. */
  sync: () => void;
}

/** Ids of every control that should re-render the readouts when touched. */
const WATCHED = ["trkStart", "trkTake", "trkBitrate", "trkFormat",
                 "trkCh", "trkRate", "trkNorm", "trkFadeIn", "trkFadeOut"];

/**
 * Bind the form.
 *
 * @param flashUsed bytes the current show already occupies, for the readout.
 */
export function initImportOpts(flashUsed: () => number): OptsForm {
  const el = (id: string): HTMLInputElement | null =>
    document.getElementById(id) as HTMLInputElement | null;
  const val = (id: string): string => el(id)?.value.trim() ?? "";

  const values = (): ImportOpts => ({
    id: val("trkId"), start: val("trkStart"), take: val("trkTake"),
    sensitivity: val("trkSens"), bitrate: val("trkBitrate"),
    sample_rate: val("trkRate"), channels: val("trkCh"),
    format: val("trkFormat"),
    normalize: !!el("trkNorm")?.checked,
    fade_in: val("trkFadeIn"), fade_out: val("trkFadeOut"),
  });

  function sync(): void {
    const o = values();
    const cap = document.getElementById("trkCap");
    if (cap) cap.innerHTML = capacityHtml(o.bitrate, o.channels, flashUsed());
    const hint = document.getElementById("trkOptsHint");
    if (hint) hint.textContent = optsHint(o);
    // Bitrate is meaningless for the lossless containers; grey it out rather
    // than letting someone set a number that gets silently discarded.
    const br = el("trkBitrate");
    if (br) {
      const fmt = (o.format || "mp3").toLowerCase();
      br.disabled = LOSSLESS.includes(fmt);
      br.title = br.disabled ? `${fmt.toUpperCase()} has no bitrate to set` : "";
    }
  }

  for (const id of WATCHED) {
    for (const ev of ["input", "change"]) {
      document.getElementById(id)?.addEventListener(ev, sync);
    }
  }
  // A human keystroke in either trim box makes the values theirs. isTrusted
  // is what separates it from the clip editor's synthetic writes.
  for (const t of TRIM_IDS) {
    document.getElementById(t)?.addEventListener("input", e => {
      if (e.isTrusted) setTrimOwner(null);
    });
  }
  sync();
  return { values, sync };
}

/**
 * Point the Options panel at one track's remembered settings.
 *
 * The panel is one global form, and it used to keep whatever the last
 * import typed into it — so "Re-import for stereo" could silently apply a
 * leftover name, trim or bitrate from a different song (round-1 user test
 * walked straight into it). Now what the panel shows when a track is open
 * is what that track was made with. The synthetic input events are
 * isTrusted:false, so the clip editor ignores them (adoptTyped).
 *
 * start/take are NOT filled: the clip editor owns those, and it writes
 * them when the analysis lands.
 */
export function fillOptsFrom(t: TrackInfo): void {
  const o = t.opts || {};
  const set = (id: string, v: string | undefined | null): void => {
    const el = document.getElementById(id) as HTMLInputElement | null;
    if (!el || v === undefined || v === null || el.value === String(v)) return;
    el.value = String(v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  };
  set("trkId", "");                       // a name is for NEW imports only
  set("trkBitrate", o.bitrate != null ? String(o.bitrate) : "");
  // The 44100 option's value is deliberately "" (the default); writing the
  // literal "44100" matched nothing and left the select BLANK (round 3).
  set("trkRate", o.sample_rate === 22050 ? "22050" : "");
  set("trkCh", String(o.channels ?? 1));
  set("trkFormat", (t.ext || o.format || "mp3").toLowerCase());
  set("trkSens", o.sensitivity != null ? String(o.sensitivity) : "1.1");
  set("trkFadeIn", o.fade_in != null ? String(o.fade_in) : "");
  set("trkFadeOut", o.fade_out != null ? String(o.fade_out) : "");
  const norm = document.getElementById("trkNorm") as HTMLInputElement | null;
  if (norm && norm.checked !== !!o.normalize) {
    norm.checked = !!o.normalize;
    norm.dispatchEvent(new Event("change", { bubbles: true }));
  }
  // A <select> handed a value it has no option for lands on nothing and
  // reads as "unset" (rounds 2–3). Snap those back to their first option.
  // trkRate is exempt: its default option's value IS "".
  for (const [id, dflt] of [["trkCh", "2"], ["trkFormat", "mp3"]] as const) {
    const sel = document.getElementById(id) as HTMLSelectElement | null;
    if (sel && sel.value === "") set(id, dflt);
  }
}

/* ── Who wrote START/LENGTH ─────────────────────────────────────────────
   The two trim inputs are shared by every import path AND the clip editor,
   which writes them as you drag. Without a record of who wrote them, a drop
   of song B inherited song A's trim (judge B, JB1-1: a 358-byte file and a
   traceback). So the editor stamps the inputs with the track they describe,
   a human keystroke clears the stamp, and an import only ever takes values
   a human typed. */

const TRIM_IDS = ["trkStart", "trkTake"] as const;
const trimEl = (id: string): HTMLInputElement | null =>
  document.getElementById(id) as HTMLInputElement | null;

/** The track id the clip editor wrote the trim for, or null: typed/blank. */
export const trimOwner = (): string | null =>
  trimEl("trkStart")?.dataset["owner"] || null;

/** Stamp (or unstamp) the trim inputs as the editor's. */
export function setTrimOwner(id: string | null): void {
  for (const t of TRIM_IDS) {
    const el = trimEl(t);
    if (!el) continue;
    if (id) el.dataset["owner"] = id; else delete el.dataset["owner"];
  }
}

/** Blank START/LENGTH and drop the stamp — on row change and after an
 *  import has consumed them. Synthetic events, so the editor ignores it. */
export function clearTrim(): void {
  for (const t of TRIM_IDS) {
    const el = trimEl(t);
    if (!el) continue;
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  setTrimOwner(null);
}

/** The option row as a NEW import may use it: the editor's trim is another
 *  track's business and is dropped; a typed one is kept. */
export function forImport(o: ImportOpts): ImportOpts {
  return trimOwner() ? { ...o, start: "", take: "" } : o;
}
