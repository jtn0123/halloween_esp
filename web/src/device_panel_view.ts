/**
 * What the castle panel SAYS — the whole of its markup, and the shape of the
 * status it reads. Split out of device_panel.ts (500-line cap) on the seam
 * the panel already had inside it: render vs bind. Everything here is pure
 * (status in, HTML string out) with no fetch, no listeners and no state, so
 * the panel proper is now only the class that fetches, mounts and wires.
 *
 * Class names only — the rules live in previewer/panels.css's .dp__* block,
 * so theme and phone CSS reach every row (grade report C2).
 */

import { esc } from "./dom.js";
import { lightsMarkup, sectionHead, speakerMarkup } from "./device_tests.js";

export interface SdFile {
  name: string;
  size: number;
  dir: boolean;
}

/** What a card file is FOR, which is not something its name shouts: the
 *  show's own audio is numbered, the test bench's tones are prefixed, and
 *  everything else is a song somebody imported. */
function kind(name: string): readonly [string, string] {
  if (/^\d\d_/.test(name)) return ["scene", "A rendered scene track"];
  if (/^test_/.test(name)) return ["tone", "A speaker-test tone"];
  return ["song", "An imported track"];
}

/** The card's file list, or a line saying why there isn't one. */
function cardFiles(tracks: SdFile[], mounted: boolean | undefined): string {
  if (!tracks.length) {
    return `<div class="dp__note">` + (mounted
      ? "no tracks on the card yet — drop audio below, or press → Castle on a " +
        "track in the Library"
      : "no SD card") + `</div>`;
  }
  return tracks.map((f, i) => {
    const [tag, why] = kind(f.name);
    return `<div class="dp__file">` +
      `<button data-play="${i}" class="dp__btn dp__btn--sm" title="Play on the castle">▶</button>` +
      `<span class="dp__file-nm" title="${esc(f.name)}">${esc(f.name)}</span>` +
      `<small class="dp__badge dp__badge--${tag}" title="${why}">${tag}</small>` +
      `<small class="dp__muted">${kb(f.size)}</small>` +
      `<button data-del="${i}" class="dp__del" title="Delete from the card">✕</button>` +
      `</div>`;
  }).join("");
}

/** The show's own tracks live in /sd/scenes/, which /api/files does not list
 *  (it reads the root only). The firmware's manifest check does know, and
 *  reports the gap in /api/status — so say what it says rather than nothing. */
function sceneTracks(st: DeviceStatus): string {
  if (!st.sd_mounted) return "";
  const n = sceneIds().length;
  const missing = (st.missing ?? "").trim();
  return missing
    ? `<div class="dp__note dp__note--warn" title="The scene will fall back to ` +
      `the chirp. Push them with tools/sd_sync.py &lt;ip&gt; scenes">` +
      `⚠ scenes/ is missing ${esc(missing)}</div>`
    : `<div class="dp__note dp__note--tight" title="The rendered show tracks the ` +
      `scene engine streams; pushed by tools/sd_sync.py &lt;ip&gt; scenes">` +
      `scenes/ — all ${n} show track${n === 1 ? "" : "s"} present</div>`;
}

export interface DeviceStatus {
  version: string;
  uptime_s: number;
  sd_mounted: boolean;
  psram_free_kb: number;
  /** Internal heap — the pool the decoder and the web servers actually run
   *  in, and the one that gets tight while a song plays. */
  heap_free_kb?: number;
  /** KB free on the card — v5.23+. */
  sd_free_kb?: number;
  /** Card size, for the "how full is it" bar. */
  sd_total_kb?: number;
  /** Scene tracks the manifest check could not find in /sd/scenes/. */
  missing?: string;
  /** 0–100, mirrored from the media player; older firmware omits it. */
  volume?: number;
  /** Motion-sensor config, mirrored from the pir_* entities. */
  pir?: { armed: boolean; cooldown_s: number; scene: string };
  /** Is the evening playlist running; older firmware omits it. */
  show_on?: boolean;
  /** Current scene id, "" when idle. */
  scene?: string;
  /** Comma-joined scene ids this firmware was BUILT with (v5.42+). */
  scenes?: string;
  /** The studio answering FOR a castle it cannot reach — not a castle. */
  studio?: boolean;
  /** Set by the studio's relay: this status came through the bridge, and
   *  the desk's merged Library is on the same page. */
  bridged?: string;
}

/** Scene ids for the PIR select — read from the page's own generated data,
 *  so a new scene shows up here without touching this file. */
const sceneIds = (): string[] => {
  const gen = (window as unknown as { CASTLE_GEN?: { scenes?: { id: string }[] } })
    .CASTLE_GEN;
  return (gen?.scenes ?? []).map((s) => s.id);
};

const fmtUptime = (s: number): string =>
  s < 3600 ? `${(s / 60) | 0}m` : `${(s / 3600) | 0}h ${((s % 3600) / 60) | 0}m`;

const kb = (bytes: number): string => `${(bytes / 1024) | 0} KB`;

/** The health line: three numbers, each with the sentence that says what it
 *  means when it falls. Heap is here because a scene start is where it goes
 *  (docs/ISSUE-scene-start-audio.md) and nothing on the desk showed it. */
function healthMeta(st: DeviceStatus): string {
  const bit = (v: string, why: string): string =>
    `<span class="dp__stat" title="${why}">${v}</span>`;
  const used = st.sd_total_kb && st.sd_free_kb
    ? 1 - st.sd_free_kb / st.sd_total_kb : 0;
  return `<div class="dp__meta">` +
    (st.sd_free_kb
      ? bit(`card ${(st.sd_free_kb / 1048576).toFixed(1)} GB`,
            "Room left on the microSD card") +
        `<span class="dp__bar" aria-hidden="true">` +
        `<i style="width:${Math.max(2, Math.round(used * 100))}%"></i></span>`
      : "") +
    bit(`psram ${(st.psram_free_kb / 1024).toFixed(1)} MB`,
        "Free PSRAM — the buffer the SD turntable streams through") +
    (st.heap_free_kb
      ? bit(`heap ${st.heap_free_kb} KB`,
            "Free internal RAM — what the decoder and the web servers run in. " +
            "It drops while a song plays; under ~20 KB things start failing")
      : "") + `</div>`;
}

/** C6: the scenes this desk knows that the BOARD's firmware does not —
 *  the drift behind "unknown scene", said before a button press finds it.
 *  Empty until the firmware reports its build list (v5.42+). */
function firmwareDrift(st: DeviceStatus): string {
  if (st.scenes === undefined) return "";
  const known = new Set(st.scenes.split(",").filter(Boolean));
  const newer = sceneIds().filter((id) => !known.has(id));
  if (!newer.length) return "";
  const n = newer.length;
  return `<div class="dp__note dp__note--warn" title="The board's firmware was ` +
    `built before ${n === 1 ? "this scene" : "these scenes"} existed; picking ` +
    `${n === 1 ? "it" : "one"} answers 'unknown scene'. make sd-build, stop ` +
    `audio, then OTA.">⚠ ${n} scene${n === 1 ? "" : "s"} newer than the ` +
    `firmware (${esc(newer.join(", "))}) — rebuild and OTA</div>`;
}

/** The panel's whole body for one poll's worth of truth. `tracks` is the
 *  card's playable root files; `onCard` is their names, which the speaker
 *  bench needs to know whether its tones are already pushed. */
export function panelMarkup(
  st: DeviceStatus, tracks: SdFile[], onCard: Set<string>,
): string {
  // Class names only — the rules are previewer/panels.css's .dp__* block,
  // so theme and phone CSS reach every row (grade report C2).
  return (
    `<div class="dp__hd">` +
    `<span class="dp__grow"><b>🏰 v${esc(st.version)}</b>` +
    `<small class="dp__muted"> · up ${fmtUptime(st.uptime_s)}</small></span>` +
    `<button id="dpClose" class="dp__x" title="Close this panel" aria-label="Close">✕</button>` +
    `</div>` +
    healthMeta(st) +
    firmwareDrift(st) +

    `<div class="dp__sec">` +
    `<div class="dp__row">` +
    `<button id="dpPlaylist" class="dp__go${st.show_on ? " dp__go--on" : ""}" ` +
    `title="Every scene in order with dark gaps, ` +
    `looping until stopped — the whole evening on one button">` +
    `${st.show_on ? "■ stop the show" : "▶ start the show"}</button>` +
    (st.show_on && st.scene
      ? ` <small class="dp__muted">now: ${esc(st.scene)}</small>` : "") +
    // The castle serves a four-button page of its own (firmware/
    // sd_web_remote.h) — the thing to hand a phone on the porch. Nothing
    // linked to it (JB1-8); now the panel does.
    `<a id="dpRemote" class="dp__link" href="/remote" target="_blank" rel="noopener" ` +
    `title="The castle's own phone page: ambient, scare, stop and the evening ` +
    `show on four thumb-sized buttons. Opens in a new tab">📱 phone remote</a>` +
    `</div>` +
    `<div class="dp__row dp__row--tight" ` +
    `title="Park every pixel on one colour, or give them back to the show">` +
    `<small class="dp__muted">lights</small>` +
    `<input id="dpColor" class="dp__color" type="color" value="#ff8c1e" ` +
    `title="Park the pixels on a colour">` +
    `<button id="dpShow" class="dp__btn" title="Hand the pixels back to the scene engine">` +
    `resume show</button>` +
    `<button id="dpOff" class="dp__ghost">off</button>` +
    `</div></div>` +

    lightsMarkup() +
    speakerMarkup(onCard) +

    `<div class="dp__sec">` +
    sectionHead("💿", "on the card",
                `${tracks.length} track${tracks.length === 1 ? "" : "s"} in the root`) +
    sceneTracks(st) +
    `<div class="dp__files" id="dpFiles">` + cardFiles(tracks, st.sd_mounted) +
    `</div>` +
    // Through the studio the Library below can also DOWNLOAD a card file
    // and push one back; this list plays and deletes, which is what you
    // want while standing at the castle.
    (st.bridged
      ? `<div class="dp__note dp__note--tight">the Library below can also ` +
        `download these and push new ones</div>` : "") +
    `<div id="dpDrop" class="dp__drop">drop audio files here to upload</div>` +
    `</div>` +

    `<div class="dp__sec">` +
    sectionHead("👣", "motion sensor", "who it wakes for, and how often") +
    `<div class="dp__row dp__row--tight">` +
    `<label><input type="checkbox" id="dpPirArm" ${st.pir?.armed ? "checked" : ""}> armed</label> ` +
    `<select id="dpPirScene" title="Which scene the motion sensor plays">` +
    sceneIds().map((s) =>
      `<option${s === st.pir?.scene ? " selected" : ""}>${s}</option>`).join("") +
    `</select> ` +
    `<input id="dpPirCool" class="dp__cool" type="number" min="5" max="600" step="5" ` +
    `value="${st.pir?.cooldown_s ?? 60}" ` +
    `title="Cooldown: seconds before the sensor can fire again">` +
    `<small class="dp__muted">s between triggers</small>` +
    `</div></div>` +

    `<div class="dp__foot">` +
    `<button id="dpLog" class="dp__logbtn">boot log ▸</button>` +
    `<pre id="dpLogOut" class="dp__log" hidden></pre>` +
    `</div>`
  );
}
