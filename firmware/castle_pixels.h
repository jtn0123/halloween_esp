#pragma once
// The per-zone render loop, lifted out of castle.yaml.
//
// It used to be a sixty-line `addressable_lambda` inside the one `light:`
// block, which worked while all three zones were identical Jewels on one
// chain. They are not any more: an RGBW Jewel and an RGB ring cannot share a
// chain at all (24 bits per pixel against 32 — see docs/WIRING.md §1), so
// each zone is now its own strip with its own lambda, and three copies of
// sixty lines of YAML-embedded C++ is not a thing anyone should maintain.
//
// So the loop lives here and each generated lambda is a handful of lines.
// The header deliberately knows nothing about ESPHome — it writes bytes into
// a caller-owned buffer rather than touching an AddressableLight — which is
// what lets it be syntax-checked, and eventually tested, off the device.
//
// Geometry comes from generated/rig.h; see the Fixture comment in
// castle_effects.h for why that is generated rather than written here.

#include <cstdint>

#include "castle_effects.h"

namespace castle {

/** One zone's inputs for a frame. Assembled by the lambda from the ESPHome
 *  globals, because `id(...)` only resolves inside one. */
struct ZoneIo {
  // Strike envelope — read AND written, since the decay happens here.
  float *flash;
  float *flash_target;
  float *flash_rise;
  float flash_decay;
  /** RGBW multiplier for this zone's current strike. Four floats. */
  const float *flash_col;

  float level;    // scales the base effect only; strikes are unscaled
  float phase;    // seconds added to this zone's clock
  float trim;     // per-zone install calibration
  float hue;      // global hue balance
  int effect;
  int center_eff; // -1 = the centre pixel runs the base effect too
  int overlay;
  int palette;
  int flash_mode;
  int flash_epoch;
  bool soft;
};

/**
 * Advance the zone's strike envelope by one 16 ms frame.
 *
 * Split out because each zone now ticks in its own lambda: three strips mean
 * three callbacks, and each must decay only its own zone or a strike would
 * fall three times as fast.
 */
inline void step_flash(ZoneIo &io) {
  // Softened strikes fall slower and peak lower, turning a strobe into a
  // swell — the photosensitivity guard, on by default.
  float d = io.soft ? (1.0f - (1.0f - io.flash_decay) * 0.35f) : io.flash_decay;
  if (*io.flash_target > 0.0f) {
    // Attack phase: swell toward the peak, then hand over to decay.
    *io.flash += *io.flash_rise;
    if (*io.flash >= *io.flash_target) {
      *io.flash = *io.flash_target;
      *io.flash_target = 0.0f;
    }
  } else {
    *io.flash *= d;
    if (*io.flash < 0.004f) *io.flash = 0.0f;
  }
}

/**
 * Render one zone into `out`, four bytes per pixel in R,G,B,W order.
 *
 * `out` must have room for `fx.n * 4` bytes. The caller owns it so this stays
 * free of allocation on a device with no heap to spare mid-frame.
 */
inline void render_zone(uint8_t *out, int zi, const Fixture &fx, float t, ZoneIo &io) {
  const float fbase = *io.flash * (io.soft ? 0.55f : 0.92f);
  const float tz = t + io.phase;          // anti-phase breathing between zones
  // The centre pixel may play its own role — an ember core inside a candle
  // ring, eyes in a dark window. A fixture with no middle (fx.center < 0)
  // never matches, so the base effect covers all of it.
  const int ring_eff = io.effect;
  const int center_eff = io.center_eff >= 0 ? io.center_eff : ring_eff;

  for (int p = 0; p < fx.n; p++) {
    // Seed varies per pixel so flame moves ACROSS the fixture.
    const float seed = zi * 4.7f + p * 1.31f;
    Rgbw c = render(p == fx.center ? center_eff : ring_eff, tz, seed, io.hue,
                    io.soft, io.palette);
    c = apply_overlay(io.overlay, c, tz, p, zi, fx);

    const float f = fbase * flash_gate(io.flash_mode, p, zi, io.flash_epoch, fx);
    const float r = fminf(1.0f, c.r * io.level + f * io.flash_col[0]) * io.trim;
    const float g = fminf(1.0f, c.g * io.level + f * io.flash_col[1]) * io.trim;
    const float b = fminf(1.0f, c.b * io.level + f * io.flash_col[2] * 0.96f) * io.trim;
    const float w = fminf(1.0f, c.w * io.level + f * io.flash_col[3]) * io.trim;

    out[p * 4 + 0] = (uint8_t) (r * 255.0f);
    out[p * 4 + 1] = (uint8_t) (g * 255.0f);
    out[p * 4 + 2] = (uint8_t) (b * 255.0f);
    out[p * 4 + 3] = (uint8_t) (w * 255.0f);
  }
}

}  // namespace castle
