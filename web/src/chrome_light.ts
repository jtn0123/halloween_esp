/**
 * The page lighting itself from the show.
 *
 * Two things that are not panels: the masthead's live status line, and the
 * glow the stage and header take from whatever the zones are currently
 * putting out. Neither is text, table or widget — they are the show leaking
 * into the chrome around it — and the frame they read is exactly the state
 * panels.ts promises never to look at. So they live here instead.
 *
 * Everything is hue-only, scaled by luminance: a dim scene glows faintly in
 * its own colour rather than washing out to grey, and a dark one leaves the
 * chrome at its theme defaults. Colours are mixed INTO the theme tokens
 * rather than set outright, because a value bright enough to read against
 * the night stage is unreadable on the light theme's ground.
 */

import type { ZoneRender } from "./show.js";

const el = (id: string): HTMLElement | null => document.getElementById(id);

const shell = el("stageShell");
const head = el("head");
const headTxt = el("headTxt");
const headDot = el("headDot");

const byte = (v: number): number => Math.round(Math.min(1, v) * 255);

/**
 * The one live line in the masthead: which machine, what is sounding.
 *
 * `ok` drives the dot beside it. A green light next to "castle not
 * answering" is worse than no light at all — it is the one place on the page
 * someone glances at to decide whether the porch is alive.
 */
export function setStatus(text: string, ok = true): void {
  if (headTxt) headTxt.textContent = text;
  if (!headDot) return;
  const c = ok ? "var(--spirit)" : "var(--alarm)";
  headDot.style.background = c;
  headDot.style.boxShadow = `0 0 7px ${c}`;
}

/** Paint one frame's output into the chrome: the stage panel's border and
 *  bloom from the mean of all three zones, the masthead's spill from the
 *  doorway, and the status line from the left tower. */
export function lightChrome(out: ZoneRender): void {
  const zones = [out.towerL.avg, out.door.avg, out.towerR.avg];
  const mean = [0, 1, 2].map(k =>
    Math.min(1, ((zones[0]?.[k] ?? 0) + (zones[1]?.[k] ?? 0) + (zones[2]?.[k] ?? 0)) / 3));
  const lum = Math.max(mean[0] ?? 0, mean[1] ?? 0, mean[2] ?? 0);

  if (shell) {
    // Keep the hue, let luminance drive the strength.
    const n = lum > 0.02 ? 1 / lum : 0;
    const tint = `rgba(${byte((mean[0] ?? 0) * n)},${byte((mean[1] ?? 0) * n)},`
      + `${byte((mean[2] ?? 0) * n)},${(0.1 + Math.sqrt(lum) * 0.34).toFixed(3)})`;
    shell.style.boxShadow =
      `var(--shadow), 0 0 ${(22 + lum * 54).toFixed(0)}px -6px ${tint}`;
    shell.style.borderColor = `color-mix(in srgb, ${tint} 60%, var(--line))`;
  }

  if (!head) return;
  const d = out.door.avg;
  head.style.background = "radial-gradient(120% 260% at 22% 150%, "
    + `color-mix(in srgb, rgb(${byte(d[0])},${byte(d[1])},${byte(d[2])}) 16%, `
    + "transparent), transparent 70%)";

  if (!headTxt) return;
  const l = out.towerL.avg;
  const lit = Math.max(l[0], l[1], l[2]);
  headTxt.style.color = `color-mix(in srgb, rgb(${byte(l[0])},${byte(l[1])},`
    + `${byte(l[2])}) ${(lit * 45).toFixed(0)}%, var(--muted))`;
}
