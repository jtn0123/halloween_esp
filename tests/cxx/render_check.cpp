// Compile-checks the render path off-device: castle_effects.h, the generated
// geometry tables, and castle_pixels.h, plus one call of each entry point.
#include "castle_effects.h"
#include "castle_pixels.h"
#include "generated/rig.h"

#include <cstdio>

int main() {
  using namespace castle;
  float flash = 1.0f, target = 0.0f, rise = 0.0f;
  const float col[4] = {1, 1, 1, 1};
  ZoneIo io{&flash, &target, &rise, 0.90f, col,
            1.0f, 0.0f, 1.0f, 0.5f,
            1, -1, 2, 0, 2, 0, false};

  static uint8_t buf[RIG_MAX_PIXELS * 4];
  unsigned long sum = 0;
  for (int z = 0; z < 3; z++) {
    const Fixture &fx = RIG[z];
    step_flash(io);
    render_zone(buf, z, fx, 1.234f, io);
    for (int i = 0; i < fx.n * 4; i++) sum += buf[i];
  }
  std::printf("rendered ok, checksum %lu, flash now %.4f\n", sum, flash);
  return 0;
}
