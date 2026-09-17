// firmware/castle_cues.h, run on this machine against a real cue file.
//
//   cues_check <dir> <track>
//
// Loads <dir>/<track>.cue the way /api/play does, holds the clock 320 ms for
// a speaker that has not started, then ticks once a 16 ms render frame and
// prints the zone globals after every tick that applied something.
// tests/test_cue_file_cxx.py writes the file with tools/cue_file.py, builds
// the same trace from cue_file.decode, and the two must agree line for line
// — the file format has one definition in Python and one in C, and this is
// the only thing that holds them together.
#include <cstdio>
#include <string>

#include "castle_cues.h"

namespace {
constexpr long long MS = 1000;
int effect[3], center[3], overlay[3], palette[3], flash_mode[3], flash_epoch[3];
float flash[3], flash_col[12], flash_decay[3], flash_target[3], flash_rise[3],
    level[3], phase[3];

void dump(long long at_ms) {
  std::printf("%lld", at_ms);
  for (int z = 0; z < 3; z++)
    std::printf(" | %d %.4f %.4f %.4f %.4f %.2f %d %d %d %.2f %d %d %.2f %.2f %.2f %.2f",
                effect[z], flash[z], flash_target[z], flash_rise[z], flash_decay[z],
                level[z], center[z], overlay[z], palette[z], phase[z], flash_mode[z],
                flash_epoch[z], flash_col[z * 4], flash_col[z * 4 + 1],
                flash_col[z * 4 + 2], flash_col[z * 4 + 3]);
  std::printf("\n");
}
}  // namespace

int main(int argc, char **argv) {
  if (argc != 3) return 2;
  const castle_cues::Pixels px{effect, flash, flash_col, flash_decay, flash_target,
                               flash_rise, level, center, overlay, palette, phase,
                               flash_mode, flash_epoch};
  // What is not a cue file is not loaded, and says so by changing nothing.
  if (castle_cues::load("../escape.mp3", 0, argv[1]) || castle_cues::active()) return 3;
  if (castle_cues::load("scenes/09_x.mp3", 0, argv[1]) || castle_cues::active()) return 3;
  if (!castle_cues::load(std::string(argv[2]) + ".mp3", 0, argv[1])) {
    std::printf("refused\n");
    return 0;
  }
  std::printf("loaded %u\n", (unsigned) castle_cues::count());
  castle_cues::apply_base(px);
  dump(-1);
  // A silent speaker holds the first cue...
  for (long long now = 0; now < 320 * MS; now += 16 * MS)
    if (castle_cues::tick(px, false, now) != 0) return 4;
  // ...and the clock starts on the tick it is first heard.
  const long long start = 320 * MS;
  for (long long now = start; castle_cues::g_next < castle_cues::g_count; now += 16 * MS)
    if (castle_cues::tick(px, true, now) > 0) dump((now - start) / 1000);
  castle_cues::unload();
  if (castle_cues::active() || castle_cues::count() != 0) return 5;
  std::printf("cues OK\n");
  return 0;
}
