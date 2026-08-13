/**
 * The clip editor's canvas — pixels only.
 *
 * Split out of waveform.ts for the same reason onsets.ts is split out of
 * tracks.ts: the 500-line ceiling, and a seam that happens to be clean. Nothing
 * in here touches the server, the audio element or the Tracks inputs. It is
 * handed peaks, onsets, a selection and a playhead, and it draws them.
 *
 * Everything below is in CSS pixels. The backing store is scaled to the device
 * ratio once per resize, the same way stage.ts does it, so the drawing code
 * never has to think about retina.
 */

import { BAND_INK, type BandName } from "./bands.js";

export { BAND_INK };
export type { BandName };

/** `[time in seconds, strength 0..1]` — the same pair analyze.py emits. */
export type Onset = readonly [seconds: number, velocity: number];

/** A band with no hits is simply absent, so every entry is optional. */
export type WaveOnsets = Partial<Record<BandName, readonly Onset[]>>;

/** One `GET /api/waveform/<id>` body, once it has been checked over. */
export interface WaveData {
  id: string;
  /** Seconds. */
  duration: number;
  /** Absolute peak per bucket, 0..1, roughly a thousand of them. */
  peaks: readonly number[];
  onsets: WaveOnsets;
  /**
   * Full-range loudness over time, [seconds, level 0..1] at ~6 Hz. Near-silent
   * points are omitted at the source, so a gap reads as quiet. Optional: an
   * older studio, or static mode before analysis, simply has none.
   */
  env?: ReadonlyArray<readonly [number, number]>;
}

/** In and out points, in seconds. */
export interface WaveClip {
  start: number;
  end: number;
}

/** Top to bottom, so the lanes read like a spectrum with bass at the floor. */
const BAND_ORDER: readonly BandName[] = ["onset_high", "onset_mid", "onset_low"];

/** One onset lane, CSS px. */
const LANE = 9;
const LANES = LANE * BAND_ORDER.length;

/** Grab-handle width, and with it how close to an edge counts as grabbing it. */
export const EDGE_SLOP = 7;

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

interface Palette { accent: string; line: string; panel: string; ink: string }

/**
 * The dark theme's own values are the fallbacks. They are only reached before
 * the canvas is in the document, when custom properties resolve to nothing.
 */
const readPalette = (el: Element): Palette => {
  const cs = getComputedStyle(el);
  const v = (name: string, fallback: string): string =>
    cs.getPropertyValue(name).trim() || fallback;
  return {
    accent: v("--accent", "#ff9d3c"),
    line: v("--line-2", "#333a52"),
    panel: v("--panel-2", "#171c2c"),
    ink: v("--ink-2", "#a9a4bd"),
  };
};

export class WaveView {
  readonly el: HTMLCanvasElement;
  private readonly g2: CanvasRenderingContext2D;
  private readonly ro: ResizeObserver;
  private pal: Palette;
  private dpr = 1;
  private w = 1;
  private h = 1;

  /** Null until a track has been analysed; `message` is shown instead. */
  data: WaveData | null = null;
  clip: WaveClip | null = null;
  /**
   * Loudness tiers as the audition will play them: [absolute seconds, tier]
   * with tier 0 hush / 1 verse / 2 chorus. Computed by the host from the SAME
   * sections() the scene uses — this overlay is a promise about the show, so
   * it must not have its own opinion. Null: no tinting.
   */
  sections: Array<[number, number]> | null = null;
  /** Bands muted in the band editor — their lanes draw dimmed. */
  muted: Partial<Record<BandName, boolean>> = {};
  /** Audition position in seconds, or null when stopped. */
  playhead: number | null = null;
  /** Stands in for the waveform — "no track selected", a failed fetch, etc. */
  message = "";

  constructor() {
    const el = document.createElement("canvas");
    el.style.cssText = "display:block;width:100%;height:132px;cursor:crosshair;"
      // A pointer drag across the canvas is the selection gesture, so the
      // browser must not also read it as a scroll of the panel behind it.
      + "touch-action:none;border-radius:6px";
    const ctx = el.getContext("2d");
    if (!ctx) throw new Error("waveform: 2D canvas context unavailable");
    this.el = el;
    this.g2 = ctx;
    this.pal = readPalette(el);
    this.ro = new ResizeObserver(() => { this.resize(); this.draw(); });
    this.ro.observe(el);
  }

  /**
   * Match the backing store to the element's CSS box at device resolution,
   * capped at 2x — beyond that the fill rate buys nothing visible.
   */
  private resize(): void {
    const r = this.el.getBoundingClientRect();
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = Math.max(1, Math.round(r.width));
    this.h = Math.max(1, Math.round(r.height));
    this.el.width = Math.max(1, Math.round(this.w * this.dpr));
    this.el.height = Math.max(1, Math.round(this.h * this.dpr));
    // The theme's custom properties only resolve once we are in the document,
    // and a resize is the cheapest reliable moment to notice that we now are.
    this.pal = readPalette(this.el);
  }

  /** Seconds -> CSS x. Shared with the pointer code so both agree exactly. */
  secToX(s: number): number {
    const d = this.data?.duration ?? 0;
    return d > 0 ? (s / d) * this.w : 0;
  }

  /** CSS x -> seconds, clamped to the track. */
  xToSec(x: number): number {
    const d = this.data?.duration ?? 0;
    return d > 0 ? clamp(x / this.w, 0, 1) * d : 0;
  }

  /** Where a pointer event landed, in this canvas's own CSS pixels. */
  eventX(e: PointerEvent): number {
    return e.clientX - this.el.getBoundingClientRect().left;
  }

  draw(): void {
    const g2 = this.g2;
    g2.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g2.clearRect(0, 0, this.w, this.h);
    g2.fillStyle = this.pal.panel;
    g2.fillRect(0, 0, this.w, this.h);

    if (!this.data) {
      g2.fillStyle = this.pal.ink;
      g2.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
      g2.textAlign = "center";
      g2.textBaseline = "middle";
      g2.fillText(this.message, this.w / 2, this.h / 2, this.w - 16);
      return;
    }

    this.drawSections();
    this.drawWave();
    this.drawOnsets();
    this.drawClip();
    this.drawPlayhead();
  }

  /** Hush/verse/chorus washes behind the wave, plus a solid strip up top. */
  private drawSections(): void {
    const segs = this.sections, d = this.data;
    if (!segs || !segs.length || !d) return;
    const g2 = this.g2, h = this.h - LANES - 2;
    // Hush stays unpainted — quiet reading as "nothing here" is correct.
    const WASH = ["", "rgba(255,157,60,0.10)", "rgba(255,84,60,0.16)"];
    const STRIP = ["rgba(90,140,255,0.55)", "rgba(255,157,60,0.75)",
                   "rgba(255,84,60,0.9)"];
    segs.forEach(([sec, tier], i) => {
      const x0 = this.secToX(sec);
      const x1 = this.secToX(segs[i + 1]?.[0] ?? d.duration);
      if (WASH[tier]) {
        g2.fillStyle = WASH[tier]!;
        g2.fillRect(x0, 0, x1 - x0, h);
      }
      g2.fillStyle = STRIP[tier] ?? STRIP[1]!;
      g2.fillRect(x0, 0, x1 - x0, 3);
    });
  }

  /** The peaks, mirrored about the midline as one filled shape. */
  private drawWave(): void {
    const d = this.data;
    if (!d || d.peaks.length === 0) return;
    const g2 = this.g2, w = this.w, n = d.peaks.length;
    const mid = (this.h - LANES - 2) / 2;

    // One column per CSS pixel, taking the loudest bucket that falls in it —
    // averaging would quietly file the transients off, which are the whole
    // point of looking at this picture.
    const cols = new Float32Array(w);
    for (let x = 0; x < w; x++) {
      const a = Math.floor(x * n / w);
      const b = Math.max(a + 1, Math.floor((x + 1) * n / w));
      let p = 0;
      for (let i = a; i < b && i < n; i++) p = Math.max(p, d.peaks[i] ?? 0);
      cols[x] = p;
    }

    g2.fillStyle = this.pal.line;
    g2.beginPath();
    g2.moveTo(0, mid);
    const amp = (x: number): number => Math.max(0.6, (cols[x] ?? 0) * mid * 0.94);
    for (let x = 0; x < w; x++) g2.lineTo(x, mid - amp(x));
    for (let x = w - 1; x >= 0; x--) g2.lineTo(x, mid + amp(x));
    g2.closePath();
    g2.fill();
  }

  /** Ticks in per-band lanes, plus a wash of each one up over the waveform. */
  private drawOnsets(): void {
    const d = this.data;
    if (!d) return;
    const g2 = this.g2, waveH = this.h - LANES - 2;
    BAND_ORDER.forEach((band, row) => {
      const hits = d.onsets[band];
      if (!hits) return;
      const top = waveH + 2 + row * LANE;
      // A muted band's lane fades rather than vanishes: the hits are still
      // detected and will still export — only the audition skips them.
      const dim = this.muted[band] ? 0.22 : 1;
      g2.fillStyle = BAND_INK[band];
      for (const [sec, vel] of hits) {
        const x = Math.round(this.secToX(sec));
        const v = clamp(vel, 0, 1);
        // The lane tick says when and how hard; the faint full-height line says
        // against what, which is the judgement being asked of the user here.
        g2.globalAlpha = (0.10 + 0.30 * v) * dim;
        g2.fillRect(x, 0, 1, waveH);
        g2.globalAlpha = dim;
        const tick = 3 + Math.round(v * (LANE - 4));
        g2.fillRect(x - 1, top + (LANE - 1 - tick), 2, tick);
      }
      g2.globalAlpha = 1;
    });
  }

  /** The selection: everything outside it knocked back, edges and handles. */
  private drawClip(): void {
    const c = this.clip;
    if (!c) return;
    const g2 = this.g2, h = this.h;
    const x0 = this.secToX(c.start), x1 = this.secToX(c.end);

    // Dimming the outside rather than lighting the inside keeps the wave and
    // the onsets at full contrast exactly where you are looking.
    g2.fillStyle = this.pal.panel;
    g2.globalAlpha = 0.62;
    g2.fillRect(0, 0, x0, h);
    g2.fillRect(x1, 0, this.w - x1, h);
    g2.fillStyle = this.pal.accent;
    g2.globalAlpha = 0.10;
    g2.fillRect(x0, 0, x1 - x0, h);
    g2.globalAlpha = 1;

    for (const x of [x0, x1]) {
      g2.fillRect(x - 1, 0, 2, h);
      g2.fillRect(x - EDGE_SLOP / 2, 0, EDGE_SLOP, 16);   // the grab handle
    }
  }

  private drawPlayhead(): void {
    if (this.playhead === null) return;
    const g2 = this.g2;
    g2.fillStyle = this.pal.ink;
    g2.fillRect(Math.round(this.secToX(this.playhead)), 0, 2, this.h);
  }

  destroy(): void {
    this.ro.disconnect();
    this.el.remove();
  }
}
