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

// Mansion palette poles, matching scenes.yaml.
constexpr float VIOLET[3] = {0.66f, 0.08f, 1.00f};
constexpr float GREEN[3] = {0.14f, 1.00f, 0.42f};

inline Rgbw mix_pal(float k, float level) {
  k = fminf(1.0f, fmaxf(0.0f, k));
  return Rgbw{(VIOLET[0] + (GREEN[0] - VIOLET[0]) * k) * level,
              (VIOLET[1] + (GREEN[1] - VIOLET[1]) * k) * level,
              (VIOLET[2] + (GREEN[2] - VIOLET[2]) * k) * level, 0.0f};
}

// `seed` varies per pixel so a flame moves ACROSS the jewel rather than the
// whole window pulsing as one lamp. That spatial motion is most of the realism.
//
// `hue` biases the mansion crossfade violet<->green.
// `soft` damps hard strobing — ~7 Hz white strobe is a photosensitivity risk.
inline Rgbw render(int eff, float t, float seed, float hue, bool soft) {
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
      return mix_pal(0.0f, 0.24f + 0.52f * b);
    }
    case EFF_WISP: {
      float n = fbm(t * 2.1f + seed * 5.3f);
      float l = fmaxf(0.0f, 0.18f + 0.82f * n - 0.14f);
      return mix_pal(1.0f, l);
    }
    case EFF_MANSION: {
      float sweep = 0.5f + 0.5f * sinf(t * 0.38f + seed * 0.7f);
      float shimmer = 0.84f + 0.16f * fbm(t * 1.05f + seed * 2.7f);
      return mix_pal(sweep * 0.8f + (hue - 0.5f) * 0.9f, 0.62f * shimmer);
    }
    case EFF_CHILL: {
      float b = 0.5f + 0.5f * sinf(t * 0.50f + seed * 1.1f);
      return mix_pal(hue * 0.35f, 0.14f + 0.16f * b);
    }
    case EFF_THROB: {
      float p = 0.5f + 0.5f * sinf(t * 7.4f + seed * 0.4f);
      p *= p;
      return mix_pal(hue * 0.5f, 0.20f + 0.80f * p);
    }
    case EFF_STROBE: {
      if (soft) {
        float l = 0.34f + 0.44f * (0.5f + 0.5f * sinf(t * 3.1f + seed));
        return Rgbw{0.10f * l, 0.10f * l, 0.14f * l, 1.00f * l};
      }
      float on = sinf(t * 44.0f + seed) > 0.0f ? 1.0f : 0.06f;
      return Rgbw{0.12f * on, 0.12f * on, 0.18f * on, 1.00f * on};
    }
    case EFF_OFF:
    default:
      return Rgbw{0.0f, 0.0f, 0.0f, 0.0f};
  }
}

}  // namespace castle
