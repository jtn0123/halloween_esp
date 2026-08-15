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
 *   - volume: one slider, debounced, straight to the amp
 *   - the SD library: play any track on the castle speaker, delete with care
 *   - the boot log: the invisible early-boot window, one tap away
 *
 * The intended way to use it with the stage: leave the desk's own audio muted
 * (it is by default), press play here — the castle makes the sound, the canvas
 * above renders the full 21-pixel show the finished rig would do, and the one
 * soldered pixel plays its part in the corner of the room.
 */

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
  /** 0–100, mirrored from the media player; older firmware omits it. */
  volume?: number;
  /** Motion-sensor config, mirrored from the pir_* entities. */
  pir?: { armed: boolean; cooldown_s: number; scene: string };
  /** Is the evening playlist running; older firmware omits it. */
  show_on?: boolean;
  /** Current scene id, "" when idle. */
  scene?: string;
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

  constructor() {
    this.root = document.createElement("div");
    // Styled in previewer/panels.css — as tokens, so the panel follows the
    // light theme instead of hardcoding its own dark one (grade report C7).
    this.root.id = "devicePanel";
    this.body = document.createElement("div");
    this.root.appendChild(this.body);
    document.body.appendChild(this.root);
  }

  toggle(): void {
    this.open = !this.open;
    this.root.style.display = this.open ? "block" : "none";
    if (this.open) void this.render();
  }

  private async render(): Promise<void> {
    let st: DeviceStatus;
    let files: SdFile[] = [];
    try {
      st = await getJson<DeviceStatus>("/api/status");
      if (st.sd_mounted) files = await getJson<SdFile[]>("/api/files");
    } catch {
      this.body.innerHTML =
        `<div style="padding:.8rem">castle stopped answering</div>`;
      return;
    }
    const tracks = files.filter((f) => !f.dir && /\.(mp3|wav)$/i.test(f.name));

    this.body.innerHTML =
      `<div style="padding:.6rem .8rem;border-bottom:1px solid #35264f">` +
      `<b>🏰 v${st.version}</b> · up ${fmtUptime(st.uptime_s)} · ` +
      `${st.psram_free_kb} KB PSRAM free` +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-bottom:1px solid #35264f">` +
      `<button id="dpPlaylist" title="Every scene in order with dark gaps, ` +
      `looping until stopped — the whole evening on one button" ` +
      `style="cursor:pointer;border:0;border-radius:6px;padding:.25rem .7rem;` +
      `background:${st.show_on ? "#7a2a2a" : "#2a5537"};color:inherit">` +
      `${st.show_on ? "■ stop the show" : "▶ start the show"}</button>` +
      (st.show_on && st.scene
        ? ` <small style="color:#9a8fb0">now: ${st.scene}</small>` : "") +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-bottom:1px solid #35264f">` +
      `🔊 <input id="dpVol" type="range" min="0" max="100" ` +
      `value="${st.volume ?? 70}" style="width:200px;vertical-align:middle">` +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-bottom:1px solid #35264f;` +
      `display:flex;gap:.5rem;align-items:center">` +
      `💡 <input id="dpColor" type="color" value="#ff8c1e" ` +
      `title="Park the pixels on a colour" style="cursor:pointer">` +
      `<button id="dpShow" title="Hand the pixels back to the scene engine" ` +
      `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
      `border-radius:6px;padding:.2rem .5rem">resume show</button>` +
      `<button id="dpOff" style="cursor:pointer;background:none;` +
      `color:#9a8fb0;border:1px solid #35264f;border-radius:6px;` +
      `padding:.2rem .5rem">off</button>` +
      `</div>` +
      `<div style="max-height:180px;overflow:auto" id="dpFiles">` +
      (tracks.length
        ? tracks.map((f, i) =>
            `<div style="display:flex;gap:.4rem;align-items:center;` +
            `padding:.3rem .8rem">` +
            `<button data-play="${i}" title="Play on the castle" ` +
            `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
            `border-radius:6px;padding:.15rem .5rem">▶</button>` +
            `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;` +
            `white-space:nowrap" title="${f.name}">${f.name}</span>` +
            `<small style="color:#9a8fb0">${kb(f.size)}</small>` +
            `<button data-del="${i}" title="Delete from the card" ` +
            `style="cursor:pointer;background:none;color:#9a8fb0;border:0">✕</button>` +
            `</div>`).join("")
        : `<div style="padding:.5rem .8rem;color:#9a8fb0">` +
          `${st.sd_mounted ? "no tracks on the card — tools/sd_sync.py push" : "no SD card"}</div>`) +
      `</div>` +
      `<div style="padding:.5rem .8rem;border-top:1px solid #35264f" ` +
      `title="What the motion sensor triggers">` +
      `👣 <label><input type="checkbox" id="dpPirArm" ${st.pir?.armed ? "checked" : ""}> armed</label> ` +
      `<select id="dpPirScene">` +
      sceneIds().map((s) =>
        `<option${s === st.pir?.scene ? " selected" : ""}>${s}</option>`).join("") +
      `</select> ` +
      `<input id="dpPirCool" type="number" min="5" max="600" step="5" ` +
      `value="${st.pir?.cooldown_s ?? 60}" style="width:3.5rem" title="cooldown seconds">s` +
      `</div>` +
      `<div id="dpDrop" style="padding:.4rem .8rem;border-top:1px dashed #503a75;` +
      `color:#9a8fb0;text-align:center">drop audio files here to upload</div>` +
      `<div style="padding:.4rem .8rem;border-top:1px solid #35264f">` +
      `<button id="dpLog" style="cursor:pointer;background:none;border:0;` +
      `color:#9a8fb0">boot log ▸</button>` +
      `<pre id="dpLogOut" style="display:none;max-height:160px;overflow:auto;` +
      `font-size:11px;white-space:pre-wrap;margin:.4rem 0 0"></pre>` +
      `</div>`;

    // The playlist toggle re-renders after the queued action lands (the
    // 200 ms bridge plus a beat), so the button reflects the device's own
    // idea of the show, not the click's.
    this.body.querySelector<HTMLButtonElement>("#dpPlaylist")!
      .addEventListener("click", () => {
        void fetch(`/api/show/${st.show_on ? "stop" : "start"}`, { method: "POST" })
          .then(() => new Promise(r => setTimeout(r, 600)))
          .then(() => this.render());
      });

    // Volume: debounced so a slider drag is a handful of POSTs, not hundreds.
    let volTimer: number | undefined;
    this.body.querySelector<HTMLInputElement>("#dpVol")!
      .addEventListener("input", (e) => {
        const v = (e.target as HTMLInputElement).value;
        clearTimeout(volTimer);
        volTimer = window.setTimeout(() => {
          void fetch(`/api/volume?v=${v}`, { method: "POST" });
        }, 150);
      });

    // The light override: a colour parks the chain (today: the one onboard
    // pixel, which plays towerL pixel 0), "resume show" gives it back.
    this.body.querySelector<HTMLInputElement>("#dpColor")!
      .addEventListener("input", (e) => {
        const hex = (e.target as HTMLInputElement).value.slice(1);
        void fetch(`/api/light?c=${hex}`, { method: "POST" });
      });
    this.body.querySelector<HTMLButtonElement>("#dpShow")!
      .addEventListener("click", () =>
        void fetch("/api/light?c=show", { method: "POST" }));
    this.body.querySelector<HTMLButtonElement>("#dpOff")!
      .addEventListener("click", () =>
        void fetch("/api/light?c=off", { method: "POST" }));

    this.body.querySelectorAll<HTMLButtonElement>("[data-play]").forEach((b) =>
      b.addEventListener("click", () => {
        const f = tracks[Number(b.dataset.play)];
        if (f === undefined) return;
        void fetch(`/api/play?f=${encodeURIComponent(f.name)}`, { method: "POST" });
      }));

    this.body.querySelectorAll<HTMLButtonElement>("[data-del]").forEach((b) =>
      b.addEventListener("click", () => {
        const f = tracks[Number(b.dataset.del)];
        if (f === undefined) return;
        // A deliberate two-step: deleting from a 30 GB card is cheap to undo
        // (push again), but "the show's track vanished on Halloween" is not.
        if (!confirm(`Delete ${f.name} from the card?`)) return;
        void fetch(`/api/files/${encodeURIComponent(f.name)}`, { method: "DELETE" })
          .then(() => this.render());
      }));

    // PIR settings: each control posts just its own field; the device's
    // main loop applies them to the persisted entities.
    this.body.querySelector<HTMLInputElement>("#dpPirArm")!
      .addEventListener("change", (e) =>
        void fetch(`/api/pir?armed=${(e.target as HTMLInputElement).checked ? 1 : 0}`,
                   { method: "POST" }));
    this.body.querySelector<HTMLSelectElement>("#dpPirScene")!
      .addEventListener("change", (e) =>
        void fetch(`/api/pir?scene=${encodeURIComponent((e.target as HTMLSelectElement).value)}`,
                   { method: "POST" }));
    this.body.querySelector<HTMLInputElement>("#dpPirCool")!
      .addEventListener("change", (e) =>
        void fetch(`/api/pir?cooldown=${(e.target as HTMLInputElement).value}`,
                   { method: "POST" }));

    // Drag-drop upload: the last terminal-only workflow, gone. Files land in
    // the card root, same as tools/sd_sync.py push.
    const drop = this.body.querySelector<HTMLDivElement>("#dpDrop")!;
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.style.color = "#e8e0f0";
    });
    drop.addEventListener("dragleave", () => { drop.style.color = "#9a8fb0"; });
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.style.color = "#9a8fb0";
      for (const f of Array.from(e.dataTransfer?.files ?? [])) {
        drop.textContent = `uploading ${f.name} (${(f.size / 1024) | 0} KB)…`;
        const r = await fetch(`/api/files/${encodeURIComponent(f.name)}`,
                              { method: "PUT", body: f });
        drop.textContent = r.ok ? `✓ ${f.name}` : `✗ ${f.name} failed`;
      }
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
