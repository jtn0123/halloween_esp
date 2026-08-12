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
};

struct Rgbw {
  float r, g, b, w;
};

// Smoothed value noise. A flame flickers coherently; per-frame random reads as
// a loose connection, which is why this is interpolated rather than sampled.
inline float hashf(float n) {
  float s = sinf(n * 127.1f) * 43758.5453f;
  return s - floorf(s);
}

inline float vnoise(float x) {
  float i = floorf(x);
  float f = x - i;
  float u = f * f * (3.0f - 2.0f * f);
  return hashf(i) * (1.0f - u) + hashf(i + 1.0f) * u;
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

// ── Overlays — a second voice on top of any base effect ─────────────────
// Per-pixel ROLES: a glint landing on one pixel, a point orbiting the ring,
// a drip falling through. Compositing, not replacement — the candle keeps
// burning under the sparkle. Formulas mirror web/src/effects.ts exactly;
// a jewel is pixel 0 (centre) + pixels 1-6 (ring).

enum Overlay : int { OV_NONE = 0, OV_SPARKLE = 1, OV_CHASE = 2, OV_METEOR = 3 };

inline float ring_dist(float a, float pos) {
  float d = fabsf(a - pos);
  return fminf(d, 6.0f - d);
}

inline Rgbw apply_overlay(int ov, Rgbw c, float t, int p, int zi) {
  if (ov == OV_SPARKLE) {
    float cell = floorf(t * 7.0f);
    float g = hashf(cell * 13.7f + p * 7.77f + zi * 3.1f);
    if (g > 0.93f) {
      float k = (g - 0.93f) / 0.07f;
      return Rgbw{fminf(1.0f, c.r + 0.30f * k), fminf(1.0f, c.g + 0.30f * k),
                  fminf(1.0f, c.b + 0.30f * k), fminf(1.0f, c.w + 0.90f * k)};
    }
    return c;
  }
  if (ov == OV_CHASE) {
    float pos = fmodf(t * 0.45f + zi * 0.37f, 1.0f) * 6.0f;
    if (p == 0) return Rgbw{c.r * 0.55f, c.g * 0.55f, c.b * 0.55f, c.w * 0.55f};
    float boost = fmaxf(0.0f, 1.0f - ring_dist((float) (p - 1), pos) * 0.9f);
    float k = 0.45f + 0.55f * boost;
    return Rgbw{c.r * k, c.g * k, c.b * k,
                fminf(1.0f, c.w * k + 0.50f * boost * boost)};
  }
  if (ov == OV_METEOR) {
    float ph = fmodf(t / 2.6f + zi * 0.41f, 1.0f);
    if (ph < 0.12f) {
      if (p != 0) return c;
      float k = (0.12f - ph) / 0.12f;
      return Rgbw{c.r, c.g, c.b, fminf(1.0f, c.w + 0.80f * k)};
    }
    if (p == 0) return c;
    float fall = ((ph - 0.12f) / 0.88f) * 6.0f;
    float fade = 1.0f - ((ph - 0.12f) / 0.88f) * 0.5f;
    float boost = fmaxf(0.0f, 1.0f - ring_dist((float) (p - 1), fall) * 1.2f) * fade;
    return Rgbw{fminf(1.0f, c.r + 0.20f * boost), c.g,
                fminf(1.0f, c.b + 0.25f * boost), fminf(1.0f, c.w + 0.60f * boost)};
  }
  return c;
}

// ── Strike masks — which pixels a flash actually hits ───────────────────
// 0 = whole jewel, 1 = fresh random scatter per strike (epoch changes),
// 2 = centre only, 3 = ring only. Mirrors flashGate in effects.ts.
inline float flash_gate(int mode, int p, int zi, int epoch) {
  if (mode == 1) return hashf(p * 9.13f + zi * 5.7f + epoch * 17.9f) > 0.45f ? 1.0f : 0.15f;
  if (mode == 2) return p == 0 ? 1.0f : 0.1f;
  if (mode == 3) return p == 0 ? 0.1f : 1.0f;
  return 1.0f;
}

}  // namespace castle
