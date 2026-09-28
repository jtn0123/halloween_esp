// Host-side checks for what cue format v2 (v5.71) added to the render path:
// the ornament layer, the half-fixture and arc strike masks, the tempo-locked
// overlay clock and the train-only soften (firmware/castle_layers.h). Properties and
// exact small cases, run by tests/test_firmware_cxx.py; the invariants every
// other entry point must keep are render_check.cpp's.
//
//   layers_check             exit 0 and "layers ok" on success; every
//                            failure is printed as one line starting "FAIL".
#include "castle_effects.h"
#include "castle_layers.h"
#include "castle_pixels.h"
#include "generated/rig.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>

using namespace castle;

static int g_fails = 0;
static long g_checks = 0;

#define CHECK(cond, ...)                                \
  do {                                                  \
    g_checks++;                                         \
    if (!(cond)) {                                      \
      g_fails++;                                        \
      if (g_fails <= 40) {                              \
        std::printf("FAIL %s:%d ", __FILE__, __LINE__); \
        std::printf(__VA_ARGS__);                       \
        std::printf("\n");                              \
      }                                                 \
    }                                                   \
  } while (0)

static constexpr int kZones = (int) (sizeof(RIG) / sizeof(RIG[0]));

struct Zone {
  float flash = 0, target = 0, rise = 0;
  float col[4] = {1, 1, 1, 1};
  ZoneExtra x;
  ZoneIo io(int ov = OV_NONE, int mode = 0, bool soft = false, bool with_x = true) {
    return ZoneIo{&flash, &target, &rise, 0.9f, col, 0.0f, 0.0f, 1.0f, 0.5f, EFF_OFF,
                  -1, ov, 0, mode, 0, soft, with_x ? &x : nullptr};
  }
};

// ── layer 1 is ADDED to layer 0, clamped, and each decays on its own ──
static void check_additive() {
  uint8_t a[RIG_MAX_PIXELS * 4], b[RIG_MAX_PIXELS * 4], ab[RIG_MAX_PIXELS * 4];
  for (int zi = 0; zi < kZones; zi++) {
    const Fixture &fx = RIG[zi];
    Zone only0, only1, both;
    only0.flash = both.flash = 0.30f;
    only1.x.orn_flash = both.x.orn_flash = 0.20f;
    ZoneIo i0 = only0.io(), i1 = only1.io(), i2 = both.io();
    render_zone(a, zi, fx, 1.0f, i0);
    render_zone(b, zi, fx, 1.0f, i1);
    render_zone(ab, zi, fx, 1.0f, i2);
    const auto sum = (uint8_t) (fminf(1.0f, 0.30f * 0.92f + 0.20f * 0.92f) * 255.0f);
    for (int p = 0; p < fx.n; p++) {
      CHECK(ab[p * 4] == sum, "zone %d p=%d: both layers %d, want %d", zi, p, ab[p * 4], sum);
      CHECK(ab[p * 4] >= a[p * 4] && ab[p * 4] >= b[p * 4], "zone %d p=%d: a layer cut", zi, p);
    }
    // Saturated: two full strikes clamp at 255, never wrap.
    both.flash = both.x.orn_flash = 1.0f;
    i2 = both.io();
    render_zone(ab, zi, fx, 1.0f, i2);
    for (int p = 0; p < fx.n; p++) CHECK(ab[p * 4] == 255, "zone %d clamp: %d", zi, ab[p * 4]);
  }
  Zone z;
  z.flash = 1.0f;
  z.x.orn_flash = 1.0f;
  z.x.orn_decay = 0.5f;
  ZoneIo io = z.io();
  step_flash(io);
  CHECK(std::fabs(z.flash - 0.9f) < 1e-6f && std::fabs(z.x.orn_flash - 0.5f) < 1e-6f,
        "independent decay: %g %g", z.flash, z.x.orn_flash);
  // Rise, then decay — layer 0's envelope on layer 1's fields.
  z.x.orn_flash = 0.0f;
  z.x.orn_target = 0.6f;
  z.x.orn_rise = 0.25f;
  for (int i = 0; i < 3; i++) step_flash(io);
  CHECK(z.x.orn_flash == 0.6f && z.x.orn_target == 0.0f, "ornament attack %g", z.x.orn_flash);
  // No v2 state (a v1 show): the ornament term is exactly zero.
  Zone v1;
  v1.flash = 0.4f;
  ZoneIo with = v1.io(), without = v1.io(OV_NONE, 0, false, false);
  render_zone(a, 0, RIG[0], 2.0f, with);
  render_zone(b, 0, RIG[0], 2.0f, without);
  CHECK(std::memcmp(a, b, (size_t) RIG[0].n * 4) == 0, "empty ZoneExtra changed a byte");
}

// ── modes 4-7: left/right and top/bottom halves are complementary ──
static void check_halves() {
  for (int zi = 0; zi < kZones; zi++) {
    const Fixture &fx = RIG[zi];
    for (int pair = 4; pair <= 6; pair += 2) {
      int lit_a = 0, lit_b = 0;
      for (int p = 0; p < fx.n; p++) {
        const float ga = flash_gate(pair, p, zi, 0, fx);
        const float gb = flash_gate(pair + 1, p, zi, 0, fx);
        const bool comp = (ga == 1.0f && gb == 0.1f) || (ga == 0.1f && gb == 1.0f) ||
                          (ga == 0.5f && gb == 0.5f);
        CHECK(comp, "zone %d p=%d modes %d/%d: %g %g", zi, p, pair, pair + 1, ga, gb);
        lit_a += ga == 1.0f ? 1 : 0;
        lit_b += gb == 1.0f ? 1 : 0;
      }
      CHECK(fx.n < 3 || (lit_a > 0 && lit_b > 0), "zone %d modes %d/%d leave a half dark",
            zi, pair, pair + 1);
      CHECK(lit_a == lit_b, "zone %d modes %d/%d lopsided: %d vs %d", zi, pair, pair + 1,
            lit_a, lit_b);
    }
  }
}

// ── modes 8-15: arcs round the loop, identical on both layers ──
static const float kJewelArc[2][7] = {{0.3f, 1.0f, 0.55f, 0.1f, 0.1f, 0.1f, 0.55f},
                                      {0.3f, 0.775f, 1.0f, 0.325f, 0.1f, 0.1f, 0.1f}};
static const float kRingArc[2][12] = {
    {1.0f, 1.0f, 0.55f, 0.1f, 0.1f, 0.1f, 0.1f, 0.1f, 0.1f, 0.1f, 0.55f, 1.0f},
    {0.775f, 1.0f, 1.0f, 0.775f, 0.325f, 0.1f, 0.1f, 0.1f, 0.1f, 0.1f, 0.1f, 0.325f}};

static void check_arc_geometry(int zi, const Fixture &fx) {
  const bool jewel = fx.n == 7 && fx.center == 0, ring = fx.n == 12 && fx.center < 0;
  // arc0 (12 o'clock) and arc1 (half past one), pixel for pixel.
  for (int k = 0; k < 2; k++)
    for (int p = 0; p < fx.n; p++) {
      const float g = flash_gate(kArcFirst + k, p, zi, 0, fx);
      if (jewel) CHECK(g == kJewelArc[k][p], "jewel arc%d p=%d: %g", k, p, g);
      if (ring) CHECK(g == kRingArc[k][p], "ring arc%d p=%d: %g", k, p, g);
    }
  // The rest are those two turned: two arcs is a quarter turn, which is
  // three pixels on a Ring12; four is half a turn, three on a Jewel's six.
  for (int k = 0; k + 2 < kArcs && ring; k++)
    for (int p = 0; p < 12; p++)
      CHECK(flash_gate(kArcFirst + k + 2, (p + 3) % 12, zi, 0, fx) ==
                flash_gate(kArcFirst + k, p, zi, 0, fx), "ring arc%d->%d p=%d", k, k + 2, p);
  for (int k = 0; k + 4 < kArcs && jewel; k++)
    for (int j = 0; j < 6; j++)
      CHECK(flash_gate(kArcFirst + k + 4, 1 + (j + 3) % 6, zi, 0, fx) ==
                flash_gate(kArcFirst + k, 1 + j, zi, 0, fx), "jewel arc%d->%d j=%d", k, k + 4, j);
}

static void check_arcs() {
  int jewels = 0, rings = 0;
  uint8_t a[RIG_MAX_PIXELS * 4], b[RIG_MAX_PIXELS * 4];
  for (int zi = 0; zi < kZones; zi++) {
    const Fixture &fx = RIG[zi];
    jewels += fx.n == 7 && fx.center == 0 ? 1 : 0;
    rings += fx.n == 12 && fx.center < 0 ? 1 : 0;
    check_arc_geometry(zi, fx);
    for (int k = 0; k < kArcs; k++) {
      // The same arc on layer 0 and on the ornament: the same bytes, and
      // each is exactly the gate times the strike.
      Zone l0, l1;
      l0.flash = l1.x.orn_flash = 0.8f;
      l1.x.orn_mode = kArcFirst + k;
      ZoneIo i0 = l0.io(OV_NONE, kArcFirst + k), i1 = l1.io();
      render_zone(a, zi, fx, 1.0f, i0);
      render_zone(b, zi, fx, 1.0f, i1);
      CHECK(std::memcmp(a, b, (size_t) fx.n * 4) == 0, "zone %d arc%d: layers differ", zi, k);
      for (int p = 0; p < fx.n; p++) {
        const float g = flash_gate(kArcFirst + k, p, zi, 0, fx);
        const auto want = (uint8_t) (fminf(1.0f, 0.8f * 0.92f * g) * 255.0f);
        CHECK(a[p * 4] == want, "zone %d arc%d p=%d: %d want %d", zi, k, p, a[p * 4], want);
      }
    }
  }
  CHECK(jewels > 0 && rings > 0, "the rig has %d Jewel7s and %d Ring12s", jewels, rings);
}

// ── the overlay clock: legacy by default, no jump on any change ──
static void check_motion() {
  const Rgbw base{0.2f, 0.1f, 0.0f, 0.3f};
  // The legacy head IS what apply_overlay computes on its own, bit for bit,
  // so leaving the legacy clock for a rate starts where the head already is.
  for (int zi = 0; zi < kZones; zi++)
    for (float tz = 0.0f; tz < 40.0f; tz += 0.731f)
      for (int ov = OV_CHASE; ov <= OV_METEOR; ov++)
        for (int p = 0; p < RIG[zi].n; p++) {
          const Rgbw l = apply_overlay(ov, base, tz, p, zi, RIG[zi]);
          const Rgbw h = apply_overlay(ov, base, tz, p, zi, RIG[zi], legacy_head(ov, tz, zi));
          CHECK(std::memcmp(&l, &h, sizeof l) == 0, "legacy head ov=%d zi=%d tz=%g", ov, zi, tz);
        }
  ZoneExtra x;
  CHECK(overlay_head(x, 12.0f) == -1.0f, "a fresh zone is on the legacy clock");
  set_motion(x, OV_CHASE, 0.3f, 1, 5.0f, 0.5f, -1.0f);
  CHECK(overlay_head(x, 5.0f) == legacy_head(OV_CHASE, 5.3f, 1), "leaving legacy jumped");
  const float before = overlay_head(x, 7.0f);
  set_motion(x, OV_CHASE, 0.3f, 1, 7.0f, 2.0f, -1.0f);
  CHECK(std::fabs(overlay_head(x, 7.0f) - before) < 1e-6f, "a rate change jumped");
  CHECK(std::fabs(overlay_head(x, 7.25f) - frac1(before + 0.5f)) < 1e-5f, "new rate");
  set_motion(x, OV_CHASE, 0.3f, 1, 8.0f, -1.0f, 0.25f);  // keep the rate, set the phase
  CHECK(x.rate == 2.0f && overlay_head(x, 8.0f) == 0.25f, "phase set: rate %g", x.rate);
  set_motion(x, OV_CHASE, 0.3f, 1, 9.0f, -1.0f, -1.0f);  // keep both: no change
  CHECK(std::fabs(overlay_head(x, 9.0f) - frac1(0.25f + 2.0f)) < 1e-5f, "keep drifted");
  set_motion(x, OV_CHASE, 0.3f, 1, 9.0f, 0.0f, 0.5f);  // back to legacy; head ignored
  CHECK(x.rate == 0.0f && overlay_head(x, 9.0f) == -1.0f, "rate 0 is legacy");
  // Rate 0 renders frame-exact against no v2 state at all.
  uint8_t a[RIG_MAX_PIXELS * 4], b[RIG_MAX_PIXELS * 4];
  Zone z;
  for (float t = 0.0f; t < 20.0f; t += 0.37f) {
    ZoneIo i0 = z.io(OV_CHASE), i1 = z.io(OV_CHASE, 0, false, false);
    render_zone(a, 2, RIG[2], t, i0);
    render_zone(b, 2, RIG[2], t, i1);
    CHECK(std::memcmp(a, b, (size_t) RIG[2].n * 4) == 0, "rate 0 differs at t=%g", t);
  }
}

// ── the train rule: softened only < 333 ms after the zone's last strike ──
static void check_trains() {
  ZoneExtra x;
  CHECK(!note_strike(x, 1000), "the first strike after load is never a train");
  CHECK(note_strike(x, 1332), "332 ms apart is a train");
  CHECK(note_strike(x, 1332), "the same millisecond is the same event");
  CHECK(!note_strike(x, 1665), "333 ms apart is not");
  CHECK(note_strike(x, 1700), "35 ms apart is");
  CHECK(!note_strike(x, 5000), "a long gap ends the train");
  reset_zone_x();
  g_zone_x[1].last_ms = 10;
  reset_zone_x();
  CHECK(g_zone_x[1].last_ms == -1 && g_zone_x[1].rate == 0.0f, "reset_zone_x");
  // The switch gates the train live: soft && train softens, nothing else does.
  const bool cases[4][2] = {{false, false}, {false, true}, {true, false}, {true, true}};
  for (const auto &c : cases) {
    Zone z;
    z.flash = 1.0f;
    z.x.train0 = c[1];
    ZoneIo io = z.io(OV_NONE, 0, c[0]);
    step_flash(io);
    const float want = c[0] && c[1] ? 0.965f : 0.9f;
    CHECK(std::fabs(z.flash - want) < 1e-6f, "soft=%d train=%d decay %g", c[0], c[1], z.flash);
  }
}

int main() {
  check_additive();
  check_halves();
  check_arcs();
  check_motion();
  check_trains();
  if (g_fails) {
    std::printf("FAILED %d of %ld checks\n", g_fails, g_checks);
    return 1;
  }
  std::printf("layers ok, %ld checks\n", g_checks);
  return 0;
}
