// Castle effect engine.
//
// Port of the effect vocabulary in previewer/castle-cue-desk.html. Kept in a
// header rather than inline lambdas so the YAML stays readable and the maths
// can be reasoned about in one place.
//
// Effect indices are generated into firmware/generated/scenes.yaml by
// tools/gen_esphome.py — the EFFECTS enum below is the contract between them.

#pragma once

#include <cmath>
#include <cstdint>

namespace castle {

enum Effect : int {
  EFF_OFF = 0,
  EFF_CANDLE = 1,
  EFF_EMBER = 2,
  EFF_FURNACE = 3,
  EFF_SPIRIT = 4,
  EFF_EYES = 5,
  EFF_SEANCE = 6,
  EFF_WISP = 7,
  EFF_MANSION = 8,
  EFF_CHILL = 9,
  EFF_THROB = 10,
  EFF_STROBE = 11,
  EFF_BLOOD = 12,
  // Not an effect: one past the last id. The host parity harness draws its
  // random effect from this instead of a typed "13", so a fourteenth effect
  // is exercised the day it lands rather than silently never (grade report
  // 2026-09-06 D4). tests/test_pulse_dynamics_parity.py holds it equal to
  // the vocabulary's length.
  EFF_COUNT = 13,
};

struct Rgbw {
  float r, g, b, w;
};

// ── Noise primitives ────────────────────────────────────────────────────
// Every random-looking thing the castle does — the flame, a blink, a glint, a
// scatter strike — comes from ONE integer hash over INTEGER coordinates, and
// web/src/effects.ts runs the very same bit operations. That is what lets the
// browser draw the porch frame for frame: the old frac(sin(n*127.1)*43758.5)
// could not be computed to the same digits in float32 and double, so the desk
// and the castle only ever agreed about the distribution, never the frame.
//
// mix32 is lowbias32 (Chris Wellons): a full-avalanche 32-bit bijection, so
// consecutive lattice cells and neighbouring pixels land anywhere in 0..1.
// The result keeps 24 bits, which a float32 holds exactly — the same bits
// the desk's double sees. Inputs are cast through int32 (two's complement on
// the wire, `| 0` in JS); the arguments the effects reach stay far inside
// that range (a week of uptime at the fastest flame is a few million).
inline uint32_t mix32(uint32_t x) {
  x ^= x >> 16;
  x *= 0x7feb352dU;
  x ^= x >> 15;
  x *= 0x846ca68bU;
  x ^= x >> 16;
  return x;
}

inline float unit01(uint32_t h) { return (float) (h >> 8) * (1.0f / 16777216.0f); }

// Noise at one lattice point (the vnoise cell index).
inline float hashi(int32_t i) { return unit01(mix32((uint32_t) i)); }

// Noise at a triple of small integer coordinates — a time cell, a pixel and
// a zone for the sparkle; a pixel, a zone and a strike epoch for the scatter.
inline float hash3(int32_t a, int32_t b, int32_t c) {
  return unit01(mix32(mix32(mix32((uint32_t) a) + (uint32_t) b) + (uint32_t) c));
}

// Smoothed value noise. A flame flickers coherently; per-frame random reads as
// a loose connection, which is why this is interpolated rather than sampled.
inline float vnoise(float x) {
  int32_t i = (int32_t) floorf(x);
  float f = x - (float) i;
  float u = f * f * (3.0f - 2.0f * f);
  return hashi(i) * (1.0f - u) + hashi(i + 1) * u;
}

inline float fbm(float x) {
  return 0.55f * vnoise(x) + 0.30f * vnoise(x * 2.13f + 11.3f) +
         0.15f * vnoise(x * 4.31f + 27.7f);
}

// Palettes — pole pairs the crossfade effects sweep between. Same table,
// same order, as web/src/effects.ts. Index 0 is the classic haunt look.
constexpr float PALETTES[4][2][3] = {
    {{0.66f, 0.08f, 1.00f}, {0.14f, 1.00f, 0.42f}},   // haunt
    {{0.72f, 0.08f, 0.00f}, {1.00f, 0.55f, 0.05f}},   // ember
    {{0.10f, 0.22f, 0.85f}, {0.72f, 0.85f, 1.00f}},   // moonlight
    {{0.05f, 0.90f, 0.10f}, {0.85f, 1.00f, 0.05f}},   // toxic
};

inline Rgbw mix_pal(float k, float level, int pal = 0) {
  if (pal < 0 || pal > 3) pal = 0;
  const float *a = PALETTES[pal][0];
  const float *b = PALETTES[pal][1];
  k = fminf(1.0f, fmaxf(0.0f, k));
  return Rgbw{(a[0] + (b[0] - a[0]) * k) * level,
              (a[1] + (b[1] - a[1]) * k) * level,
              (a[2] + (b[2] - a[2]) * k) * level, 0.0f};
}

// `seed` varies per pixel so a flame moves ACROSS the jewel rather than the
// whole window pulsing as one lamp. That spatial motion is most of the realism.
//
// `hue` biases the mansion crossfade violet<->green.
// `soft` damps hard strobing — ~7 Hz white strobe is a photosensitivity risk.
inline Rgbw render(int eff, float t, float seed, float hue, bool soft, int pal = 0) {
  switch (eff) {
    case EFF_CANDLE: {
      float n = fbm(t * 1.4f + seed * 3.7f);
      float l = fmaxf(0.0f, 1.0f - 0.55f * (1.0f - n));
      // Warm white carries the body; red tints it toward flame.
      return Rgbw{0.34f * l, 0.05f * l, 0.0f, 1.00f * l};
    }
    case EFF_EMBER: {
      float n = fbm(t * 0.63f + seed * 2.2f);
      float l = 0.22f + 0.16f * n;
      return Rgbw{0.40f * l, 0.06f * l, 0.0f, 0.85f * l};
    }
    case EFF_FURNACE: {
      float n = fbm(t * 2.5f + seed * 0.9f);
      float l = 0.80f + 0.20f * n;
      return Rgbw{1.00f * l, 0.22f * l, 0.02f * l, 0.55f * l};
    }
    case EFF_SPIRIT: {
      float b = 0.5f + 0.5f * sinf(t * 1.15f + seed * 0.8f);
      float l = 0.22f + 0.42f * b;
      return Rgbw{0.10f * l, 1.00f * l, 0.66f * l, 0.0f};
    }
    case EFF_EYES: {
      float blink = vnoise(t * 1.9f + seed * 0.55f) > 0.82f ? 0.10f : 1.0f;
      float l = (0.55f + 0.28f * sinf(t * 3.1f)) * blink;
      return Rgbw{1.00f * l, 0.05f * l, 0.03f * l, 0.0f};
    }
    case EFF_SEANCE: {
      float b = 0.5f + 0.5f * sinf(t * 0.80f + seed * 0.6f);
      return mix_pal(0.0f, 0.24f + 0.52f * b, pal);
    }
    case EFF_WISP: {
      float n = fbm(t * 2.1f + seed * 5.3f);
      float l = fmaxf(0.0f, 0.18f + 0.82f * n - 0.14f);
      return mix_pal(1.0f, l, pal);
    }
    case EFF_MANSION: {
      float sweep = 0.5f + 0.5f * sinf(t * 0.38f + seed * 0.7f);
      float shimmer = 0.84f + 0.16f * fbm(t * 1.05f + seed * 2.7f);
      return mix_pal(sweep * 0.8f + (hue - 0.5f) * 0.9f, 0.62f * shimmer, pal);
    }
    case EFF_CHILL: {
      float b = 0.5f + 0.5f * sinf(t * 0.50f + seed * 1.1f);
      return mix_pal(hue * 0.35f, 0.14f + 0.16f * b, pal);
    }
    case EFF_THROB: {
      float p = 0.5f + 0.5f * sinf(t * 7.4f + seed * 0.4f);
      p *= p;
      return mix_pal(hue * 0.5f, 0.20f + 0.80f * p, pal);
    }
    case EFF_STROBE: {
      if (soft) {
        float l = 0.34f + 0.44f * (0.5f + 0.5f * sinf(t * 3.1f + seed));
        return Rgbw{0.10f * l, 0.10f * l, 0.14f * l, 1.00f * l};
      }
      float on = sinf(t * 44.0f + seed) > 0.0f ? 1.0f : 0.06f;
      return Rgbw{0.12f * on, 0.12f * on, 0.18f * on, 1.00f * on};
    }
    case EFF_BLOOD: {
      // Near-dark deep red smoulder — the floor under the heartbeat pulses
      // in the crypt. Slow uneven breathing, never bright, no white at all.
      float n = fbm(t * 0.35f + seed * 1.7f);
      float l = 0.045f + 0.05f * n;
      return Rgbw{1.00f * l, 0.02f * l, 0.01f * l, 0.0f};
    }
    case EFF_OFF:
    default:
      return Rgbw{0.0f, 0.0f, 0.0f, 0.0f};
  }
}

// ── Fixture geometry ────────────────────────────────────────────────────
// What is actually in a window: how many pixels, which one is the middle,
// and normalised coordinates per pixel — where it sits around the loop a
// chase travels, how far down the path a meteor falls, and (v5.71) where
// the desk DRAWS it, x left to right and y top to bottom, which is what the
// left/right/top/bottom strike masks split on.
//
// The tables are GENERATED into generated/rig.h from tools/rig_layout.py, so
// there is no layout arithmetic on the device at all. That is deliberate:
// the browser (web/src/rig.ts) and the generator both compute this geometry,
// and web/test/rig_parity.mjs proves they agree — a third hand-written copy
// here would be a third thing to keep in step, and the one nobody can test.
struct Fixture {
  int n;
  int center;        // -1 where the fixture has no middle (a bare ring)
  int fall_steps;    // distinct heights, which sets how tall a drip is
  const float *walk;
  const float *fall;
  const bool *core;  // which pixels a "centre" strike lands on
  const float *x;    // drawn position, 0 = left edge .. 1 = right edge
  const float *y;    // drawn position, 0 = top .. 1 = bottom
};

// ── Overlays — a second voice on top of any base effect ─────────────────
// Per-pixel ROLES: a glint landing on one pixel, a point travelling the
// fixture, a drip falling through. Compositing, not replacement — the candle
// keeps burning under the sparkle. Formulas mirror web/src/effects.ts.

enum Overlay : int { OV_NONE = 0, OV_SPARKLE = 1, OV_CHASE = 2, OV_METEOR = 3 };

// Shortest way between two points on a loop, in turns (0..0.5).
inline float loop_dist(float a, float b) {
  float d = fmodf(fabsf(a - b), 1.0f);
  return fminf(d, 1.0f - d);
}

// `head` is where a look record's tempo clock puts the chase head (and the
// meteor's drip phase), in turns — castle_layers.h overlay_head. Negative is
// the legacy clock below, which is what every v1 show runs, digit for digit.
inline Rgbw apply_overlay(int ov, Rgbw c, float t, int p, int zi, const Fixture &fx,
                          float head_at = -1.0f) {
  if (ov == OV_SPARKLE) {
    int32_t cell = (int32_t) floorf(t * 7.0f);
    float g = hash3(cell, p, zi);
    if (g > 0.93f) {
      float k = (g - 0.93f) / 0.07f;
      return Rgbw{fminf(1.0f, c.r + 0.30f * k), fminf(1.0f, c.g + 0.30f * k),
                  fminf(1.0f, c.b + 0.30f * k), fminf(1.0f, c.w + 0.90f * k)};
    }
    return c;
  }
  if (ov == OV_CHASE) {
    if (p == fx.center) return Rgbw{c.r * 0.55f, c.g * 0.55f, c.b * 0.55f, c.w * 0.55f};
    float head = head_at >= 0.0f ? head_at : fmodf(t * 0.45f + zi * 0.37f, 1.0f);
    // Width is set in PIXELS, not in turns, so the lit head stays one pixel
    // wide whether it is going round six of them or sixteen.
    float span = (float) (fx.center < 0 ? fx.n : fx.n - 1);
    float boost = fmaxf(0.0f, 1.0f - loop_dist(fx.walk[p], head) * span * 0.9f);
    float k = 0.45f + 0.55f * boost;
    return Rgbw{c.r * k, c.g * k, c.b * k,
                fminf(1.0f, c.w * k + 0.50f * boost * boost)};
  }
  if (ov == OV_METEOR) {
    float ph = head_at >= 0.0f ? head_at : fmodf(t / 2.6f + zi * 0.41f, 1.0f);
    float rung = 1.0f / fmaxf(1.0f, (float) (fx.fall_steps - 1));
    if (ph < 0.12f) {
      // On a fixture with a middle the drip forms there, as it always has on
      // the Jewels. On one without, it forms along the top edge instead.
      bool forms = fx.center >= 0 ? (p == fx.center) : (fx.fall[p] < rung);
      if (!forms) return c;
      float k = (0.12f - ph) / 0.12f;
      return Rgbw{c.r, c.g, c.b, fminf(1.0f, c.w + 0.80f * k)};
    }
    if (p == fx.center) return c;
    float front = (ph - 0.12f) / 0.88f;
    float fade = 1.0f - front * 0.5f;
    float d = fabsf(fx.fall[p] - front);
    float boost = fmaxf(0.0f, 1.0f - d / (rung * 1.5f)) * fade;
    return Rgbw{fminf(1.0f, c.r + 0.20f * boost), c.g,
                fminf(1.0f, c.b + 0.25f * boost), fminf(1.0f, c.w + 0.60f * boost)};
  }
  return c;
}

// ── Strike masks — which pixels a flash actually hits ───────────────────
// 0 = the whole fixture, 1 = a fresh random scatter per strike (the epoch
// changes), 2 = core only, 3 = everything but the core. Mirrors flashGate in
// effects.ts. "Core" is pixel 0 on a Jewel and whatever generated/rig.h
// decided is the middle on a fixture that has no single centre.
//
// v5.71 (cue format v2): 4 = left half, 5 = right half, 6 = top half,
// 7 = bottom half, by the pixel's drawn x/y (8-15, the arcs, are below). A
// pixel ON the midline (a Jewel's centre, a ring's 12 and 6 o'clock) gets
// half; the far side keeps the same 0.1 glow the role masks leave. The
// ±0.001 band is what makes the 0.500000f rig.h prints and the desk's
// 0.5 + 1e-17 the same pixel.
inline float half_gate(float v, bool low) {
  if (v < 0.499f) return low ? 1.0f : 0.1f;
  if (v > 0.501f) return low ? 0.1f : 1.0f;
  return 0.5f;
}

// v5.71 arcs, modes 8-15 (arc0..arc7): ONE patch of the loop, centred at
// walk k/8 — on a ring pixel 0 is 12 o'clock and walk runs clockwise, so arc0
// is the top, arc2 3 o'clock, arc4 the bottom, arc6 9 o'clock. Stepping k is
// what spins a strike round the door ring and the Jewels. Full on within
// 1/12 turn of the centre, falling linearly to the 0.1 glow at 1/4 turn and
// 0.1 beyond. A hub's centre (Fixture::center) has no place on the loop and
// takes 0.3 under any arc.
//
// Exact in every copy (effects.ts arcGate, core/src/overlay.rs arc_gate) by
// construction, not by tolerance: walk is snapped to whole 1/3072 turns
// (3072 = 8 arcs x 384 = 12 hours x 256; a walk of i/n, n <= 64, is never
// within float error of a half step), the distance and the ramp are integer
// arithmetic, and the gate is thousandths — one correctly-rounded division,
// which float32 and a double rounded to float32 agree on.
inline constexpr int kArcFirst = 8;
inline constexpr int kArcs = 8;
inline constexpr int kArcTurn = 3072;

inline float arc_gate(int k, int p, const Fixture &fx) {
  if (p == fx.center) return 0.3f;
  const int q = (int) floorf(fx.walk[p] * (float) kArcTurn + 0.5f);
  int d = (q - k * (kArcTurn / kArcs)) % kArcTurn;
  if (d < 0) d += kArcTurn;
  if (d > kArcTurn / 2) d = kArcTurn - d;
  // 256 = 1/12 turn, 768 = 1/4: 0..512 steps down 0.900 in thousandths.
  const int x = d < 256 ? 0 : d > 768 ? 512 : d - 256;
  return (float) (1000 - 225 * x / 128) / 1000.0f;
}

inline float flash_gate(int mode, int p, int zi, int epoch, const Fixture &fx) {
  if (mode == 1) return hash3(p, zi, epoch) > 0.45f ? 1.0f : 0.15f;
  if (mode == 2) return fx.core[p] ? 1.0f : 0.1f;
  if (mode == 3) return fx.core[p] ? 0.1f : 1.0f;
  if (mode == 4) return half_gate(fx.x[p], true);
  if (mode == 5) return half_gate(fx.x[p], false);
  if (mode == 6) return half_gate(fx.y[p], true);
  if (mode == 7) return half_gate(fx.y[p], false);
  if (mode >= kArcFirst && mode < kArcFirst + kArcs)
    return arc_gate(mode - kArcFirst, p, fx);
  return 1.0f;
}

}  // namespace castle
