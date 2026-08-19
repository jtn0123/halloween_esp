/**
 * Fixture close-ups — the three spots drawn as the parts that are in them.
 *
 * The castle canvas is honest about the LOOK (glow, spill, bloom) but blurs a
 * whole fixture into one light. These insets are honest about the DATA: every
 * real pixel as its own dot, in the arrangement it physically has. That is
 * the only view where per-pixel work — centre roles, chases, sparkles,
 * scattered strikes — can actually be reviewed.
 *
 * It used to draw three identical seven-dot Jewels because that is what the
 * castle had. Now it asks the rig (rig.ts) what is in each spot and draws
 * that: a ring with no middle, a stick, a 4×8 matrix, a couple of loose
 * singles. Swapping a fixture in the panel changes this view on the next
 * frame, which is the whole point of choosing one before soldering it.
 *
 * Draws from the same ZoneRender frame the stage consumes; owns one small
 * canvas it creates itself, so no template edit is needed.
 */

import { fixture, loadRig, zoneLayout, ZONE_ORDER, type Layout, type RigState }
  from "./rig.js";
import type { ZoneRender } from "./show.js";
import type { ZoneId } from "./types.js";

/** Laid out the way the castle stands, not the way the zones are declared —
 *  the same stage order the channel strip reads in, so a colour you see here
 *  and a hex you read there are in the same place on the screen. */
const LABEL: Record<ZoneId, string> = {
  towerL: "tower L",
  door: "door · centre",
  towerR: "tower R",
};

/** Bitmap size. The 420:146 ratio is load-bearing — kiosk mode reserves the
 *  row's height from it (see the `--jewel-row` note in main.ts), so changing
 *  it pushes the pixel row below the fold on a wall tablet. */
const W = 420;
const H = 146;
/** Room under each cell for the fixture name and the zone's mean. */
const FOOT = 30;

export class PixelInsets {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private rig: RigState;

  constructor(anchor: HTMLElement, rig: RigState = loadRig()) {
    this.rig = rig;
    this.canvas = document.createElement("canvas");
    this.canvas.id = "jewels";
    // OUTSIDE the .stage box. Inside it, two things go wrong at once:
    // `.stage canvas { height:100% }` stretches the bitmap to the stage's
    // full height (giant blurry ovals), and the stage's fixed aspect-ratio
    // means the extra canvas overflows the panel as a dead black region.
    //
    // Sizing lives in panels.css, not in cssText here: an inline width beats
    // every stylesheet rule, so the phone breakpoint and the Pixels-only view
    // were both quietly losing to a hardcoded 420px.
    (anchor.closest(".stage") ?? anchor).insertAdjacentElement("afterend", this.canvas);
    const ctx = this.canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context for the pixel insets");
    this.ctx = ctx;
    this.retitle();
  }

  /** Follow a rig change on the next frame. */
  setRig(rig: RigState): void {
    this.rig = rig;
    this.retitle();
  }

  private retitle(): void {
    const parts = ZONE_ORDER.map((z) =>
      `${LABEL[z]}: ${fixture(this.rig.zones[z].fixture).name}`);
    const total = ZONE_ORDER.reduce((n, z) => n + zoneLayout(this.rig, z).n, 0);
    this.canvas.title = `${total} real pixels, one dot each — ${parts.join(", ")}`;
  }

  draw(zones: ZoneRender): void {
    if (this.canvas.width !== W) { this.canvas.width = W; this.canvas.height = H; }
    const g = this.ctx;
    g.clearRect(0, 0, W, H);
    g.fillStyle = "#0d0a14";
    g.fillRect(0, 0, W, H);

    const cell = W / ZONE_ORDER.length;
    ZONE_ORDER.forEach((z, zi) => {
      const L = zoneLayout(this.rig, z);
      const fx = fixture(this.rig.zones[z].fixture);
      const box = { x: zi * cell + 6, y: 6, w: cell - 12, h: H - FOOT - 12 };
      const pts = place(L, box);

      if (L.n === 0) {
        g.textAlign = "center";
        g.fillStyle = "#4c4460";
        g.font = "11px system-ui";
        g.fillText("not wired", box.x + box.w / 2, box.y + box.h / 2 + 4);
      } else {
        // Radius from the tightest gap in this cell, so a 32-pixel matrix and
        // a single mini PCB are both legible without per-fixture tuning.
        const rad = Math.min(12, Math.max(2.5, minGap(pts) * 0.45));
        const pix = zones[z].pix;
        // Non-core pixels first, so a centre dot sits on top where they touch.
        for (let p = 0; p < L.n; p++) if (!L.core[p]) this.dot(pts, p, rad, pix);
        for (let p = 0; p < L.n; p++) if (L.core[p]) this.dot(pts, p, rad + 1, pix);
      }

      const cx = box.x + box.w / 2;
      g.textAlign = "center";
      g.fillStyle = "#9a8fb0";
      g.font = "11px system-ui";
      // The spot and what is in it. Reading "door" and having to look
      // somewhere else to learn it is currently a Ring 16 defeats the view.
      g.fillText(`${LABEL[z]} · ${fx.name}`, cx, H - 18);

      // The zone's mean as bytes, under its own fixture. The channel strip
      // says the same thing, but reading a colour and reading its value
      // should not mean looking at two corners of the page.
      const avg = zones[z].avg;
      g.fillStyle = "#6d6480";
      g.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
      g.fillText(avg.slice(0, 3).map((v) =>
        Math.round(Math.min(1, v) * 255).toString(16).padStart(2, "0")).join(""),
      cx, H - 5);
    });
  }

  private dot(
    pts: ReadonlyArray<readonly [number, number]>, p: number, rad: number,
    pix: ReadonlyArray<readonly [number, number, number]>,
  ): void {
    const at = pts[p];
    if (!at) return;
    const [r, g0, b] = pix[p] ?? [0, 0, 0];
    const g = this.ctx;
    const [x, y] = at;

    // A lit pixel blooms; a dark one is still visibly THERE as a socket.
    const lum = Math.max(r, g0, b);
    if (lum > 0.02) {
      const halo = g.createRadialGradient(x, y, rad * 0.3, x, y, rad * 2.2);
      halo.addColorStop(0,
        `rgba(${r * 255 | 0},${g0 * 255 | 0},${b * 255 | 0},${0.55 * lum})`);
      halo.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = halo;
      g.beginPath();
      g.arc(x, y, rad * 2.2, 0, 6.2832);
      g.fill();
    }
    g.fillStyle = `rgb(${Math.min(255, 30 + r * 225) | 0},`
      + `${Math.min(255, 30 + g0 * 225) | 0},${Math.min(255, 30 + b * 225) | 0})`;
    g.beginPath();
    g.arc(x, y, rad, 0, 6.2832);
    g.fill();
    g.strokeStyle = "#2a2138";
    g.stroke();
  }
}

interface Box { x: number; y: number; w: number; h: number }

/**
 * The fixture's unit-square positions, fitted into the cell at its own shape.
 *
 * Stretching the unit square to the box would draw the 4×8 FeatherWing as a
 * squashed 4×8 — the dots would collide horizontally long before they filled
 * the height. So the box is reduced to the fixture's natural aspect first and
 * centred in what is left.
 */
function place(L: Layout, box: Box): ReadonlyArray<readonly [number, number]> {
  if (L.n === 0) return [];
  const aspect = naturalAspect(L);
  const boxAspect = box.w / box.h;
  const w = aspect >= boxAspect ? box.w : box.h * aspect;
  const h = aspect >= boxAspect ? box.w / aspect : box.h;
  const ox = box.x + (box.w - w) / 2;
  const oy = box.y + (box.h - h) / 2;
  return L.pos.map((p) => [ox + (p[0] ?? 0) * w, oy + (p[1] ?? 0) * h] as const);
}

/** Width:height of the fixture as it physically sits, from the spread of its
 *  own pixels. Derived rather than declared so a new layout kind in rig.ts
 *  draws sensibly without also editing this file. */
function naturalAspect(L: Layout): number {
  let x0 = 1, x1 = 0, y0 = 1, y1 = 0;
  for (const p of L.pos) {
    x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]);
    y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]);
  }
  // A single pixel, or a perfectly flat row, has no height to divide by. The
  // floor also stops a stick being drawn as an infinitely wide sliver.
  const dx = Math.max(x1 - x0, 0.08);
  const dy = Math.max(y1 - y0, 0.08);
  return Math.min(6, dx / dy);
}

/** Closest two dots in the cell, which is what a radius has to clear. */
function minGap(pts: ReadonlyArray<readonly [number, number]>): number {
  if (pts.length < 2) return 24;
  let best = Infinity;
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const a = pts[i], b = pts[j];
      if (!a || !b) continue;
      best = Math.min(best, Math.hypot(a[0] - b[0], a[1] - b[1]));
    }
  }
  return Number.isFinite(best) ? best : 24;
}
