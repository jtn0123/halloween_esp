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
#include "castle_layers.h"

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
  /** The "Soften lightning" switch. Since v5.71 it softens a strike only
   *  when that strike landed in a train (castle_layers.h kSoftenWindowMs);
   *  the soft STROBE effect still follows the switch alone. */
  bool soft;
  /** The zone's v2 state — the ornament layer, the overlay clock and the
   *  train flags (castle_layers.h). Null reads as "nothing v2 has
   *  happened": no ornament, legacy clock, no strike in a train. */
  ZoneExtra *x = nullptr;
};

/** One layer's rise-then-decay for a 16 ms frame. `soft` is whether THIS
 *  strike is softened: the switch AND the strike's own train flag. */
inline void step_envelope(float &flash, float &target, float rise, float decay, bool soft) {
  // Softened strikes fall slower and peak lower, turning a strobe into a
  // swell — the photosensitivity guard, on by default.
  float d = soft ? (1.0f - (1.0f - decay) * 0.35f) : decay;
  if (target > 0.0f) {
    // Attack phase: swell toward the peak, then hand over to decay.
    flash += rise;
    if (flash >= target) {
      flash = target;
      target = 0.0f;
    }
  } else {
    flash *= d;
    if (flash < 0.004f) flash = 0.0f;
  }
}

/**
 * Advance the zone's strike envelope by one 16 ms frame.
 *
 * Split out because each zone now ticks in its own lambda: three strips mean
 * three callbacks, and each must decay only its own zone or a strike would
 * fall three times as fast.
 */
inline void step_flash(ZoneIo &io) {
  const bool train0 = io.x != nullptr && io.x->train0;
  step_envelope(*io.flash, *io.flash_target, *io.flash_rise, io.flash_decay,
                io.soft && train0);
  if (io.x != nullptr) {
    ZoneExtra &x = *io.x;
    step_envelope(x.orn_flash, x.orn_target, x.orn_rise, x.orn_decay, io.soft && x.train1);
  }
}

/**
 * Render one zone into `out`, four bytes per pixel in R,G,B,W order.
 *
 * `out` must have room for `fx.n * 4` bytes. The caller owns it so this stays
 * free of allocation on a device with no heap to spare mid-frame.
 */
inline void render_zone(uint8_t *out, int zi, const Fixture &fx, float t, ZoneIo &io) {
  const ZoneExtra &x = io.x != nullptr ? *io.x : kNoExtra;
  const float fbase = *io.flash * (io.soft && x.train0 ? 0.55f : 0.92f);
  // Layer 1, the ornament, ADDS to layer 0 (v5.71). 0 in every v1 show.
  const float obase = x.orn_flash * (io.soft && x.train1 ? 0.55f : 0.92f);
  const float *oc = x.orn_col;
  // Where a look record's tempo clock puts the overlay head — on the render
  // clock, not the zone's phased one: the look's own phase is the anchor.
  const float head = overlay_head(x, t);
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
    c = apply_overlay(io.overlay, c, tz, p, zi, fx, head);

    const float f = fbase * flash_gate(io.flash_mode, p, zi, io.flash_epoch, fx);
    const float o = obase * flash_gate(x.orn_mode, p, zi, x.orn_epoch, fx);
    const float r = fminf(1.0f, c.r * io.level + f * io.flash_col[0] + o * oc[0]) * io.trim;
    const float g = fminf(1.0f, c.g * io.level + f * io.flash_col[1] + o * oc[1]) * io.trim;
    const float b =
        fminf(1.0f, c.b * io.level + f * io.flash_col[2] * 0.96f + o * oc[2] * 0.96f) * io.trim;
    const float w = fminf(1.0f, c.w * io.level + f * io.flash_col[3] + o * oc[3]) * io.trim;

    out[p * 4 + 0] = (uint8_t) (r * 255.0f);
    out[p * 4 + 1] = (uint8_t) (g * 255.0f);
    out[p * 4 + 2] = (uint8_t) (b * 255.0f);
    out[p * 4 + 3] = (uint8_t) (w * 255.0f);
  }
}

}  // namespace castle
