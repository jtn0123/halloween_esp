/**
 * The device bridge — what makes the cue desk aware it is being served BY the
 * castle rather than from a laptop file.
 *
 * The same single-file desk is used two ways: opened locally (pure simulator,
 * no hardware anywhere) and served from the ESP32's SD card at http://<castle>/
 * (see firmware/sd_web.h). The only difference between those two worlds is
 * whether `/api/status` answers from the page's own origin — so that probe is
 * the entire mode switch. No build flag, no second bundle.
 *
 * In device mode:
 *   - a status chip appears: version, SD state, what is PLAYING right now,
 *     a volume slider that starts where the amp actually is, and mute;
 *   - picking a scene in the desk also fires it on the real castle, so the
 *     canvas preview and the porch are looking at the same show;
 *   - on load the desk ADOPTS the castle's current scene, so the page opens
 *     showing what the hardware is doing rather than the default;
 *   - every queued POST answers with a small toast — the pending-action queue
 *     on the device means "queued", and silence reads as a dead button.
 *
 * Mirroring is fire-and-forget on purpose. The desk must never stall on the
 * radio link, and a lost POST costs one button press, not state: the device
 * runs its own show engine and this page only nudges it.
 */

import { DevicePanel } from "./device_panel.js";

/** What `deviceBridge()` hands back; every call is safe in simulator mode. */
export interface DeviceLink {
  /** Fire scene `id` on the castle, if one is listening and mirroring is on. */
  scene(id: string): void;
  /** Stop castle audio + scene. */
  stop(): void;
}

interface Status {
  version: string;
  sd_mounted: boolean;
  volume?: number;
  scene?: string;
  track?: string;
}

const PROBE_TIMEOUT_MS = 1500;

async function probe(): Promise<Status | null> {
  try {
    const r = await fetch("/api/status", {
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (!r.ok) return null;
    return (await r.json()) as Status;
  } catch {
    return null;
  }
}

/** One small transient message near the chip. The device queues actions, so
 *  "queued" IS the honest success state — see the interval in castle_sd.yaml. */
export function toast(msg: string, isError = false): void {
  const el = document.createElement("div");
  el.textContent = msg;
  el.style.cssText =
    "position:fixed;right:12px;bottom:52px;z-index:41;padding:.4rem .7rem;" +
    "border-radius:8px;font:12px system-ui;pointer-events:none;" +
    "transition:opacity .4s;opacity:1;" +
    (isError ? "background:#5a1a2a;color:#ffd8e0;" : "background:#2a3a1a;color:#e0ffd0;");
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; }, 1400);
  setTimeout(() => el.remove(), 1900);
}

/** POST with a toast on both outcomes. */
function post(path: string, okMsg: string): void {
  fetch(path, { method: "POST" }).then(
    (r) => { r.ok ? toast(okMsg) : toast(`${okMsg} failed`, true); },
    () => toast(`${okMsg} failed`, true),
  );
}

export interface BridgeOpts {
  /** Called once, on first contact, with the scene the castle is running —
   *  so the desk can open showing reality instead of the default. */
  adoptScene?: (sceneId: string) => void;
}

export function deviceBridge(opts: BridgeOpts = {}): DeviceLink {
  let live = false;
  let mirror = true;
  let lastVol = 70;
  const panel = new DevicePanel();

  const chip = document.createElement("div");
  chip.id = "deviceChip";
  chip.style.cssText =
    "position:fixed;right:12px;bottom:12px;z-index:40;display:none;" +
    "background:#241a38;color:#e8e0f0;border:1px solid #503a75;" +
    "border-radius:10px;padding:.5rem .75rem;font:13px system-ui;" +
    "box-shadow:0 4px 16px rgba(0,0,0,.5)";
  document.body.appendChild(chip);

  const render = (s: Status) => {
    chip.style.display = "block";
    chip.style.opacity = "1";
    lastVol = s.volume ?? lastVol;
    const playing = s.scene && s.scene !== "stop"
      ? `▶ ${s.scene}${s.track ? ` · ${s.track}` : ""}` : "idle";
    chip.innerHTML =
      `<div>🏰 castle v${s.version} · ${s.sd_mounted ? "SD ok" : "no SD"} · ` +
      `<span id="devNow" style="color:#b8a8d8">${playing}</span></div>` +
      `<div style="margin-top:.25rem;display:flex;align-items:center;gap:.4rem">` +
      `<button id="devMute" title="Mute the castle speaker" ` +
      `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
      `border-radius:6px;padding:.15rem .45rem">${lastVol === 0 ? "🔇" : "🔊"}</button>` +
      `<input id="devVol" type="range" min="0" max="100" value="${lastVol}" ` +
      `style="width:90px">` +
      `<label style="cursor:pointer;white-space:nowrap">` +
      `<input type="checkbox" id="devMirror" ${mirror ? "checked" : ""}> on castle</label>` +
      `<button id="devStop" style="cursor:pointer;background:#3a2a55;` +
      `color:inherit;border:0;border-radius:6px;padding:.2rem .5rem">■</button>` +
      `<button id="devMore" title="SD library, light, PIR, boot log" ` +
      `style="cursor:pointer;background:#3a2a55;color:inherit;border:0;` +
      `border-radius:6px;padding:.2rem .5rem">☰</button>` +
      `</div>`;

    chip.querySelector<HTMLInputElement>("#devMirror")!
      .addEventListener("change", (e) => {
        mirror = (e.target as HTMLInputElement).checked;
      });
    chip.querySelector<HTMLButtonElement>("#devStop")!
      .addEventListener("click", () => post("/api/stop", "stop"));
    chip.querySelector<HTMLButtonElement>("#devMore")!
      .addEventListener("click", () => panel.toggle());

    let volTimer: number | undefined;
    const vol = chip.querySelector<HTMLInputElement>("#devVol")!;
    vol.addEventListener("input", () => {
      clearTimeout(volTimer);
      volTimer = window.setTimeout(
        () => post(`/api/volume?v=${vol.value}`, `volume ${vol.value}`), 150);
    });
    chip.querySelector<HTMLButtonElement>("#devMute")!
      .addEventListener("click", () => {
        // Mute is volume 0 with memory — the device has no separate flag.
        const to = Number(vol.value) === 0 ? (lastVol || 70) : 0;
        if (to === 0) lastVol = Number(vol.value);
        vol.value = String(to);
        post(`/api/volume?v=${to}`, to === 0 ? "muted" : `volume ${to}`);
      });
  };

  void probe().then((s) => {
    if (s === null) return;   // simulator mode: the chip never appears
    live = true;
    render(s);
    if (s.scene && s.scene !== "stop") opts.adoptScene?.(s.scene);
    // A slow poll keeps the chip honest (version after an OTA, card pulled,
    // a scene the PIR fired while nobody was looking).
    setInterval(async () => {
      const now = await probe();
      if (now !== null) render(now);
      else chip.style.opacity = "0.4";   // castle stopped answering
    }, 15000);
  });

  return {
    scene(id: string): void {
      if (!live || !mirror) return;
      post(`/api/scene?s=${encodeURIComponent(id)}`, `scene ${id}`);
    },
    stop(): void {
      if (!live) return;
      post("/api/stop", "stop");
    },
  };
}
