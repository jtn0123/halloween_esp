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
 *   - a status chip appears (name, firmware version, SD state), polled gently;
 *   - picking a scene in the desk also fires it on the real castle, so the
 *     canvas preview and the porch are looking at the same show;
 *   - a stop button stops the hardware, not just the page.
 *
 * Mirroring is fire-and-forget on purpose. The desk must never stall on the
 * radio link, and a lost POST costs one button press, not state: the device
 * runs its own show engine and this page only nudges it.
 */

/** What `deviceBridge()` hands back; every call is safe in simulator mode. */
export interface DeviceLink {
  /** Fire scene `id` on the castle, if one is listening and mirroring is on. */
  scene(id: string): void;
  /** Stop castle audio + scene. */
  stop(): void;
}

import { DevicePanel } from "./device_panel.js";

interface Status {
  version: string;
  sd_mounted: boolean;
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

/** POST and forget; errors surface on the chip, not as dialogs. */
function post(path: string, onError: () => void): void {
  fetch(path, { method: "POST" }).then(
    (r) => { if (!r.ok) onError(); },
    onError,
  );
}

export function deviceBridge(): DeviceLink {
  let live = false;
  let mirror = true;
  const panel = new DevicePanel();

  const chip = document.createElement("div");
  chip.id = "deviceChip";
  chip.style.cssText =
    "position:fixed;right:12px;bottom:12px;z-index:40;display:none;" +
    "background:#241a38;color:#e8e0f0;border:1px solid #503a75;" +
    "border-radius:10px;padding:.5rem .75rem;font:13px system-ui;" +
    "box-shadow:0 4px 16px rgba(0,0,0,.5)";
  document.body.appendChild(chip);

  const render = (s: Status, note = "") => {
    chip.style.display = "block";
    chip.innerHTML =
      `🏰 castle v${s.version} · ${s.sd_mounted ? "SD ok" : "no SD"}` +
      ` <label style="margin-left:.5rem;cursor:pointer">` +
      `<input type="checkbox" id="devMirror" ${mirror ? "checked" : ""}>` +
      ` play on castle</label>` +
      ` <button id="devStop" style="margin-left:.5rem;cursor:pointer;` +
      `background:#3a2a55;color:inherit;border:0;border-radius:6px;` +
      `padding:.2rem .5rem">■ stop</button>` +
      ` <button id="devMore" title="SD library, volume, boot log" ` +
      `style="margin-left:.25rem;cursor:pointer;background:#3a2a55;` +
      `color:inherit;border:0;border-radius:6px;padding:.2rem .5rem">☰</button>` +
      (note ? `<div style="color:#c9a">${note}</div>` : "");
    chip.querySelector<HTMLInputElement>("#devMirror")!
      .addEventListener("change", (e) => {
        mirror = (e.target as HTMLInputElement).checked;
      });
    chip.querySelector<HTMLButtonElement>("#devStop")!
      .addEventListener("click", () => post("/api/stop", () => {}));
    chip.querySelector<HTMLButtonElement>("#devMore")!
      .addEventListener("click", () => panel.toggle());
  };

  void probe().then((s) => {
    if (s === null) return;   // simulator mode: the chip never appears
    live = true;
    render(s);
    // A slow poll keeps the chip honest (version after an OTA, card pulled).
    setInterval(async () => {
      const now = await probe();
      if (now !== null) render(now);
      else chip.style.opacity = "0.4";   // castle stopped answering
    }, 15000);
  });

  return {
    scene(id: string): void {
      if (!live || !mirror) return;
      post(`/api/scene?s=${encodeURIComponent(id)}`, () =>
        render({ version: "?", sd_mounted: false }, "scene POST failed"));
    },
    stop(): void {
      if (!live) return;
      post("/api/stop", () => {});
    },
  };
}
