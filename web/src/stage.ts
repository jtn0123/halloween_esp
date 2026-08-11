/**
 * The stage canvas — castle silhouette at night, with the three lit apertures.
 *
 * This module owns nothing but pixels. It is handed a finished frame (the
 * per-pixel colours the effects produced) plus the current strike level, and
 * draws it; it never reads show state, so the same picture can be produced
 * from a live clock, a scrub, or a still frame in a test.
 *
 * The 800x520 viewBox is fixed. Everything is drawn in those units and the
 * context is scaled to fit the element, so the composition holds at any size.
 */

import type { ZoneId } from "./types.js";
import { hash } from "./effects.js";

/** Screen RGB, 0..1 per channel — what a pixel actually looks like. */
export type Rgb = readonly [r: number, g: number, b: number];

/** One zone's frame: the seven jewel pixels, plus their mean for the glow. */
export interface ZoneFrame {
  /** Seven entries, centre first, then the ring — see the jewel loop below. */
  readonly pix: readonly Rgb[];
  readonly avg: Rgb;
}

/** A whole frame: every zone's pixels. What `Stage.draw` consumes. */
export type ZoneRender = Readonly<Record<ZoneId, ZoneFrame>>;

/** The arch geometry of one lit aperture, in viewBox units. */
export interface Aperture {
  /** Left edge of the opening. */
  x: number;
  w: number;
  /** Bottom of the opening — sill or threshold. */
  base: number;
  /** Where the side walls stop and the arch begins to curve. */
  spring: number;
  /** Crown of the arch. */
  top: number;
  /** Centre of the jewel behind the opening. */
  cx: number;
  cy: number;
}

/**
 * Where each zone's opening sits on the castle. Exported because the light
 * spill, the jewel and anything else that wants to point at a window all have
 * to agree on one set of numbers.
 */
export const APERTURE: Readonly<Record<ZoneId, Aperture>> = {
  towerL: { x: 174, w: 52, base: 292, spring: 250, top: 224, cx: 200, cy: 256 },
  towerR: { x: 574, w: 52, base: 292, spring: 250, top: 224, cx: 600, cy: 256 },
  door:   { x: 362, w: 76, base: 470, spring: 412, top: 366, cx: 400, cy: 424 },
};

const ZONE_IDS: readonly ZoneId[] = ["towerL", "towerR", "door"];

const VW = 800;
const VH = 520;
const TAU = 6.2832;

interface Star {
  x: number;
  y: number;
  /** Radius. */
  r: number;
  /** Twinkle phase, so they do not all pulse together. */
  p: number;
}

/**
 * Stars are seeded from the same hash the effects use, not from Math.random:
 * the sky is then identical every reload, which matters when you are comparing
 * two screenshots of the same cue.
 */
const STARS: readonly Star[] = Array.from({ length: 110 }, (_, i) => ({
  x: hash(i * 3.1) * VW,
  y: hash(i * 7.7 + 5) * 340,
  r: 0.4 + hash(i * 2.3) * 1.0,
  p: hash(i * 11.1) * 6.28,
}));

const rgba = (c: Rgb, a: number): string =>
  `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

const luma = (c: Rgb): number => Math.max(c[0], c[1], c[2]);

export class Stage {
  private readonly cvs: HTMLCanvasElement;
  private readonly g2: CanvasRenderingContext2D;
  /** viewBox units -> backing-store pixels. Recomputed on every resize. */
  private scale = 1;

  constructor(canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("stage: 2D canvas context unavailable");
    this.cvs = canvas;
    this.g2 = ctx;
    new ResizeObserver(() => { this.resize(); }).observe(canvas);
    this.resize();
  }

  /**
   * Match the backing store to the element's CSS box at device resolution,
   * capped at 2x — beyond that the fill-rate cost buys nothing visible.
   */
  private resize(): void {
    const r = this.cvs.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.cvs.width = Math.max(1, Math.round(r.width * dpr));
    this.cvs.height = Math.max(1, Math.round(r.height * dpr));
    this.scale = (r.width / VW) * dpr;
  }

  /** The pointed arch shared by both windows and the door. */
  private archPath(a: Aperture): void {
    const { x, w, base, spring, top } = a;
    const g2 = this.g2;
    g2.beginPath();
    g2.moveTo(x, base);
    g2.lineTo(x, spring);
    g2.quadraticCurveTo(x, top, x + w / 2, top);
    g2.quadraticCurveTo(x + w, top, x + w, spring);
    g2.lineTo(x + w, base);
    g2.closePath();
  }

  /**
   * Draw one frame.
   *
   * `flash` and `flashColor` are the strongest strike anywhere on the castle —
   * the sky and stonework brighten off it, tinted by that strike's colour, so
   * lightning washes blue-white but a crypt heartbeat only breathes a faint
   * red onto the stone. The caller picks the winner; this module just paints.
   */
  draw(out: ZoneRender, ts: number, flash: number, flashColor: readonly number[]): void {
    const g2 = this.g2;
    g2.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    g2.clearRect(0, 0, VW, VH);

    const fl = flash;
    // Default to white so a colourless strike washes neutral, as before.
    const fc0 = flashColor[0] ?? 1;
    const fc1 = flashColor[1] ?? 1;
    const fc2 = flashColor[2] ?? 1;

    this.drawSky(ts, fl, fc0, fc1, fc2);
    this.drawCastle(fl, fc0, fc1, fc2);
    this.drawSpill(out);
    this.drawApertures(out);
    this.drawAtmosphere(ts);
  }

  /** Sky gradient, stars, moon, and the strike wash over all of them. */
  private drawSky(ts: number, fl: number, fc0: number, fc1: number, fc2: number): void {
    const g2 = this.g2;

    // sky
    const sky = g2.createLinearGradient(0, 0, 0, VH);
    sky.addColorStop(0, "#070912");
    sky.addColorStop(0.62, "#141a30");
    sky.addColorStop(1, "#1d2340");
    g2.fillStyle = sky;
    g2.fillRect(0, 0, VW, VH);

    // stars
    g2.save();
    for (const s of STARS) {
      const tw = 0.45 + 0.55 * (0.5 + 0.5 * Math.sin(ts * 1.3 + s.p));
      g2.globalAlpha = tw * 0.75;
      g2.fillStyle = "#cfd6f5";
      g2.beginPath();
      g2.arc(s.x, s.y, s.r, 0, TAU);
      g2.fill();
    }
    g2.restore();

    // moon
    const mg = g2.createRadialGradient(676, 86, 4, 676, 86, 62);
    mg.addColorStop(0, "rgba(220,228,255,.5)");
    mg.addColorStop(1, "rgba(220,228,255,0)");
    g2.fillStyle = mg;
    g2.beginPath();
    g2.arc(676, 86, 62, 0, TAU);
    g2.fill();
    g2.fillStyle = "#dfe4f7";
    g2.beginPath();
    g2.arc(676, 86, 22, 0, TAU);
    g2.fill();
    // The crescent is cut by a second disc in the sky's own mid-tone.
    g2.fillStyle = "#141a30";
    g2.beginPath();
    g2.arc(666, 78, 20, 0, TAU);
    g2.fill();

    // strike sky wash, in the strike's own colour
    if (fl > 0) {
      g2.save();
      g2.globalCompositeOperation = "lighter";
      g2.fillStyle = `rgba(${Math.round(150 * fc0)},${Math.round(170 * fc1)},${Math.round(220 * fc2)},${fl * 0.30})`;
      g2.fillRect(0, 0, VW, VH * 0.8);
      g2.restore();
    }
  }

  /** Towers, keep, crenellations, ground — all one silhouette colour. */
  private drawCastle(fl: number, fc0: number, fc1: number, fc2: number): void {
    const g2 = this.g2;

    const stone = fl > 0
      ? `rgb(${Math.round(5 + fl * 46 * fc0)},${Math.round(7 + fl * 52 * fc1)},${Math.round(14 + fl * 70 * fc2)})`
      : "#05070e";
    g2.fillStyle = stone;

    // towers
    const towers: readonly (readonly [number, number])[] = [[150, 250], [550, 650]];
    for (const [x0, x1] of towers) {
      g2.fillRect(x0, 182, x1 - x0, 300);
      g2.beginPath();
      g2.moveTo(x0 - 12, 182);
      g2.lineTo(x1 + 12, 182);
      g2.lineTo((x0 + x1) / 2, 104);
      g2.closePath();
      g2.fill();
    }

    // keep + crenellations
    g2.fillRect(250, 258, 300, 224);
    for (let i = 0; i < 6; i++) g2.fillRect(252 + i * 50, 238, 30, 22);

    // ground
    g2.fillStyle = "#04060c";
    g2.fillRect(0, 476, VW, VH - 476);
  }

  /** The halo each lit aperture throws onto the stone around it. */
  private drawSpill(out: ZoneRender): void {
    const g2 = this.g2;
    g2.save();
    g2.globalCompositeOperation = "lighter";
    for (const id of ZONE_IDS) {
      const a = APERTURE[id];
      const c = out[id].avg;
      const lum = luma(c);
      if (lum < 0.01) continue;
      const rad = id === "door" ? 190 : 140;
      const gr = g2.createRadialGradient(a.cx, a.cy, 2, a.cx, a.cy, rad);
      gr.addColorStop(0, rgba(c, 0.50 * lum));
      gr.addColorStop(0.45, rgba(c, 0.13 * lum));
      gr.addColorStop(1, rgba(c, 0));
      g2.fillStyle = gr;
      g2.beginPath();
      g2.arc(a.cx, a.cy, rad, 0, TAU);
      g2.fill();
    }
    g2.restore();
  }

  /** The openings themselves: dark pane, wash, then the jewel behind it. */
  private drawApertures(out: ZoneRender): void {
    const g2 = this.g2;
    for (const id of ZONE_IDS) {
      const a = APERTURE[id];
      const zone = out[id];
      this.archPath(a);
      g2.fillStyle = "#02030a";
      g2.fill();

      // A faint pane wash from the average, then the jewel itself: one pixel
      // at centre, six around — the same layout as the physical part, so
      // motion across the jewel reads on screen the way it will in the window.
      const lum = luma(zone.avg);
      if (lum > 0.005) {
        const ig = g2.createLinearGradient(0, a.top, 0, a.base);
        ig.addColorStop(0, rgba(zone.avg, 0.40));
        ig.addColorStop(1, rgba(zone.avg, 0.16));
        g2.fillStyle = ig;
        g2.fill();
      }
      g2.save();
      this.archPath(a);
      g2.clip();
      g2.globalCompositeOperation = "lighter";
      const jr = a.w * 0.26;             // ring radius
      const pr = a.w * 0.15;             // pixel dot radius
      for (let p = 0; p < 7; p++) {
        const ang = (p - 1) * (Math.PI / 3) - Math.PI / 2;
        const px = p === 0 ? a.cx : a.cx + jr * Math.cos(ang);
        const py = p === 0 ? a.cy : a.cy + jr * Math.sin(ang);
        // A short frame is a caller bug, but skipping beats throwing mid-paint.
        const c = zone.pix[p];
        if (!c) continue;
        const plum = luma(c);
        if (plum < 0.01) continue;
        const pg = g2.createRadialGradient(px, py, 0.5, px, py, pr * 2.6);
        pg.addColorStop(0, rgba(c, 0.95));
        pg.addColorStop(0.4, rgba(c, 0.45 * plum));
        pg.addColorStop(1, rgba(c, 0));
        g2.fillStyle = pg;
        g2.beginPath();
        g2.arc(px, py, pr * 2.6, 0, TAU);
        g2.fill();
      }
      g2.restore();
    }
  }

  /** Ground fog and the vignette that closes the frame. */
  private drawAtmosphere(ts: number): void {
    const g2 = this.g2;

    // ground fog
    g2.save();
    g2.globalCompositeOperation = "lighter";
    for (let i = 0; i < 3; i++) {
      const fx = 200 + i * 210 + Math.sin(ts * 0.13 + i) * 34;
      const fg = g2.createRadialGradient(fx, 486, 4, fx, 486, 170);
      fg.addColorStop(0, "rgba(120,132,180,.11)");
      fg.addColorStop(1, "rgba(120,132,180,0)");
      g2.fillStyle = fg;
      g2.beginPath();
      g2.ellipse(fx, 486, 170, 42, 0, 0, TAU);
      g2.fill();
    }
    g2.restore();

    // vignette
    const vg = g2.createRadialGradient(VW / 2, VH / 2, VH * 0.35, VW / 2, VH / 2, VH * 0.95);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,.55)");
    g2.fillStyle = vg;
    g2.fillRect(0, 0, VW, VH);
  }
}
