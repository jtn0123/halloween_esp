#pragma once
// What cue format version 2 (v5.71) added to a zone: an ornament layer, a
// tempo-locked overlay clock, and the memory the rate-aware soften needs.
//
// None of it is an ESPHome global. Nothing but the cue reader
// (castle_cues.h) and the render loop (castle_pixels.h) reads or writes it,
// no YAML lambda names it by id, and a struct per zone is one thing to reset
// where three times as many globals would be a dozen. Three of them are
// ~70 bytes each of internal RAM. They are reset wherever a show starts
// (castle_cues::apply_zones) and at blackout (scene_stop, tools/gen_show.py),
// so a v1 show — which never writes any of this — renders exactly as it did
// before the struct existed.
//
// web/src/show_layers.ts is the desk's copy of every function below, and
// demo/castle-radio/cue-playback.js drives that copy with card semantics.
// Change all three or none (docs/PARITY.md).

#include <cmath>
#include <cstdint>

#include "castle_effects.h"

namespace castle {

/// A strike that lands on a zone less than this many ms after the zone's
/// previous strike (either layer) is part of a flash TRAIN — faster than
/// three a second — and only a train is softened by the "Soften lightning"
/// switch. Per zone, not castle-wide: the three fixtures are separate small
/// lights, and a steady left/right beat must not turn soft at random
/// whenever the door happens to flash in between. The first strike on a
/// zone after a show starts is never part of a train.
inline constexpr int64_t kSoftenWindowMs = 333;

/// One zone's v2 state. Layer 0 is still the zone_flash* globals in
/// castle.yaml; `orn_*` is layer 1, the ornament, which a strike reaches
/// with bit 7 of its zone mask and which the render loop ADDS to layer 0
/// instead of replacing it — a weak hit no longer cuts a strong one short.
struct ZoneExtra {
  // ── Layer 1: the same envelope as layer 0, field for field ──
  float orn_flash = 0.0f;
  float orn_target = 0.0f;
  float orn_rise = 0.0f;
  float orn_decay = 0.9f;
  float orn_col[4] = {1.0f, 1.0f, 1.0f, 1.0f};
  int orn_mode = 0;
  int orn_epoch = 0;
  // ── Rate-aware soften: was each layer's current strike part of a train?
  // Decided when the strike FIRES; the switch is applied live on top.
  bool train0 = false;
  bool train1 = false;
  // Song-clock ms of this zone's latest strike and the one before it
  // (strictly earlier). -1 = none yet. Strikes at the same millisecond are
  // one event, so a layer-0 + layer-1 pair gets one decision.
  int64_t last_ms = -1;
  int64_t prev_ms = -1;
  // ── The overlay clock (look records). rate 0 = the legacy clock-driven
  // chase and meteor, exactly as before v5.71. Otherwise the head is
  // head0 + rate * (t - t0) turns, t the render clock in seconds: every
  // rate change re-anchors (head0, t0) at the head's CURRENT position, so
  // the head never jumps, and being a function of the clock rather than a
  // per-frame sum it cannot drift off the beat when a frame is late.
  float rate = 0.0f;   // turns per second
  float head0 = 0.0f;  // turns, at t0
  float t0 = 0.0f;     // render-clock seconds
};

inline ZoneExtra g_zone_x[3];
/// What a zone with no v2 state reads as (castle_pixels.h, a null ZoneIo::x).
inline constexpr ZoneExtra kNoExtra{};

/// Every zone back to "nothing v2 has happened": a new show, or blackout.
inline void reset_zone_x() {
  for (auto &z : g_zone_x) z = ZoneExtra{};
}

/// Book a strike at song-clock `t_ms` and answer whether it is part of a
/// train (see kSoftenWindowMs).
inline bool note_strike(ZoneExtra &x, int64_t t_ms) {
  if (t_ms != x.last_ms) {
    x.prev_ms = x.last_ms;
    x.last_ms = t_ms;
  }
  return x.prev_ms >= 0 && t_ms >= x.prev_ms && t_ms - x.prev_ms < kSoftenWindowMs;
}

inline float frac1(float v) { return v - floorf(v); }

/// Where the legacy clock puts the overlay's head at zone time `tz`: the
/// meteor's drip phase on a meteor zone, the chase's head on any other.
inline float legacy_head(int overlay, float tz, int zi) {
  if (overlay == OV_METEOR) return fmodf(tz / 2.6f + zi * 0.41f, 1.0f);
  return fmodf(tz * 0.45f + zi * 0.37f, 1.0f);
}

/// The head the render loop hands apply_overlay at render-clock `t`:
/// -1 (legacy) while the rate is 0.
inline float overlay_head(const ZoneExtra &x, float t) {
  return x.rate > 0.0f ? frac1(x.head0 + x.rate * (t - x.t0)) : -1.0f;
}

/// A look record's motion half, at render-clock `t_rec`. `rate` < 0 keeps
/// the current rate; `head` < 0 continues from wherever the head is now.
/// A rate of 0 goes back to the legacy clock (and ignores `head`: the
/// legacy head has no phase to set). `overlay` is the zone's overlay AFTER
/// the record, `phase` its zone_phase — together they say where a legacy
/// head is, so leaving the legacy clock is as seamless as a rate change.
inline void set_motion(ZoneExtra &x, int overlay, float phase, int zi, float t_rec,
                       float rate, float head) {
  const float next = rate < 0.0f ? x.rate : rate;
  if (next > 0.0f) {
    const float now = x.rate > 0.0f ? overlay_head(x, t_rec)
                                    : legacy_head(overlay, t_rec + phase, zi);
    x.head0 = head < 0.0f ? now : head;
    x.t0 = t_rec;
  }
  x.rate = next;
}

}  // namespace castle
