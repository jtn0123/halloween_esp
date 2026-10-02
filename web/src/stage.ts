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
import { DEFAULT_RIG, zoneLayout, type Layout } from "./rig.js";
import { bloom, hue, interior, leds, luma, rgba, stoneMask } from "./stage_light.js";

/** Screen RGB, 0..1 per channel — what a pixel actually looks like. */
export type Rgb = readonly [r: number, g: number, b: number];

/** One zone's frame: the fixture pixels, plus their mean for the glow. */
export interface ZoneFrame {
  /** One entry per configured pixel, in fixture order. */
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

/** The two towers' walls, left and right edge. */
const TOWERS: readonly (readonly [number, number])[] = [[150, 250], [550, 650]];
/** Where the ground starts. */
const GROUND = 476;

export class Stage {
  private readonly cvs: HTMLCanvasElement;
  private readonly g2: CanvasRenderingContext2D;
  /** viewBox units -> backing-store pixels. Recomputed on every resize. */
  private scale = 1;
  /** The stonework mask at the current backing size (stage_light.stoneMask). */
  private stone: HTMLCanvasElement | null = null;
  private layouts: Record<ZoneId, Layout> = {
    towerL: zoneLayout(DEFAULT_RIG, "towerL"),
    door: zoneLayout(DEFAULT_RIG, "door"),
    towerR: zoneLayout(DEFAULT_RIG, "towerR"),
  };

  setLayouts(layouts: Record<ZoneId, Layout>): void { this.layouts = layouts; }

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
    this.stone = null; // rebuilt at the new size on the next frame
  }

  /** Every surface of the castle a window can light, as one path to clip to. */
  private castlePath(): void {
    const g2 = this.g2;
    g2.beginPath();
    for (const [x0, x1] of TOWERS) {
      g2.rect(x0, 182, x1 - x0, GROUND - 182);
      g2.moveTo(x0 - 12, 182);
      g2.lineTo(x1 + 12, 182);
      g2.lineTo((x0 + x1) / 2, 104);
      g2.closePath();
    }
    g2.rect(250, 258, 300, GROUND - 258);
    for (let i = 0; i < 6; i++) g2.rect(252 + i * 50, 238, 30, 22);
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
    this.drawStone();
    this.drawApertures(out);
    for (const id of ZONE_IDS) bloom(g2, APERTURE[id], out[id].avg);
    this.drawAtmosphere(ts, out);
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
    for (const [x0, x1] of TOWERS) {
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
    g2.fillRect(0, GROUND, VW, VH - GROUND);

    // A sill under each window and a step under the door, to catch the light.
    g2.fillStyle = stone;
    for (const id of ZONE_IDS) {
      const a = APERTURE[id];
      g2.fillRect(a.x - 6, a.base, a.w + 12, id === "door" ? 6 : 5);
    }
  }

  /**
   * The light each aperture throws: a wash on the stone around it (clipped
   * to the castle, so the sky beside a tower stays dark), leaning downward
   * the way light out of a window falls, and a pool on the ground in front
   * of the door.
   */
  private drawSpill(out: ZoneRender): void {
    const g2 = this.g2;
    g2.save();
    this.castlePath();
    g2.clip();
    g2.globalCompositeOperation = "lighter";
    for (const id of ZONE_IDS) {
      const a = APERTURE[id];
      const lum = luma(out[id].avg);
      if (lum < 0.01) continue;
      const h = hue(out[id].avg);
      const rad = id === "door" ? 140 : 100;
      g2.save();
      g2.translate(a.cx, a.cy + rad * 0.12);
      g2.scale(1, 1.25);
      const gr = g2.createRadialGradient(0, 0, a.w * 0.3, 0, 0, rad);
      // Falling off near the square of the distance, as real light does.
      gr.addColorStop(0, rgba(h, 0.6 * lum));
      gr.addColorStop(0.25, rgba(h, 0.2 * lum));
      gr.addColorStop(0.6, rgba(h, 0.05 * lum));
      gr.addColorStop(1, rgba(h, 0));
      g2.fillStyle = gr;
      g2.fillRect(-rad, -rad, rad * 2, rad * 2);
      g2.restore();
    }
    g2.restore();

    const door = out.door.avg;
    const lum = luma(door);
    if (lum < 0.01) return;
    const h = hue(door);
    g2.save();
    g2.beginPath();
    g2.rect(0, GROUND, VW, VH - GROUND);
    g2.clip();
    g2.globalCompositeOperation = "lighter";
    g2.translate(APERTURE.door.cx, GROUND + 4);
    g2.scale(1, 0.22);
    const pool = g2.createRadialGradient(0, 0, 0, 0, 0, 170);
    pool.addColorStop(0, rgba(h, 0.55 * lum));
    pool.addColorStop(0.5, rgba(h, 0.16 * lum));
    pool.addColorStop(1, rgba(h, 0));
    g2.fillStyle = pool;
    g2.fillRect(-170, -170, 340, 340);
    g2.restore();
  }

  /** The stonework, multiplied over the castle: it shows only where lit. */
  private drawStone(): void {
    const g2 = this.g2;
    if (!(this.scale > 0)) return; // hidden: nothing to texture
    this.stone ??= stoneMask(this.cvs.width, this.cvs.height, this.scale);
    g2.save();
    this.castlePath();
    g2.clip();
    g2.globalCompositeOperation = "multiply";
    g2.drawImage(this.stone, 0, 0, VW, VH);
    g2.restore();
  }

  /**
   * The openings: a dark pane, the room behind it lit by the fixture, every
   * configured pixel as a real emitter, and the reveal and sill the light
   * catches on its way out. Rings have no invented centre; grids and sticks
   * keep their layout.
   */
  private drawApertures(out: ZoneRender): void {
    const g2 = this.g2;
    for (const id of ZONE_IDS) {
      const a = APERTURE[id];
      const zone = out[id];
      this.archPath(a);
      g2.fillStyle = "#02030a";
      g2.fill();
      const lum = luma(zone.avg);
      g2.save();
      this.archPath(a);
      g2.clip();
      g2.globalCompositeOperation = "lighter";
      if (lum > 0.005) interior(g2, a, zone.avg, lum);
      leds(g2, a, this.layouts[id], zone.pix);
      g2.restore();
      if (lum > 0.005) this.drawReveal(a, zone.avg, lum);
    }
  }

  /** The inner edge of the arch and the top of the sill, rim-lit. */
  private drawReveal(a: Aperture, c: Rgb, lum: number): void {
    const g2 = this.g2;
    const h = hue(c);
    g2.save();
    g2.globalCompositeOperation = "lighter";
    this.archPath(a);
    g2.strokeStyle = rgba(h, 0.15 * lum);
    g2.lineWidth = 2.2;
    g2.stroke();
    g2.fillStyle = rgba(h, 0.35 * lum);
    g2.fillRect(a.x - 6, a.base, a.w + 12, 1.4);
    g2.restore();
  }

  /** Ground fog — tinted where the door lights it — and the vignette. */
  private drawAtmosphere(ts: number, out: ZoneRender): void {
    const g2 = this.g2;
    const door = out.door.avg;
    const dl = luma(door);

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
      const lit = dl * Math.max(0, 1 - Math.abs(fx - APERTURE.door.cx) / 320);
      if (lit < 0.01) continue;
      const tg = g2.createRadialGradient(fx, 486, 4, fx, 486, 170);
      tg.addColorStop(0, rgba(hue(door), 0.2 * lit));
      tg.addColorStop(1, rgba(hue(door), 0));
      g2.fillStyle = tg;
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
