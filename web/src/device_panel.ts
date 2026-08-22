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
 * What it offers:
 *   - status: firmware version, uptime, free PSRAM (the SD turntable's budget)
 *   - the SD library: play any track on the castle speaker, delete with care
 *   - the boot log: the invisible early-boot window, one tap away
 * Volume lives on the chip (device.ts) alone — the same slider twice was
 * the scatter the dogfood pass called out, and two sliders drift.
 *
 * The intended way to use it with the stage: leave the desk's own audio muted
 * (it is by default), press play here — the castle makes the sound, the canvas
 * above renders the full 21-pixel show the finished rig would do, and the one
 * soldered pixel plays its part in the corner of the room.
 */

import { cardChanged } from "./castle_bus.js";
import { castleAct } from "./device.js";
import { ZONE_ORDER } from "./rig.js";

/** The strips in porch order (left, door, right) for the per-line test. */
const STRIPS = ZONE_ORDER;
/** Brightness for the strip test and the colour picker; survives a
 *  re-render, not a reload. 100 % on a tower is a lot of LED in a dark room. */
let testPct = 100;
const PCTS = [25, 50, 75, 100] as const;
/** The bench patterns, on every strip at once — each answers a different
 *  question than a solid colour does. Names match gen_rig's TEST_EFFECTS. */
/** The colour buttons on every strip row: what to send, what to print. */
const CHANNELS: readonly (readonly [string, string])[] = [
  ["ff0000", "R"], ["00ff00", "G"], ["0000ff", "B"], ["white", "W"], ["off", "off"],
];

/** What one strip-test button promises, for its tooltip. */
function stripTitle(zone: string, label: string): string {
  if (label === "off") return `${zone}: off`;
  if (label === "W") return `${zone}: white channel`;
  return `${zone}: solid ${label}`;
}

const PATTERNS: readonly (readonly [string, string])[] = [
  ["bars", "R G B repeating: colour order, pixel count, dead pixels"],
  ["chase", "One dot walking: where it stops is where the data stops"],
  ["ends", "First pixel red, last blue: which end the data goes in"],
];

interface SdFile {
  name: string;
  size: number;
  dir: boolean;
}

interface DeviceStatus {
  version: string;
  uptime_s: number;
  sd_mounted: boolean;
  psram_free_kb: number;
  /** KB free on the card — v5.23+. */
  sd_free_kb?: number;
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
    // Styled in previewer/panels.css — as tokens, so the panel follows the
    // light theme instead of hardcoding its own dark one (grade report C7).
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

  /** Redraw if open — device.ts calls this when the castle goes quiet or
   *  comes back, so the panel cannot keep showing a file list and an uptime
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

    // Class names only — the rules are previewer/panels.css's .dp__* block,
    // so theme and phone CSS reach every row (grade report C2).
    this.body.innerHTML =
      `<div class="dp__hd">` +
      `<span class="dp__grow"><b>🏰 v${st.version}</b> · up ${fmtUptime(st.uptime_s)}` +
      (st.sd_free_kb
        ? ` · card ${(st.sd_free_kb / 1048576).toFixed(1)} GB free` : "") +
      ` <small title="Free working memory (PSRAM) — what the SD turntable runs on">` +
      `· ${(st.psram_free_kb / 1024).toFixed(1)} MB memory free</small></span>` +
      `<button id="dpClose" class="dp__x" title="Close this panel" aria-label="Close">✕</button>` +
      `</div>` +
      `<div class="dp__row">` +
      `<button id="dpPlaylist" class="dp__go${st.show_on ? " dp__go--on" : ""}" ` +
      `title="Every scene in order with dark gaps, ` +
      `looping until stopped — the whole evening on one button">` +
      `${st.show_on ? "■ stop the show" : "▶ start the show"}</button>` +
      (st.show_on && st.scene
        ? ` <small class="dp__muted">now: ${st.scene}</small>` : "") +
      // The castle serves a four-button page of its own (firmware/
      // sd_web_remote.h) — the thing to hand a phone on the porch. Nothing
      // linked to it (JB1-8); now the panel does.
      `<a id="dpRemote" class="dp__link" href="/remote" target="_blank" rel="noopener" ` +
      `title="The castle's own phone page: ambient, scare, stop and the evening ` +
      `show on four thumb-sized buttons. Opens in a new tab">📱 phone remote</a>` +
      `</div>` +
      `<div class="dp__lights" ` +
      `title="Park every pixel on one colour, or give them back to the show">` +
      `💡 <small class="dp__muted">lights</small> ` +
      `<input id="dpColor" class="dp__color" type="color" value="#ff8c1e" ` +
      `title="Park the pixels on a colour">` +
      `<button id="dpShow" class="dp__btn" title="Hand the pixels back to the scene engine">` +
      `resume show</button>` +
      `<button id="dpOff" class="dp__ghost">off</button>` +
      `</div>` +
      // One strip at a time: which data line is dead, which one strobes.
      // Plain colours, no effect engine in the way — a strip that will not
      // show solid red here is a wiring/shifter/power problem, not a scene.
      `<div class="dp__lights dp__strips" ` +
      `title="Drive ONE strip with a solid colour — finds the dead data line. ` +
      `Any test stops the scene first, so nothing else is touching the pixels">` +
      `🔌 <small class="dp__muted">strip test</small> ` +
      `<span class="dp__strip" title="Brightness for the strip test and the colour picker">` +
      PCTS.map((p) =>
        `<button class="dp__ghost dp__btn--sm" data-pct="${p}" ` +
        `aria-pressed="${p === testPct}">${p}%</button>`).join("") + `</span> ` +
      STRIPS.map((z) =>
        `<span class="dp__strip"><small>${z}</small> ` +
        CHANNELS
          .map(([spec, label]) =>
            `<button class="dp__ghost dp__btn--sm" data-zl="${z}:${spec}" ` +
            `title="${stripTitle(z, label)}">${label}</button>`)
          .join("") + `</span>`).join(" ") +
      `<span class="dp__strip" title="Patterns run on every strip at once">` +
      `<small>patterns</small>` +
      PATTERNS.map(([spec, why]) =>
        `<button class="dp__ghost dp__btn--sm" data-zl=":${spec}" title="${why}">` +
        `${spec}</button>`).join("") + `</span>` +
      `</div>` +
      `<div class="dp__files" id="dpFiles">` +
      (tracks.length && st.bridged
        // Through the studio the merged Library below already lists every
        // card file with Play/⬇/Delete — two lists of one card drift.
        ? `<div class="dp__note">${tracks.length} ` +
          `track${tracks.length === 1 ? "" : "s"} on the card — see the ` +
          `Library below (🏰 rows and badges)</div>`
        : tracks.length
        ? tracks.map((f, i) =>
            `<div class="dp__file">` +
            `<button data-play="${i}" class="dp__btn dp__btn--sm" title="Play on the castle">▶</button>` +
            `<span class="dp__file-nm" title="${f.name}">${f.name}</span>` +
            `<small class="dp__muted">${kb(f.size)}</small>` +
            `<button data-del="${i}" class="dp__del" title="Delete from the card">✕</button>` +
            `</div>`).join("")
        : `<div class="dp__note">` +
          `${st.sd_mounted
            ? "no tracks on the card yet — drop audio below, or press → Castle on a track in the Library"
            : "no SD card"}</div>`) +
      `</div>` +
      `<div class="dp__pir" ` +
      `title="The motion sensor: when someone walks up, which scene plays, and how long before it can fire again">` +
      `👣 <small class="dp__muted">motion sensor</small> ` +
      `<label><input type="checkbox" id="dpPirArm" ${st.pir?.armed ? "checked" : ""}> armed</label> ` +
      `<select id="dpPirScene" title="Which scene the motion sensor plays">` +
      sceneIds().map((s) =>
        `<option${s === st.pir?.scene ? " selected" : ""}>${s}</option>`).join("") +
      `</select> ` +
      `<input id="dpPirCool" class="dp__cool" type="number" min="5" max="600" step="5" ` +
      `value="${st.pir?.cooldown_s ?? 60}" ` +
      `title="Cooldown: seconds before the sensor can fire again">` +
      `<small class="dp__muted"> s between triggers</small>` +
      `</div>` +
      `<div id="dpDrop" class="dp__drop">drop audio files here to upload</div>` +
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

    // The light override: a colour parks the chain (today: the one onboard
    // pixel, which plays towerL pixel 0), "resume show" gives it back.
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
    this.body.querySelectorAll<HTMLButtonElement>("[data-zl]").forEach((b) =>
      b.addEventListener("click", () => {
        const spec = b.dataset.zl!.replace(/^:/, "");   // ":bars" = all strips
        const arg = spec.endsWith("off") ? spec : `${spec}@${testPct}`;
        void castleAct(`/api/light?c=${arg}`, `strip ${arg}`);
      }));
    this.body.querySelectorAll<HTMLButtonElement>("[data-pct]").forEach((b) =>
      b.addEventListener("click", () => {
        testPct = Number(b.dataset.pct);
        this.body.querySelectorAll<HTMLButtonElement>("[data-pct]").forEach((o) =>
          o.setAttribute("aria-pressed", String(o === b)));
      }));

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
