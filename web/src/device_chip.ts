/**
 * The castle chip's face: how one status renders as markup, and which
 * control fires which handler. Split out of device.ts (500-line cap) along
 * the seam "what the chip LOOKS like" vs "what the bridge KNOWS" — the
 * probe, the route, the masthead line and every timer stay in device.ts;
 * this file is pure markup plus listener wiring, with no fetch and no state
 * of its own. Colours and spacing live in previewer/panels.css (#deviceChip)
 * so the chip follows the light/dark theme like the rest of the desk.
 */

/** The slice of /api/status the chip reads (device.ts owns the full type). */
export interface ChipStatus {
  version: string;
  sd_mounted?: boolean;
  sd_free_kb?: number;
  scene?: string;
  track?: string;
}

/** "SD ok" / "no SD", or nothing at all when the answer isn't known. */
export const sdText = (s: ChipStatus): string => {
  if (s.sd_mounted === undefined) return "";
  return s.sd_mounted ? " · SD ok" : " · no SD";
};

/** The chip's richer version: how much room the card actually has. */
export const sdChip = (s: ChipStatus): string => {
  if (s.sd_mounted === undefined) return "";
  if (!s.sd_mounted) return " · no SD";
  return s.sd_free_kb
    ? ` · SD ${(s.sd_free_kb / 1048576).toFixed(1)} GB free` : " · SD ok";
};

/** "▶ scene · track", or "idle". A track can play with no scene (the card
 *  rows, the panel's ▶) — the chip must say so, not "idle". */
export function nowLine(s: ChipStatus): string {
  const bits = [s.scene && s.scene !== "stop" ? s.scene : "", s.track ?? ""]
    .filter(Boolean);
  return bits.length ? `▶ ${bits.join(" · ")}` : "idle";
}

export function chipHtml(s: ChipStatus, vol: number, mirror: boolean): string {
  return (
    `<div>🏰 castle v${s.version}${sdChip(s)} · ` +
    `<span id="devNow">${nowLine(s)}</span></div>` +
    `<div class="chip__row">` +
    `<button id="devSnd" class="chip__btn"></button>` +
    `<button id="devMute" class="chip__btn" title="Mute the castle speaker">` +
    `${vol === 0 ? "🔇" : "🔊"}</button>` +
    `<input id="devVol" type="range" min="0" max="100" value="${vol}">` +
    `<label class="chip__mirror" title="Also fire scene picks on the real castle">` +
    `<input type="checkbox" id="devMirror" ${mirror ? "checked" : ""}> on castle</label>` +
    `<button id="devStop" class="chip__btn" title="Stop the castle: audio and scene">■</button>` +
    `<button id="devMore" class="chip__btn" ` +
    `title="The castle's own controls: SD library, show, light, motion sensor, boot log">` +
    `🏰 Castle</button>` +
    `</div>`);
}

export interface ChipHandlers {
  mirror: (on: boolean) => void;
  stop: () => void;
  more: () => void;
  route: () => void;
  /** Slider settled (150 ms after the last move) at this level. */
  volume: (v: number) => void;
  /** 🔇 pressed; the slider is handed over so mute can read and set it. */
  mute: (vol: HTMLInputElement) => void;
}

/** Attach the chip's listeners to freshly rendered markup. */
export function wireChip(chip: HTMLElement, h: ChipHandlers): void {
  const q = <T extends HTMLElement>(id: string): T => chip.querySelector<T>(`#${id}`)!;
  q<HTMLInputElement>("devMirror").addEventListener("change", (e) =>
    h.mirror((e.target as HTMLInputElement).checked));
  q("devStop").addEventListener("click", () => h.stop());
  q("devMore").addEventListener("click", () => h.more());
  q("devSnd").addEventListener("click", () => h.route());
  const vol = q<HTMLInputElement>("devVol");
  let volTimer: number | undefined;
  vol.addEventListener("input", () => {
    clearTimeout(volTimer);
    volTimer = window.setTimeout(() => h.volume(Number(vol.value)), 150);
  });
  q("devMute").addEventListener("click", () => h.mute(vol));
}
