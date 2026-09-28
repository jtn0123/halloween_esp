/**
 * How the stage makes a lit LED look like one: the colour maths behind the
 * glow, and the stonework the light falls on. `stage.ts` paints; this module
 * owns the pieces that are about light rather than layout.
 *
 * The picture is additive, as light is: every glow is drawn with the
 * "lighter" operator in the zone's FULL-strength hue at an alpha of its
 * level, so two dim pixels side by side sum the way two real ones would.
 */

import type { Aperture, Rgb } from "./stage.js";
import type { Layout } from "./rig.js";
import { hash } from "./effects.js";

const TAU = 6.2832;

/** The brightest channel — how lit a colour is, whatever its hue. */
export const luma = (c: Rgb): number => Math.max(c[0], c[1], c[2]);

export const rgba = (c: Rgb, a: number): string =>
  `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

/** The colour at full strength: the hue an LED shows however dim it runs. */
export function hue(c: Rgb): Rgb {
  const l = luma(c);
  return l > 0 ? [c[0] / l, c[1] / l, c[2] / l] : [0, 0, 0];
}

/**
 * The hot centre of a bright LED. A die at full drive overwhelms the eye (and
 * any camera) and reads nearly white whatever its colour; dimmed, it keeps
 * its hue. Mixed toward white by the square of the level, so only a real hit
 * whitens.
 */
export function hot(c: Rgb): Rgb {
  const l = luma(c);
  const h = hue(c);
  const w = 0.6 * l * l;
  return [h[0] + (1 - h[0]) * w, h[1] + (1 - h[1]) * w, h[2] + (1 - h[2]) * w];
}

/** One course of stone: its height, and how long a block runs. */
const COURSE = 13;
const BLOCK = 26;

/**
 * The stonework, as a multiply mask the size of the stage's backing store:
 * each block a slightly different shade, mortar darker between them. Drawn
 * over the lit castle with "multiply", it is invisible where the wall is
 * dark and shows its courses exactly where a window's light reaches — which
 * is what makes the glow read as light ON something.
 *
 * Seeded from the effects' hash, so it is the same wall on every reload.
 */
export function stoneMask(width: number, height: number, scale: number): HTMLCanvasElement {
  const cvs = document.createElement("canvas");
  cvs.width = Math.max(1, width);
  cvs.height = Math.max(1, height);
  const g2 = cvs.getContext("2d");
  // A hidden stage measures 0 wide, and every course below is a division by
  // the scale: without this the loops run to Infinity and the page hangs.
  if (!g2 || !(scale > 0) || !Number.isFinite(scale)) return cvs;
  g2.setTransform(scale, 0, 0, scale, 0, 0);
  g2.fillStyle = "rgb(128,124,132)"; // mortar
  g2.fillRect(0, 0, width / scale, height / scale);
  const rows = Math.ceil(height / scale / COURSE);
  const cols = Math.ceil(width / scale / BLOCK) + 1;
  for (let r = 0; r < rows; r++) {
    const off = (r % 2) * (BLOCK / 2);
    for (let k = -1; k < cols; k++) {
      const shade = 0.8 + 0.2 * hash(r * 31.7 + k * 5.3);
      const v = Math.round(255 * shade);
      g2.fillStyle = `rgb(${v},${v},${Math.min(255, v + 6)})`;
      g2.fillRect(k * BLOCK + off + 0.6, r * COURSE + 0.6, BLOCK - 1.2, COURSE - 1.2);
    }
  }
  // Weathering: soft dark blotches, so the wall is not a drawing of bricks.
  for (let i = 0; i < 140; i++) {
    const x = hash(i * 4.1 + 9) * (width / scale);
    const y = hash(i * 6.7 + 3) * (height / scale);
    const rad = 6 + 22 * hash(i * 1.9);
    const blot = g2.createRadialGradient(x, y, 0, x, y, rad);
    blot.addColorStop(0, "rgba(40,38,46,.22)");
    blot.addColorStop(1, "rgba(40,38,46,0)");
    g2.fillStyle = blot;
    g2.fillRect(x - rad, y - rad, rad * 2, rad * 2);
  }
  return cvs;
}

/** The room behind the opening, brightest round the jewel. */
export function interior(g2: CanvasRenderingContext2D, a: Aperture, c: Rgb, lum: number): void {
  const h = hue(c);
  const gr = g2.createRadialGradient(a.cx, a.cy, 0, a.cx, a.cy, a.w * 0.95);
  gr.addColorStop(0, rgba(h, 0.5 * lum));
  gr.addColorStop(0.55, rgba(h, 0.22 * lum));
  gr.addColorStop(1, rgba(h, 0.08 * lum));
  g2.fillStyle = gr;
  g2.fillRect(a.x, a.top, a.w, a.base - a.top);
}

/**
 * Each pixel as an LED: a halo of its colour and a small core that whitens
 * as it is driven harder (`hot`). Drawn in the full-strength hue
 * at an alpha of the level, so pixels add up the way light does.
 */
export function leds(
  g2: CanvasRenderingContext2D, a: Aperture, layout: Layout, pix: readonly Rgb[],
): void {
  const pr = a.w * Math.min(0.12, 0.38 / Math.sqrt(Math.max(1, layout.n)));
  for (let p = 0; p < layout.n; p++) {
    const point = layout.pos[p];
    // A short frame is a caller bug, but skipping beats throwing mid-paint.
    const c = pix[p];
    if (!point || !c) continue;
    const plum = luma(c);
    if (plum < 0.01) continue;
    const px = a.cx + (point[0] - 0.5) * a.w * 0.8;
    const py = a.cy + (point[1] - 0.5) * a.w * 0.8;
    const h = hue(c);
    const halo = g2.createRadialGradient(px, py, 0, px, py, pr * 3.2);
    halo.addColorStop(0, rgba(h, 0.7 * plum));
    halo.addColorStop(0.3, rgba(h, 0.28 * plum));
    halo.addColorStop(1, rgba(h, 0));
    g2.fillStyle = halo;
    g2.beginPath();
    g2.arc(px, py, pr * 3.2, 0, TAU);
    g2.fill();
    const w = hot(c);
    const core = g2.createRadialGradient(px, py, 0, px, py, pr * 0.9);
    core.addColorStop(0, rgba(w, Math.min(1, plum * 1.6)));
    core.addColorStop(0.6, rgba(w, 0.55 * plum));
    core.addColorStop(1, rgba(w, 0));
    g2.fillStyle = core;
    g2.beginPath();
    g2.arc(px, py, pr * 0.9, 0, TAU);
    g2.fill();
  }
}

/**
 * Glare: a bright opening blooms past its own edges, into the air and over
 * the stone, as a real one does to an eye or a camera. Grows with the square
 * of the level, so the resting glow barely blooms and a hit flares.
 */
export function bloom(g2: CanvasRenderingContext2D, a: Aperture, c: Rgb): void {
  const lum = luma(c);
  const b = lum * lum;
  if (b < 0.01) return;
  const rad = a.w * (1.1 + 1.4 * lum);
  const gr = g2.createRadialGradient(a.cx, a.cy, a.w * 0.15, a.cx, a.cy, rad);
  gr.addColorStop(0, rgba(hot(c), 0.4 * b));
  gr.addColorStop(0.4, rgba(hue(c), 0.12 * b));
  gr.addColorStop(1, rgba(hue(c), 0));
  g2.save();
  g2.globalCompositeOperation = "lighter";
  g2.fillStyle = gr;
  g2.beginPath();
  g2.arc(a.cx, a.cy, rad, 0, TAU);
  g2.fill();
  g2.restore();
}
