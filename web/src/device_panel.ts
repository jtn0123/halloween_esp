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
        `<div style="padding:.6rem .8rem;display:flex;align-items:center;gap:.4rem">` +
        `<span style="flex:1">castle stopped answering</span>` +
        `<button id="dpClose" title="Close this panel" aria-label="Close" ` +
        `style="cursor:pointer;background:none;border:0;color:var(--muted);` +
        `font-size:14px;padding:0 .2rem">✕</button></div>`;
      this.body.querySelector<HTMLButtonElement>("#dpClose")!
        .addEventListener("click", () => this.toggle());
      return;
    }
    const tracks = files.filter((f) => !f.dir && /\.(mp3|wav)$/i.test(f.name));

    this.body.innerHTML =
      `<div style="padding:.6rem .8rem;border-bottom:1px solid var(--line-2);` +
      `display:flex;align-items:center;gap:.4rem">` +
      `<span style="flex:1"><b>🏰 v${st.version}</b> · up ${fmtUptime(st.uptime_s)}` +
      (st.sd_free_kb
        ? ` · card ${(st.sd_free_kb / 1048576).toFixed(1)} GB free` : "") +
      ` <small title="Free working memory (PSRAM) — what the SD turntable runs on">` +
      `· ${(st.psram_free_kb / 1024).toFixed(1)} MB memory free</small></span>` +
      `<button id="dpClose" title="Close this panel" aria-label="Close" ` +
      `style="cursor:pointer;background:none;border:0;color:var(--muted);` +
      `font-size:14px;padding:0 .2rem">✕</button>` +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-bottom:1px solid var(--line-2);` +
      `display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">` +
      `<button id="dpPlaylist" title="Every scene in order with dark gaps, ` +
      `looping until stopped — the whole evening on one button" ` +
      `style="cursor:pointer;border:0;border-radius:6px;padding:.25rem .7rem;` +
      `background:${st.show_on ? "var(--alarm)" : "var(--spirit)"};color:#fff">` +
      `${st.show_on ? "■ stop the show" : "▶ start the show"}</button>` +
      (st.show_on && st.scene
        ? ` <small style="color:var(--muted)">now: ${st.scene}</small>` : "") +
      // The castle serves a four-button page of its own (firmware/
      // sd_web_remote.h) — the thing to hand a phone on the porch. Nothing
      // linked to it (JB1-8); now the panel does.
      `<a id="dpRemote" href="/remote" target="_blank" rel="noopener" ` +
      `title="The castle's own phone page: ambient, scare, stop and the evening ` +
      `show on four thumb-sized buttons. Opens in a new tab" ` +
      `style="margin-left:auto;color:var(--cool);text-decoration:none;` +
      `white-space:nowrap">📱 phone remote</a>` +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-bottom:1px solid var(--line-2);` +
      `display:flex;gap:.5rem;align-items:center" ` +
      `title="Park every pixel on one colour, or give them back to the show">` +
      `💡 <small style="color:var(--muted)">lights</small> ` +
      `<input id="dpColor" type="color" value="#ff8c1e" ` +
      `title="Park the pixels on a colour" style="cursor:pointer">` +
      `<button id="dpShow" title="Hand the pixels back to the scene engine" ` +
      `style="cursor:pointer;background:var(--panel);color:inherit;border:0;` +
      `border-radius:6px;padding:.2rem .5rem">resume show</button>` +
      `<button id="dpOff" style="cursor:pointer;background:none;` +
      `color:var(--muted);border:1px solid var(--line-2);border-radius:6px;` +
      `padding:.2rem .5rem">off</button>` +
      `</div>` +
      `<div style="max-height:180px;overflow:auto" id="dpFiles">` +
      (tracks.length && st.bridged
        // Through the studio the merged Library below already lists every
        // card file with Play/⬇/Delete — two lists of one card drift.
        ? `<div style="padding:.5rem .8rem;color:var(--muted)">${tracks.length} ` +
          `track${tracks.length === 1 ? "" : "s"} on the card — see the ` +
          `Library below (🏰 rows and badges)</div>`
        : tracks.length
        ? tracks.map((f, i) =>
            `<div style="display:flex;gap:.4rem;align-items:center;` +
            `padding:.3rem .8rem">` +
            `<button data-play="${i}" title="Play on the castle" ` +
            `style="cursor:pointer;background:var(--panel);color:inherit;border:0;` +
            `border-radius:6px;padding:.15rem .5rem">▶</button>` +
            `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;` +
            `white-space:nowrap" title="${f.name}">${f.name}</span>` +
            `<small style="color:var(--muted)">${kb(f.size)}</small>` +
            `<button data-del="${i}" title="Delete from the card" ` +
            `style="cursor:pointer;background:none;color:var(--muted);border:0">✕</button>` +
            `</div>`).join("")
        : `<div style="padding:.5rem .8rem;color:var(--muted)">` +
          `${st.sd_mounted
            ? "no tracks on the card yet — drop audio below, or press → Castle on a track in the Library"
            : "no SD card"}</div>`) +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-top:1px solid var(--line-2)" ` +
      `title="The motion sensor: when someone walks up, which scene plays, and how long before it can fire again">` +
      `👣 <small style="color:var(--muted)">motion sensor</small> ` +
      `<label><input type="checkbox" id="dpPirArm" ${st.pir?.armed ? "checked" : ""}> armed</label> ` +
      `<select id="dpPirScene" title="Which scene the motion sensor plays">` +
      sceneIds().map((s) =>
        `<option${s === st.pir?.scene ? " selected" : ""}>${s}</option>`).join("") +
      `</select> ` +
      `<input id="dpPirCool" type="number" min="5" max="600" step="5" ` +
      `value="${st.pir?.cooldown_s ?? 60}" style="width:3.5rem" ` +
      `title="Cooldown: seconds before the sensor can fire again">` +
      `<small style="color:var(--muted)"> s between triggers</small>` +
      `</div>` +
      `<div id="dpDrop" style="padding:.4rem .8rem;border-top:1px dashed var(--line-2);` +
      `color:var(--muted);text-align:center">drop audio files here to upload</div>` +
      `<div style="padding:.4rem .8rem;border-top:1px solid var(--line-2)">` +
      `<button id="dpLog" style="cursor:pointer;background:none;border:0;` +
      `color:var(--muted)">boot log ▸</button>` +
      `<pre id="dpLogOut" style="display:none;max-height:160px;overflow:auto;` +
      `font-size:11px;white-space:pre-wrap;margin:.4rem 0 0"></pre>` +
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
        void castleAct(`/api/light?c=${hex}`, "lights colour", { quiet: true });
      });
    this.body.querySelector<HTMLButtonElement>("#dpShow")!
      .addEventListener("click", () =>
        void castleAct("/api/light?c=show", "lights back to the show"));
    this.body.querySelector<HTMLButtonElement>("#dpOff")!
      .addEventListener("click", () =>
        void castleAct("/api/light?c=off", "lights off"));

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
      drop.style.color = "var(--ink)";
    });
    drop.addEventListener("dragleave", () => { drop.style.color = "var(--muted)"; });
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.style.color = "var(--muted)";
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
      const showing = logOut.style.display !== "none";
      logOut.style.display = showing ? "none" : "block";
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
