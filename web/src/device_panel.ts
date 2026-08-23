/**
 * The castle panel — extra chrome the cue desk grows when it is served by the
 * device itself (device.ts's probe decides; this file never runs on a laptop
 * build unless something answers /api/status).
 *
 * Everything here talks to firmware/sd_web.h and is deliberately thin: the
 * desk is the same single file in both worlds, so every feature added here
 * ships to the Mac build too and simply stays dormant. Keep it that way —
 * device-only behaviour behind the probe, shared behaviour in the desk proper.
 *
 * What it offers, in the order the panel shows it:
 *   - health: firmware version, uptime, and the three numbers that go wrong
 *     (card room, PSRAM — the SD turntable's budget — and internal heap,
 *     which is what a decoder actually starves on)
 *   - the show: the evening playlist, the phone remote, the light override
 *   - the test bench: strip test and speaker test (device_tests.ts)
 *   - the card: every track on it, with play and delete, plus whether the
 *     show's own nine scene tracks are all present
 *   - the motion sensor, the drop zone, and the boot log
 * Volume lives on the chip (device.ts) alone — the same slider twice was
 * the scatter the dogfood pass called out, and two sliders drift.
 *
 * The intended way to use it with the stage: leave the desk's own audio muted
 * (it is by default), press play here — the castle makes the sound, the canvas
 * above renders the full 21-pixel show the finished rig would do, and the one
 * soldered pixel plays its part in the corner of the room.
 */

import { cardChanged } from "./castle_bus.js";
import { esc } from "./dom.js";
import { castleAct } from "./device.js";
import { lightsMarkup, sectionHead, speakerMarkup, testPct, wireTests }
  from "./device_tests.js";

interface SdFile {
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

interface DeviceStatus {
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

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return (await r.json()) as T;
}

export class DevicePanel {
  private root: HTMLDivElement;
  private body: HTMLDivElement;
  private open = false;

  constructor(parent?: HTMLElement) {
    this.root = document.createElement("div");
    // Styled in previewer/panels.css — as tokens, not a private palette
    // hardcoded here (grade report C7).
    // Lives inside the castle dock when device.ts provides one, so chip and
    // panel are one widget rather than two floating boxes.
    this.root.id = "devicePanel";
    this.body = document.createElement("div");
    this.root.appendChild(this.body);
    (parent ?? document.body).appendChild(this.root);
  }

  toggle(): void {
    this.open = !this.open;
    this.root.style.display = this.open ? "block" : "none";
    if (this.open) void this.render();
  }

  /** Re-render only while open — a closed panel that polls is a panel lying
   *  from a castle that is no longer there (pass 1, J1-4). */
  refresh(): void {
    if (this.open) void this.render();
  }

  private async render(): Promise<void> {
    let st: DeviceStatus;
    let files: SdFile[] = [];
    try {
      st = await getJson<DeviceStatus>("/api/status");
      // The studio answers a castle-less probe 200 {"studio":true}: an
      // empty object that rendered as "vundefined · NaN MB · no SD card"
      // — a plausible, invented control panel (J2-1). Treat it as down.
      if (st.studio || !st.version) throw new Error("no castle");
      if (st.sd_mounted) files = await getJson<SdFile[]>("/api/files");
    } catch {
      this.body.innerHTML =
        `<div class="dp__hd"><span class="dp__grow">castle stopped answering</span>` +
        `<button id="dpClose" class="dp__x" title="Close this panel" aria-label="Close">✕</button></div>`;
      this.body.querySelector<HTMLButtonElement>("#dpClose")!
        .addEventListener("click", () => this.toggle());
      return;
    }
    const tracks = files.filter((f) => !f.dir && /\.(mp3|wav)$/i.test(f.name));
    const onCard = new Set(tracks.map((f) => f.name));

    // Class names only — the rules are previewer/panels.css's .dp__* block,
    // so theme and phone CSS reach every row (grade report C2).
    this.body.innerHTML =
      `<div class="dp__hd">` +
      `<span class="dp__grow"><b>🏰 v${esc(st.version)}</b>` +
      `<small class="dp__muted"> · up ${fmtUptime(st.uptime_s)}</small></span>` +
      `<button id="dpClose" class="dp__x" title="Close this panel" aria-label="Close">✕</button>` +
      `</div>` +
      healthMeta(st) +

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
      `</div>`;

    this.body.querySelector<HTMLButtonElement>("#dpClose")!
      .addEventListener("click", () => this.toggle());

    // Every control below goes through castleAct (device.ts): toast with
    // the castle's reason on failure, and a chip re-poll on success — the
    // panel used to fire-and-forget, so a 404 delete still "succeeded" and
    // the chip said "idle" while the castle played (pass 1, J1-6/J1-7).
    // The playlist toggle re-renders after the queued action lands (the
    // 200 ms bridge plus a beat), so the button reflects the device's own
    // idea of the show, not the click's.
    this.body.querySelector<HTMLButtonElement>("#dpPlaylist")!
      .addEventListener("click", () => {
        void castleAct(`/api/show/${st.show_on ? "stop" : "start"}`,
                       st.show_on ? "show stopped" : "show started")
          .then(() => new Promise(r => setTimeout(r, 600)))
          .then(() => this.render());
      });

    // The light override: a colour parks the chain, "resume show" gives it
    // back. The strip and speaker benches wire themselves (device_tests.ts).
    this.body.querySelector<HTMLInputElement>("#dpColor")!
      .addEventListener("input", (e) => {
        const hex = (e.target as HTMLInputElement).value.slice(1);
        // quiet: the picker fires continuously while the hand drags; the
        // fixed wording lets toast() fold a whole drag's failures into one.
        void castleAct(`/api/light?c=${hex}@${testPct}`, "lights colour", { quiet: true });
      });
    this.body.querySelector<HTMLButtonElement>("#dpShow")!
      .addEventListener("click", () =>
        void castleAct("/api/light?c=show", "lights back to the show"));
    this.body.querySelector<HTMLButtonElement>("#dpOff")!
      .addEventListener("click", () =>
        void castleAct("/api/light?c=off", "lights off"));
    wireTests(this.body);

    this.body.querySelectorAll<HTMLButtonElement>("[data-play]").forEach((b) =>
      b.addEventListener("click", () => {
        const f = tracks[Number(b.dataset.play)];
        if (f === undefined) return;
        void castleAct(`/api/play?f=${encodeURIComponent(f.name)}`,
                       `playing ${f.name} on the castle`);
      }));

    this.body.querySelectorAll<HTMLButtonElement>("[data-del]").forEach((b) =>
      b.addEventListener("click", () => {
        const f = tracks[Number(b.dataset.del)];
        if (f === undefined) return;
        // A deliberate two-step: deleting from a 30 GB card is cheap to undo
        // (push again), but "the show's track vanished on Halloween" is not.
        if (!confirm(`Delete ${f.name} from the castle's SD card?`)) return;
        void castleAct(`/api/files/${encodeURIComponent(f.name)}`,
                       `deleted ${f.name} from the card`, { method: "DELETE" })
          .then((ok) => { if (ok) { cardChanged(); void this.render(); } });
      }));

    // PIR settings: each control posts just its own field; the device's
    // main loop applies them to the persisted entities.
    this.body.querySelector<HTMLInputElement>("#dpPirArm")!
      .addEventListener("change", (e) => {
        const on = (e.target as HTMLInputElement).checked;
        void castleAct(`/api/pir?armed=${on ? 1 : 0}`,
                       on ? "motion sensor armed" : "motion sensor off");
      });
    this.body.querySelector<HTMLSelectElement>("#dpPirScene")!
      .addEventListener("change", (e) => {
        const sc = (e.target as HTMLSelectElement).value;
        void castleAct(`/api/pir?scene=${encodeURIComponent(sc)}`,
                       `motion sensor plays ${sc}`);
      });
    this.body.querySelector<HTMLInputElement>("#dpPirCool")!
      .addEventListener("change", (e) => {
        const v = (e.target as HTMLInputElement).value;
        void castleAct(`/api/pir?cooldown=${v}`, `motion cooldown ${v} s`);
      });

    // Drag-drop upload: the last terminal-only workflow, gone. Files land in
    // the card root, same as tools/sd_sync.py push.
    const drop = this.body.querySelector<HTMLDivElement>("#dpDrop")!;
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("dp__drop--over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dp__drop--over"));
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.classList.remove("dp__drop--over");
      for (const f of Array.from(e.dataTransfer?.files ?? [])) {
        drop.textContent = `uploading ${f.name} (${(f.size / 1024) | 0} KB)…`;
        const r = await fetch(`/api/files/${encodeURIComponent(f.name)}`,
                              { method: "PUT", body: f });
        drop.textContent = r.ok ? `✓ ${f.name}` : `✗ ${f.name} failed`;
      }
      cardChanged();                 // the Library below re-reads the card now
      void this.render();
    });

    const logBtn = this.body.querySelector<HTMLButtonElement>("#dpLog")!;
    const logOut = this.body.querySelector<HTMLPreElement>("#dpLogOut")!;
    logBtn.addEventListener("click", async () => {
      const showing = !logOut.hidden;
      logOut.hidden = showing;
      logBtn.textContent = showing ? "boot log ▸" : "boot log ▾";
      if (!showing) {
        logOut.textContent = "loading…";
        try {
          const r = await fetch("/api/bootlog");
          // The ring keeps ANSI colour codes out already; strip any stragglers.
          logOut.textContent = (await r.text()).replace(/\x1b\[[0-9;]*m/g, "");
        } catch {
          logOut.textContent = "could not fetch the boot log";
        }
      }
    });
  }
}
