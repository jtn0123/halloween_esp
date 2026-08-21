// Dump what the FIRMWARE computes for a seeded corpus, as JSON lines, so the
// browser port (web/src/effects.ts) can be checked against it numerically.
//
//   parity_dump [seed] [cases]
//
// Each line is one pixel of one frame: the inputs the device would have
// (effect, palette, hue, soft, time, zone, pixel, overlay, strike mask,
// epoch) and the three values the render loop composes — the base effect
// colour, the colour after the overlay, and the strike gate. Inputs are
// printed with enough digits to round-trip a float exactly, so the reader
// can feed the identical float32 values into the double-precision port.
//
// A second kind of line probes the noise primitives directly (hashf,
// vnoise, fbm) at the arguments the effects actually reach, so a mismatch
// can be attributed to the layer it comes from. The reader is
// web/test/firmware_parity.mjs, which owns the tolerances and the verdict.
#include "castle_effects.h"
#include "castle_pixels.h"
#include "generated/rig.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>

using namespace castle;

static uint32_t g_rng = 1;
// LCG: only the high bits are used by callers — the low bits of a
// power-of-two LCG cycle with a period of a few steps.
static uint32_t next_u32() {
  g_rng = g_rng * 1664525u + 1013904223u;
  return g_rng;
}
static float frand() { return (next_u32() >> 8) / 16777216.0f; }

static void print4(const char *key, const Rgbw &c) {
  std::printf("\"%s\":[%.9g,%.9g,%.9g,%.9g]", key, c.r, c.g, c.b, c.w);
}

int main(int argc, char **argv) {
  g_rng = argc > 1 ? (uint32_t) std::strtoul(argv[1], nullptr, 10) : 7u;
  const int cases = argc > 2 ? std::atoi(argv[2]) : 3000;
  const int nz = (int) (sizeof(RIG) / sizeof(RIG[0]));

  // Zone geometry, so the reader can check it is comparing like with like.
  for (int z = 0; z < nz; z++)
    std::printf("{\"kind\":\"zone\",\"zi\":%d,\"n\":%d,\"center\":%d,\"fall_steps\":%d}\n",
                z, RIG[z].n, RIG[z].center, RIG[z].fall_steps);

  // Noise primitives at integer and fractional arguments across the range
  // the effects reach: t*speed + seed*k, with t up to a few hours.
  for (int i = 0; i < 400; i++) {
    float x;
    switch (i % 4) {
      case 0: x = (float) ((next_u32() >> 16) % 64); break;              // small ints
      case 1: x = (float) ((next_u32() >> 16) % 200000); break;          // large ints
      case 2: x = frand() * 40.0f; break;                        // small real
      default: x = frand() * 60000.0f; break;                    // hours out
    }
    std::printf("{\"kind\":\"noise\",\"x\":%.9g,\"hash\":%.9g,\"vnoise\":%.9g,\"fbm\":%.9g}\n",
                x, hashf(x), vnoise(x), fbm(x));
  }

  for (int i = 0; i < cases; i++) {
    const int eff = (int) ((next_u32() >> 16) % 13);
    const int pal = (int) ((next_u32() >> 16) % 4);
    const float hue = (i % 9 == 0) ? 0.0f : (i % 9 == 1) ? 1.0f : frand();
    const bool soft = ((next_u32() >> 16) & 1) != 0;
    float t;
    switch (i % 4) {
      case 0: t = frand() * 10.0f; break;           // first seconds
      case 1: t = frand() * 600.0f; break;          // a long scene
      case 2: t = frand() * 36000.0f; break;        // an evening
      default: t = (float) ((next_u32() >> 16) % 4096) / 64.0f; break;  // 16 ms frames
    }
    const int zi = (int) ((next_u32() >> 16) % nz);
    const Fixture &fx = RIG[zi];
    if (fx.n == 0) continue;
    const int p = (int) ((next_u32() >> 16) % fx.n);
    const int ov = (int) ((next_u32() >> 16) % 4);
    const int mode = (int) ((next_u32() >> 16) % 4);
    const int epoch = (int) ((next_u32() >> 16) % 1000);
    const float seed = zi * 4.7f + p * 1.31f;

    const Rgbw base = render(eff, t, seed, hue, soft, pal);
    const Rgbw ovl = apply_overlay(ov, base, t, p, zi, fx);
    const float gate = flash_gate(mode, p, zi, epoch, fx);

    std::printf("{\"kind\":\"px\",\"eff\":%d,\"pal\":%d,\"hue\":%.9g,\"soft\":%d,\"t\":%.9g,"
                "\"zi\":%d,\"p\":%d,\"ov\":%d,\"mode\":%d,\"epoch\":%d,\"seed\":%.9g,",
                eff, pal, hue, soft ? 1 : 0, t, zi, p, ov, mode, epoch, seed);
    print4("base", base);
    std::printf(",");
    print4("ovl", ovl);
    std::printf(",\"gate\":%.9g}\n", gate);
  }
  return 0;
}
