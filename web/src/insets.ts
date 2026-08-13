/**
 * Jewel close-ups — three schematic 7-pixel views under the stage.
 *
 * The castle canvas is honest about the LOOK (glow, spill, bloom) but blurs
 * the jewel into one light. These insets are honest about the DATA: each of
 * the 21 pixels as its own dot, centre + 6-ring laid out like the physical
 * NeoPixel Jewel. With only one pixel soldered so far, this is the only view
 * where per-pixel work — roles, chases, sparkles, scattered strikes — can
 * actually be reviewed.
 *
 * Draws from the same ZoneRender frame the stage consumes; owns one small
 * canvas it creates itself, so no template edit is needed.
 */

import type { ZoneRender } from "./show.js";

const ZONES: ReadonlyArray<{ id: "towerL" | "towerR" | "door"; label: string }> = [
  { id: "towerL", label: "tower L" },
  { id: "towerR", label: "tower R" },
  { id: "door", label: "door" },
];

/** Ring pixel angles: pixel 1 at 12 o'clock, then clockwise — matches how
 *  the jewels hang in the windows. */
const angle = (ringIndex: number): number => -Math.PI / 2 + (ringIndex * Math.PI) / 3;

export class JewelInsets {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;

  constructor(anchor: HTMLElement) {
    this.canvas = document.createElement("canvas");
    this.canvas.id = "jewels";
    this.canvas.title = "The 21 real pixels, one dot each — centre + ring per jewel";
    // OUTSIDE the .stage box, and with an explicit height. Inside it, two
    // things go wrong at once: `.stage canvas { height:100% }` stretches the
    // 420x132 bitmap to the stage's full height (giant blurry ovals), and
    // the stage's fixed aspect-ratio means the extra canvas overflows the
    // panel as a dead black region.
    this.canvas.style.cssText =
      "display:block;width:100%;max-width:420px;height:auto;" +
      "aspect-ratio:420/132;margin:.4rem auto 0;";
    (anchor.closest(".stage") ?? anchor).insertAdjacentElement("afterend", this.canvas);
    const ctx = this.canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context for jewel insets");
    this.ctx = ctx;
  }

  draw(zones: ZoneRender): void {
    const W = 420, H = 132;
    if (this.canvas.width !== W) { this.canvas.width = W; this.canvas.height = H; }
    const g = this.ctx;
    g.clearRect(0, 0, W, H);
    g.fillStyle = "#0d0a14";
    g.fillRect(0, 0, W, H);

    ZONES.forEach((z, zi) => {
      const cx = 70 + zi * 140;
      const cy = 58;
      const pix = zones[z.id].pix;
      // Ring first so the centre dot sits on top where they touch.
      for (let p = 1; p < 7; p++) {
        const [r, gg, b] = pix[p] ?? [0, 0, 0];
        const a = angle(p - 1);
        this.dot(cx + Math.cos(a) * 30, cy + Math.sin(a) * 30, 11, r, gg, b);
      }
      const [r, gg, b] = pix[0] ?? [0, 0, 0];
      this.dot(cx, cy, 12, r, gg, b);

      g.fillStyle = "#9a8fb0";
      g.font = "11px system-ui";
      g.textAlign = "center";
      g.fillText(z.label, cx, H - 12);
    });
  }

  private dot(x: number, y: number, rad: number, r: number, g0: number, b: number): void {
    const g = this.ctx;
    // A lit pixel blooms; a dark one is still visibly THERE as a socket.
    const lum = Math.max(r, g0, b);
    if (lum > 0.02) {
      const halo = g.createRadialGradient(x, y, rad * 0.3, x, y, rad * 2.2);
      halo.addColorStop(0, `rgba(${r * 255 | 0},${g0 * 255 | 0},${b * 255 | 0},${0.55 * lum})`);
      halo.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = halo;
      g.beginPath();
      g.arc(x, y, rad * 2.2, 0, 6.2832);
      g.fill();
    }
    g.fillStyle = `rgb(${Math.min(255, 30 + r * 225) | 0},${Math.min(255, 30 + g0 * 225) | 0},${Math.min(255, 30 + b * 225) | 0})`;
    g.beginPath();
    g.arc(x, y, rad, 0, 6.2832);
    g.fill();
    g.strokeStyle = "#2a2138";
    g.stroke();
  }
}
