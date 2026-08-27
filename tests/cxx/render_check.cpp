// Host-side invariant harness for the firmware render path.
//
// Compiles castle_effects.h + castle_pixels.h + generated/rig.h with a host
// compiler and exercises every entry point the device lambda calls, over
// every fixture in the generated rig plus synthetic ones the catalogue
// allows, at time/parameter extremes the show can reach. Properties, not
// golden values: the numeric parity with the browser lives in
// parity_dump.cpp + web/test/firmware_parity.ts.
//
//   render_check [seed]      exit 0 and "rendered ok" on success; every
//                            failure is printed as one line starting "FAIL".
#include "castle_effects.h"
#include "castle_pixels.h"
#include "generated/rig.h"

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

using namespace castle;

static int g_fails = 0;
static long g_checks = 0;

#define CHECK(cond, ...)                                   \
  do {                                                     \
    g_checks++;                                            \
    if (!(cond)) {                                         \
      g_fails++;                                           \
      if (g_fails <= 40) {                                 \
        std::printf("FAIL %s:%d ", __FILE__, __LINE__);    \
        std::printf(__VA_ARGS__);                          \
        std::printf("\n");                                 \
      }                                                    \
    }                                                      \
  } while (0)

static bool unit(float v) { return std::isfinite(v) && v >= 0.0f && v <= 1.0f; }
static bool unit4(const Rgbw &c) { return unit(c.r) && unit(c.g) && unit(c.b) && unit(c.w); }

// ── Synthetic fixtures: the catalogue shapes the generated rig may not hold
// today (ring16, stick8, wing32, mini, empty). Tables are built the way
// rig_layout.py builds them for lines/rings — only their RANGE matters to the
// overlays, and these exercise n, center and fall_steps the generated
// header does not.
struct SynthFixture {
  std::vector<float> walk, fall;
  std::vector<char> core_raw;  // std::vector<bool> has no contiguous data()
  Fixture fx;
};

static SynthFixture make_fixture(int n, int center, int fall_steps, bool ring) {
  SynthFixture s;
  for (int i = 0; i < n; i++) {
    if (ring) {
      float a = -3.14159265f / 2 + ((float) i / n) * 6.2831853f;
      s.walk.push_back((float) i / n);
      s.fall.push_back((std::sin(a) + 1.0f) / 2.0f);
    } else {
      s.walk.push_back((float) i / n);
      s.fall.push_back(n == 1 ? 0.0f : (float) i / (n - 1));
    }
    s.core_raw.push_back(i == (center >= 0 ? center : 0) ? 1 : 0);
  }
  if (n == 0) {  // the generator emits one dead element for an empty zone
    s.walk.push_back(0.0f);
    s.fall.push_back(0.0f);
    s.core_raw.push_back(0);
  }
  s.fx = Fixture{n, center, fall_steps, s.walk.data(), s.fall.data(),
                 reinterpret_cast<const bool *>(s.core_raw.data())};
  return s;
}

// std::array, not C-style: these are indexed with a modulus below, and the
// container is the only thing that knows its own length.
static constexpr std::array<float, 10> TIMES = {0.0f, 1e-3f, 0.017f, 1.234f,
    60.0f, 3600.0f, 86400.0f, 1e6f, 1e7f, -5.0f};
static constexpr std::array<float, 5> HUES = {0.0f, 0.5f, 1.0f, -1.0f, 2.0f};
static constexpr std::array<int, 7> PALS = {0, 1, 2, 3, -1, 4, 99};

// ── 1. render(): every effect id, in range and out, stays finite & 0..1 ──
/// One (effect, time, hue, palette, softness) across every pixel seed.
static void check_render_seeds(int eff, float t, float hue, int pal, bool soft) {
  for (int zi = 0; zi < 3; zi++)
    for (int p = 0; p < 32; p += 5) {
      const float seed = zi * 4.7f + p * 1.31f;
      const Rgbw c = render(eff, t, seed, hue, soft, pal);
      CHECK(unit4(c), "render eff=%d t=%g hue=%g pal=%d soft=%d -> "
            "%g %g %g %g", eff, t, hue, pal, (int) soft, c.r, c.g, c.b, c.w);
      if (eff <= 0 || eff > 12)
        CHECK(c.r == 0 && c.g == 0 && c.b == 0 && c.w == 0,
              "eff %d must be black", eff);
    }
}

/// Every palette and both softness settings, for one effect at one moment.
static void check_render_palettes(int eff, float t, float hue) {
  for (int pal : PALS)
    for (int soft = 0; soft < 2; soft++)
      check_render_seeds(eff, t, hue, pal, soft != 0);
}

static void check_render() {
  for (int eff = -2; eff <= 14; eff++)
    for (float t : TIMES)
      for (float hue : HUES)
        check_render_palettes(eff, t, hue);
  // Something must actually light for every real effect.
  for (int eff = 1; eff <= 12; eff++) {
    float total = 0;
    for (float t = 0; t < 10; t += 0.25f) {
      Rgbw c = render(eff, t, 1.31f, 0.5f, false, 0);
      total += c.r + c.g + c.b + c.w;
    }
    CHECK(total > 0.1f, "eff %d never lights", eff);
  }
}

// ── 2. overlays and gates: in range, identity for unknown ids ────────────

/// Every pixel and base colour for one (overlay, time, zone).
template <size_t N>
static void check_overlay_pixels(const Fixture &fx, const char *name, int ov,
                                 float t, int zi, const Rgbw (&bases)[N]) {
  for (int p = 0; p < fx.n; p++)
    for (const Rgbw &b : bases) {
      const Rgbw c = apply_overlay(ov, b, t, p, zi, fx);
      CHECK(unit4(c), "%s overlay=%d t=%g p=%d -> %g %g %g %g", name, ov, t, p,
            c.r, c.g, c.b, c.w);
      if (ov < 1 || ov > 3)
        CHECK(c.r == b.r && c.g == b.g && c.b == b.b && c.w == b.w,
              "%s overlay %d must be identity", name, ov);
    }
}

/// Every pixel for one (gate mode, epoch, zone).
static void check_gate_pixels(const Fixture &fx, const char *name, int mode,
                              int epoch, int zi) {
  for (int p = 0; p < fx.n; p++) {
    const float g = flash_gate(mode, p, zi, epoch, fx);
    CHECK(unit(g), "%s gate mode=%d -> %g", name, mode, g);
    if (mode < 1 || mode > 3) CHECK(g == 1.0f, "%s gate %d must be 1", name, mode);
    if (mode == 2 || mode == 3) CHECK(g == 1.0f || g == 0.1f, "%s gate core", name);
    if (mode == 1) CHECK(g == 1.0f || g == 0.15f, "%s gate scatter", name);
  }
}

/// One overlay id over every base colour, pixel and moment.
static void check_overlay_id(const Fixture &fx, const char *name, int ov) {
  static const Rgbw BASES[] = {{0, 0, 0, 0}, {1, 1, 1, 1},
                               {0.34f, 0.05f, 0, 1}, {0.2f, 0.9f, 0.4f, 0.3f}};
  for (float t : TIMES)
    for (int zi = 0; zi < 3; zi++)
      check_overlay_pixels(fx, name, ov, t, zi, BASES);
}

/// One gate mode: in range everywhere, and identity outside the real ids.
static void check_gate_mode(const Fixture &fx, const char *name, int mode) {
  for (int epoch = 0; epoch < 1000; epoch += 333)
    for (int zi = 0; zi < 3; zi++)
      check_gate_pixels(fx, name, mode, epoch, zi);
}

/// Centre and ring strikes are complementary, and each lights a pixel.
static void check_gate_halves(const Fixture &fx, const char *name) {
  if (fx.n <= 0) return;
  int core = 0;
  for (int p = 0; p < fx.n; p++) {
    const bool is_core = flash_gate(2, p, 0, 0, fx) == 1.0f;
    const bool is_ring = flash_gate(3, p, 0, 0, fx) == 1.0f;
    CHECK(is_core != is_ring, "%s pixel %d is both core and ring", name, p);
    core += is_core ? 1 : 0;
  }
  CHECK(core >= 1, "%s has no core pixel to strike", name);
}

static void check_overlays(const Fixture &fx, const char *name) {
  for (int ov = -1; ov <= 5; ov++)
    check_overlay_id(fx, name, ov);
  for (int mode = -1; mode <= 5; mode++)
    check_gate_mode(fx, name, mode);
  check_gate_halves(fx, name);
}

// ── 3. render_zone(): writes exactly n*4 bytes, never past them ──────────
static const uint8_t CANARY = 0xA5;

struct Probe {
  float flash, target, rise;
  float col[4];
};

static ZoneIo make_io(Probe &pr, float level, float hue, float trim, int eff,
                      int center, int ov, int pal, int mode, int epoch, bool soft,
                      float decay = 0.90f, float phase = 0.0f) {
  return ZoneIo{&pr.flash, &pr.target, &pr.rise, decay, pr.col,
                level, phase, trim, hue, eff, center, ov, pal, mode, epoch, soft};
}

/// Does ANY overlay, at any moment, put light on an OFF base? The blackout
/// check above depends on the answer being yes — see the comment there.
static bool any_overlay_lights(uint8_t *out, int zi, const Fixture &fx, int n,
                               Probe &dark) {
  for (int ov = OV_SPARKLE; ov <= OV_METEOR; ov++) {
    ZoneIo glint = make_io(dark, 1.0f, 0.5f, 1.0f, EFF_OFF, -1, ov, 0, 0, 0, false);
    for (int k = 0; k < 64; k++) {
      render_zone(out, zi, fx, (float) k * 0.37f, glint);
      for (int i = 0; i < n * 4; i++)
        if (out[i] != 0) return true;
    }
  }
  return false;
}

static void check_zone_writes(const Fixture &fx, int zi, const char *name, uint32_t seed) {
  const int n = fx.n;
  const int guard = 16;
  std::vector<uint8_t> buf(RIG_MAX_PIXELS * 4 + 32 * 4 + guard * 2, CANARY);
  uint8_t *out = buf.data() + guard;
  uint32_t rng = seed;
  auto next = [&rng]() { rng = rng * 1664525u + 1013904223u; return rng; };
  auto frand = [&]() { return (next() >> 8) / 16777216.0f; };

  for (int iter = 0; iter < 400; iter++) {
    std::memset(buf.data(), CANARY, buf.size());
    Probe pr{frand() * 1.5f, 0.0f, 0.0f, {frand(), frand(), frand(), frand()}};
    const float level = (iter % 7 == 0) ? 0.0f : (iter % 7 == 1) ? 2.0f : frand();
    const float trim = (iter % 5 == 0) ? 0.0f : (iter % 5 == 1) ? 1.0f : 0.1f + 0.9f * frand();
    const float hue = HUES[iter % HUES.size()];
    const auto eff = (int) ((next() >> 16) % 15) - 1;
    const auto center = (int) ((next() >> 16) % 15) - 2;
    const auto ov = (int) ((next() >> 16) % 5);
    const int pal = PALS[(next() >> 16) % PALS.size()];
    const auto mode = (int) ((next() >> 16) % 5);
    const auto epoch = (int) ((next() >> 16) % 1000);
    const bool soft = ((next() >> 16) & 1) != 0;
    const float t = TIMES[(next() >> 16) % TIMES.size()];
    const float phase = (iter % 3 == 0) ? 0.0f : frand() * 5.0f;
    ZoneIo io = make_io(pr, level, hue, trim, eff, center, ov, pal, mode, epoch, soft,
                        0.9f, phase);
    render_zone(out, zi, fx, t, io);

    for (int i = 0; i < guard; i++)
      CHECK(buf[i] == CANARY && out[n * 4 + i] == CANARY,
            "%s iter %d: write outside the %d-pixel buffer (zi=%d)", name, iter, n, zi);
    // Each pixel's four floats are re-derived here from the same public
    // pieces the loop composes, so a clamp that goes missing in render_zone
    // shows up as a byte mismatch rather than as silent wraparound.
    const float fbase = pr.flash * (soft ? 0.55f : 0.92f);
    const int ring_eff = eff;
    const int center_eff = center >= 0 ? center : ring_eff;
    for (int p = 0; p < n; p++) {
      const float sd = zi * 4.7f + p * 1.31f;
      Rgbw c = render(p == fx.center ? center_eff : ring_eff, t + phase, sd, hue, soft, pal);
      c = apply_overlay(ov, c, t + phase, p, zi, fx);
      const float f = fbase * flash_gate(mode, p, zi, epoch, fx);
      const float want[4] = {
          fminf(1.0f, c.r * level + f * pr.col[0]) * trim,
          fminf(1.0f, c.g * level + f * pr.col[1]) * trim,
          fminf(1.0f, c.b * level + f * pr.col[2] * 0.96f) * trim,
          fminf(1.0f, c.w * level + f * pr.col[3]) * trim};
      for (int k = 0; k < 4; k++) {
        CHECK(unit(want[k]), "%s p=%d ch%d pre-cast %g not in 0..1", name, p, k, want[k]);
        CHECK(out[p * 4 + k] == (uint8_t) (want[k] * 255.0f),
              "%s p=%d ch%d byte %d vs %d", name, p, k, out[p * 4 + k],
              (uint8_t) (want[k] * 255.0f));
      }
    }
    if (level == 0.0f && pr.flash == 0.0f) {
      for (int i = 0; i < n * 4; i++) CHECK(out[i] == 0, "%s dark zone wrote %d", name, out[i]);
    }
    if (trim == 0.0f)
      for (int i = 0; i < n * 4; i++) CHECK(out[i] == 0, "%s trim 0 wrote %d", name, out[i]);
  }

  // Blackout is what scene_stop sets (generated/scenes.yaml): effect off,
  // centre off, OVERLAY OFF, no strike -> every byte zero, whatever level,
  // trim and hue say. The overlay must be cleared too: sparkle, chase and
  // meteor ADD white, so any of them on an OFF base can still light a pixel
  // (the old sin-hash just happened not to glint at this t; the integer
  // hash does), and the next check pins that down so nobody "simplifies"
  // scene_stop to effect-only.
  std::memset(buf.data(), CANARY, buf.size());
  Probe dark{0.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  ZoneIo io = make_io(dark, 1.0f, 0.5f, 1.0f, EFF_OFF, -1, OV_NONE, 0, 0, 0, false);
  render_zone(out, zi, fx, 1e6f, io);
  for (int i = 0; i < n * 4; i++) CHECK(out[i] == 0, "%s blackout byte %d", name, out[i]);
  for (int i = 0; i < guard; i++) CHECK(out[n * 4 + i] == CANARY, "%s blackout overrun", name);
  if (n > 0) {
    CHECK(any_overlay_lights(out, zi, fx, n, dark),
          "%s: no overlay ever lights an OFF base — scene_stop's overlay reset is now "
          "redundant; fine, but re-read the blackout comment above", name);
  }

  // Full white strike, mode all, trim 1, dark base: every pixel is the
  // strike alone — 0.92 of full (the hard-strike ceiling), blue a further
  // 0.96 of that. Same bytes on every fixture, RGB or RGBW.
  Probe white{1.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  io = make_io(white, 0.0f, 0.5f, 1.0f, EFF_OFF, -1, 0, 0, 0, 0, false);
  render_zone(out, zi, fx, 0.0f, io);
  const uint8_t hi = (uint8_t) (0.92f * 255.0f), bl = (uint8_t) (0.92f * 0.96f * 255.0f);
  for (int p = 0; p < n; p++) {
    CHECK(out[p * 4] == hi && out[p * 4 + 1] == hi && out[p * 4 + 3] == hi,
          "%s white strike p=%d: %d %d %d", name, p, out[p * 4], out[p * 4 + 1], out[p * 4 + 3]);
    CHECK(out[p * 4 + 2] == bl, "%s blue p=%d: %d vs %d", name, p, out[p * 4 + 2], bl);
  }
  // Soft mode lowers the same strike's ceiling to 0.55.
  Probe softw{1.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  io = make_io(softw, 0.0f, 0.5f, 1.0f, EFF_OFF, -1, 0, 0, 0, 0, true);
  render_zone(out, zi, fx, 0.0f, io);
  for (int p = 0; p < n; p++)
    CHECK(out[p * 4] == (uint8_t) (0.55f * 255.0f), "%s soft strike p=%d: %d", name, p, out[p * 4]);
}

// ── 4. step_flash(): the envelope the lambda ticks each frame ───────────
static void check_flash_envelope() {
  Probe pr{1.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  ZoneIo io = make_io(pr, 1, 0.5f, 1, 1, -1, 0, 0, 0, 0, false, 0.90f);
  step_flash(io);
  CHECK(std::fabs(pr.flash - 0.9f) < 1e-6f, "decay one frame: %g", pr.flash);
  for (int i = 0; i < 200; i++) step_flash(io);
  CHECK(pr.flash == 0.0f, "decay must reach exactly 0, got %g", pr.flash);
  pr.flash = 0.003f;
  step_flash(io);
  CHECK(pr.flash == 0.0f, "below the floor snaps to 0");

  // Soft: 0.90 -> 1 - 0.10*0.35 = 0.965 per frame.
  Probe ps{1.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  ZoneIo so = make_io(ps, 1, 0.5f, 1, 1, -1, 0, 0, 0, 0, true, 0.90f);
  step_flash(so);
  CHECK(std::fabs(ps.flash - 0.965f) < 1e-6f, "soft decay: %g", ps.flash);

  // Attack: climbs by rise per frame, clamps at target, disarms.
  Probe pa{0.0f, 0.8f, 0.8f * 16.0f / 96.0f, {1, 1, 1, 1}};
  ZoneIo at = make_io(pa, 1, 0.5f, 1, 1, -1, 0, 0, 0, 0, false, 0.90f);
  step_flash(at);
  CHECK(std::fabs(pa.flash - 0.8f * 16.0f / 96.0f) < 1e-6f, "attack first frame %g", pa.flash);
  int frames = 1;
  while (pa.target > 0.0f && frames < 100) { step_flash(at); frames++; }
  CHECK(pa.target == 0.0f && pa.flash == 0.8f, "attack reaches peak: %g target %g",
        pa.flash, pa.target);
  CHECK(frames >= 6 && frames <= 7, "attack took %d frames", frames);
  step_flash(at);
  CHECK(std::fabs(pa.flash - 0.72f) < 1e-6f, "decay after peak %g", pa.flash);

  // Extremes: decay 1.0 holds (documented: never falls), decay 0 kills.
  Probe ph{1.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  ZoneIo hold = make_io(ph, 1, 0.5f, 1, 1, -1, 0, 0, 0, 0, false, 1.0f);
  for (int i = 0; i < 50; i++) step_flash(hold);
  CHECK(ph.flash == 1.0f, "decay 1.0 holds");
  Probe pk{1.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  ZoneIo kill = make_io(pk, 1, 0.5f, 1, 1, -1, 0, 0, 0, 0, false, 0.0f);
  step_flash(kill);
  CHECK(pk.flash == 0.0f, "decay 0 kills");
  // A stacked strike can hand step_flash a flash above 1; it must still fall.
  Probe pb{3.0f, 0.0f, 0.0f, {1, 1, 1, 1}};
  ZoneIo big = make_io(pb, 1, 0.5f, 1, 1, -1, 0, 0, 0, 0, false, 0.5f);
  for (int i = 0; i < 40; i++) step_flash(big);
  CHECK(pb.flash == 0.0f, "over-unity flash decays to 0, got %g", pb.flash);
}

// ── 5. the generated rig itself ─────────────────────────────────────────
static void check_rig_tables() {
  const auto nz = (int) (sizeof(RIG) / sizeof(RIG[0]));
  int biggest = 0;
  for (int z = 0; z < nz; z++) {
    const Fixture &fx = RIG[z];
    CHECK(fx.n >= 0 && fx.n <= RIG_MAX_PIXELS, "zone %d n=%d exceeds RIG_MAX_PIXELS", z, fx.n);
    CHECK(fx.center == -1 || (fx.center >= 0 && fx.center < fx.n), "zone %d centre", z);
    CHECK(fx.fall_steps >= (fx.n > 0 ? 1 : 0), "zone %d fall_steps", z);
    int core = 0;
    for (int p = 0; p < fx.n; p++) {
      CHECK(unit(fx.walk[p]) && unit(fx.fall[p]), "zone %d p=%d walk/fall range", z, p);
      core += fx.core[p] ? 1 : 0;
      if (fx.center >= 0) CHECK(fx.core[p] == (p == fx.center), "zone %d hub core", z);
    }
    if (fx.n > 0) CHECK(core >= 1, "zone %d no core", z);
    if (fx.n > biggest) biggest = fx.n;
  }
  CHECK(RIG_MAX_PIXELS >= biggest && RIG_MAX_PIXELS >= 1, "RIG_MAX_PIXELS");
}

int main(int argc, char **argv) {
  uint32_t seed = argc > 1 ? (uint32_t) std::strtoul(argv[1], nullptr, 10) : 1234u;
  check_rig_tables();
  check_render();
  check_flash_envelope();

  const auto nz = (int) (sizeof(RIG) / sizeof(RIG[0]));
  for (int z = 0; z < nz; z++) {
    char name[32];
    std::snprintf(name, sizeof name, "rig[%d]", z);
    check_overlays(RIG[z], name);
    check_zone_writes(RIG[z], z, name, seed + z);
  }
  struct Synth {
    int n;
    int center;
    int steps;
    bool ring;
    const char *name;
  };
  const Synth synth[] = {
      {16, -1, 9, true, "ring16"}, {8, -1, 8, false, "stick8"},
      {32, -1, 4, false, "wing32"}, {1, -1, 1, false, "mini1"},
      {5, -1, 5, false, "mini5"}, {0, -1, 0, false, "none"},
      {7, 0, 4, true, "jewel7-synth"}, {2, -1, 2, true, "ring2"}};
  for (auto &s : synth) {
    SynthFixture f = make_fixture(s.n, s.center, s.steps, s.ring);
    check_overlays(f.fx, s.name);
    // zi beyond the generated rig is legal for the helpers (they only seed).
    check_zone_writes(f.fx, 2, s.name, seed + 100);
  }

  if (g_fails) {
    std::printf("FAILED %d of %ld checks (seed %u)\n", g_fails, g_checks, seed);
    return 1;
  }
  std::printf("rendered ok, %ld checks, seed %u\n", g_checks, seed);
  return 0;
}
