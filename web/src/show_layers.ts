/**
 * What cue format version 2 added to a zone — the desk's copy of
 * firmware/castle_layers.h, function for function.
 *
 *  - the ORNAMENT layer: a second strike envelope per zone that the render
 *    ADDS to the first, so a weak hit no longer cuts a strong one short;
 *  - the overlay CLOCK: a chase/meteor head that look records can lock to
 *    the tempo (rate in turns per second, re-anchored on every change so the
 *    head never jumps). Rate 0 is the legacy clock-driven head exactly;
 *  - the rate-aware SOFTEN: a strike is softened only when it lands on its
 *    zone less than SOFTEN_WINDOW_MS after that zone's previous strike —
 *    a flash train faster than three a second. Per zone, never castle-wide.
 *
 * demo/castle-radio/cue-playback.js drives these with card semantics (a
 * strike replaces its layer's flash); show.ts's own fireCues keeps the
 * desk's accumulate semantics. Change this file, castle_layers.h and
 * cue-playback.js together (docs/PARITY.md).
 */

import type { EffectName, Rgbw, ZoneId } from "./types.js";

/** firmware kSoftenWindowMs. */
export const SOFTEN_WINDOW_MS = 333;

/** One zone's v2 state (firmware castle::ZoneExtra). */
export interface ZoneExtra {
  ornFlash: number;
  ornTarget: number;
  ornRise: number;
  ornDecay: number;
  ornCol: Rgbw;
  ornMode: number;
  ornEpoch: number;
  /** Was each layer's current strike part of a train? Decided at fire time;
   *  the soften switch is applied live on top. */
  train0: boolean;
  train1: boolean;
  /** Song-clock ms of the zone's latest strike and the one strictly before
   *  it; -1 = none. Strikes at the same millisecond are one event. */
  lastMs: number;
  prevMs: number;
  /** Overlay clock: 0 = legacy. Else head = head0 + rate * (t - t0). */
  rate: number;
  head0: number;
  t0: number;
}

export const zoneExtra = (): ZoneExtra => ({
  ornFlash: 0, ornTarget: 0, ornRise: 0, ornDecay: 0.9, ornCol: [1, 1, 1, 1],
  ornMode: 0, ornEpoch: 0, train0: false, train1: false, lastMs: -1, prevMs: -1,
  rate: 0, head0: 0, t0: 0,
});

/** Book a strike at song-clock `tMs`; true when it is part of a train. */
export function noteStrike(x: ZoneExtra, tMs: number): boolean {
  if (tMs !== x.lastMs) {
    x.prevMs = x.lastMs;
    x.lastMs = tMs;
  }
  return x.prevMs >= 0 && tMs >= x.prevMs && tMs - x.prevMs < SOFTEN_WINDOW_MS;
}

export const frac1 = (v: number): number => v - Math.floor(v);

/** The legacy head at zone time `tz`: the meteor's phase on a meteor zone
 *  (overlay 3), the chase's head on any other. */
export function legacyHead(overlay: number, tz: number, zi: number): number {
  if (overlay === 3) return (tz / 2.6 + zi * 0.41) % 1;
  return (tz * 0.45 + zi * 0.37) % 1;
}

/** The head applyOverlay is handed at render-clock `t`; -1 = legacy. */
export const overlayHead = (x: ZoneExtra, t: number): number =>
  x.rate > 0 ? frac1(x.head0 + x.rate * (t - x.t0)) : -1;

/** A look's motion half at render-clock `tRec`. `rate` < 0 keeps the rate,
 *  `head` < 0 continues from where the head is. Rate 0 is the legacy clock
 *  again, and a head phase means nothing to it. */
export function setMotion(
  x: ZoneExtra, overlay: number, phase: number, zi: number, tRec: number,
  rate: number, head: number,
): void {
  const next = rate < 0 ? x.rate : rate;
  if (next > 0) {
    const now = x.rate > 0 ? overlayHead(x, tRec) : legacyHead(overlay, tRec + phase, zi);
    x.head0 = head < 0 ? now : head;
    x.t0 = tRec;
  }
  x.rate = next;
}

/** The per-frame decay of any softened-or-not strike (castle_pixels.h). */
export const frameDecay = (decay: number, soft: boolean): number =>
  soft ? 1 - (1 - decay) * 0.35 : decay;

/** The ornament's rise-then-decay for one 16 ms frame — layer 0's
 *  arithmetic (show.ts decayFlashes), on layer 1's fields. */
export function stepOrnament(x: ZoneExtra, soften: boolean): void {
  if (x.ornTarget > 0) {
    x.ornFlash += x.ornRise;
    if (x.ornFlash >= x.ornTarget) {
      x.ornFlash = x.ornTarget;
      x.ornTarget = 0;
    }
    return;
  }
  x.ornFlash *= frameDecay(x.ornDecay, soften && x.train1);
  if (x.ornFlash < 0.004) x.ornFlash = 0;
}

/** A look cue as the card carries it (`op: "look"`, cue format v2). Absent
 *  fields are "keep"; `center: "none"` clears the centre role. Not part of
 *  the `Cue` union: scenes.yaml has no such op, only a card file does. */
export interface LookCue {
  t: number;
  bus?: "LED";
  op: "look";
  targets?: ZoneId[];
  overlay?: string;
  palette?: string;
  center?: EffectName | "none";
  /** Turns per second; 0 = the legacy clock. */
  rate?: number;
  /** Turns, 0..1; absent = continue from where the head is. */
  head?: number;
}
