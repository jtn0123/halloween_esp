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

/* Two builds, two ceilings, and the readout shows the arithmetic rather than
   asserting it:

     ~2.9 MB  flash left for ALL scenes after the firmware. This is what the
              flash build (firmware/castle.yaml) has to fit the whole show in.
     1.67 MB  free PSRAM — MEASURED on the real board while playing, not
              estimated (bench_audio, 2026-08-10: 1713 KB free with a scene
              running). This is the cap on ONE track under the SD build.

   There is deliberately no third figure for the 32 GB card, because reaching
   it would mean streaming and streaming does not exist here. This line used to
   claim "streamed from SD: no limit" in green, which was a verified-false
   promise: micro-decoder 0.2.0 offers play_buffer (whole file in RAM) and
   play_url (HTTP) and nothing else, so there is no pull API to stream into.
   See HARDWARE_FINDINGS.md §3b. A readout that advertises a capability the
   stack does not have is worse than one that omits it — you plan around it. */
const PSRAM_FREE = 1713 * 1024, FLASH_FREE = 2.9 * 1024 * 1024;
/** Seconds of PSRAM playback that counts as enough for a whole song. */
const WANTED_SEC = 240;

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
  const psram = PSRAM_FREE / bps;
  const left = Math.max(0, (FLASH_FREE - usedBytes) / bps);
  const fits = psram >= WANTED_SEC;
  return `<b>${kbps} kbps ${ch === 2 ? "stereo" : "mono"}</b> = ${(bps / 1024).toFixed(1)} KB/s &nbsp;·&nbsp; `
    + `<span title="firmware/castle.yaml — every scene shares this">`
    + `flash build, alongside the current show: <b>${mmss(left)}</b></span> &nbsp;·&nbsp; `
    + `<span title="firmware/castle_sd.yaml — the card holds any number of tracks, `
    + `but each one is read whole into PSRAM before it plays">`
    + `SD build, one track: <b>${mmss(psram)}</b> `
    + `<span class="${fits ? "ok" : "no"}">${fits ? "(4 min fits)" : "(under 4 min)"}</span></span>`;
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
  sync();
  return { values, sync };
}
