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
}

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
    this.root.id = "devicePanel";
    this.root.style.cssText =
      "position:fixed;right:12px;bottom:56px;z-index:39;width:290px;" +
      "background:#1c1428;color:#e8e0f0;border:1px solid #503a75;" +
      "border-radius:12px;font:13px system-ui;display:none;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.55);overflow:hidden";
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
      `🔊 <input id="dpVol" type="range" min="0" max="100" value="70" ` +
      `style="width:200px;vertical-align:middle">` +
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
      `<div style="padding:.4rem .8rem;border-top:1px solid #35264f">` +
      `<button id="dpLog" style="cursor:pointer;background:none;border:0;` +
      `color:#9a8fb0">boot log ▸</button>` +
      `<pre id="dpLogOut" style="display:none;max-height:160px;overflow:auto;` +
      `font-size:11px;white-space:pre-wrap;margin:.4rem 0 0"></pre>` +
      `</div>`;

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
