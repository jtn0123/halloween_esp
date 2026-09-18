// firmware/castle_scenes.h, run on this machine against a real card.
//
//   scenes_check <dir> <op> [<op> ...]
//
// The scene runner is the v5.67 replacement for twelve generated ESPHome
// scripts, and everything it knows it reads out of two card files that
// tools/scene_manifest.py and tools/cue_file.py write. Nothing but this
// harness runs that C over those bytes; tests/test_scene_manifest_cxx.py
// writes the card, drives the ops and asserts on the lines printed here.
//
// Each op prints ONE line beginning with its own name, so a failure names the
// step rather than a diff of a whole trace:
//
//   ids                what /api/status's `scenes` would say
//   missing            what /api/status's `missing` would say
//   begin:<id>         step one of a start: the manifest row and nothing
//                      else, so `cues=0` here is the proof that no cue file
//                      was opened before the audio call (v5.68)
//   cues               step two: open the running scene's cue file
//   start:<id>         the WHOLE start, in scene_run's order — the manifest
//                      row, then the `sfx` call ("audio <token>"), then the
//                      cue file. The order of these three lines is the thing
//                      v5.68 changed and the thing a test must pin.
//   state              the runner state, unchanged
//   clock:<us>         the timeline starts (the speaker was heard)
//   fin:<us>           has it reached its length?
//   end                the script's TAIL: what castle_scenes.yaml does once
//                      `finished()` is true — re-execute itself when the
//                      scene loops, give the cues back when it does not (J2)
//   stop               the blackout path
//   raw:<track>        a raw card song's own .cue, the v5.63 path, so a
//                      scene -> song -> scene sequence can be driven
//   base               apply the loaded base look; the zone globals after it
//   zones              the zone globals as they stand, applying nothing
//   tick:<us>          one 16 ms render frame; how many records it applied
//   rm:<name>          delete one file from the card mid-run — the card
//                      changing underneath a running show, which is the only
//                      way to prove a re-run did not read it again
//
// The zone globals are this file's, not ESPHome's — castle_cues::Pixels is
// addresses by design, which is exactly what makes it testable here.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "castle_scenes.h"

namespace {
int effect[3], center[3], overlay[3], palette[3], flash_mode[3], flash_epoch[3];
float flash[3], flash_col[12], flash_decay[3], flash_target[3], flash_rise[3],
    level[3], phase[3];

const castle_cues::Pixels px{effect,      flash,  flash_col, flash_decay,
                             flash_target, flash_rise, level, center,
                             overlay,     palette, phase,     flash_mode,
                             flash_epoch};

void print_state(const char *lead) {
  std::printf("%s running=%s audio=%s armed=%d loops=%d len=%u vol=%.2f cues=%u\n",
              lead, castle_scenes::running(), castle_scenes::audio(),
              castle_scenes::armed() ? 1 : 0, castle_scenes::loops() ? 1 : 0,
              (unsigned) castle_scenes::length_ms(), castle_scenes::volume(),
              (unsigned) castle_cues::count());
}

/// The value after a ':' in an op, or "".
const char *arg_of(const char *op) {
  const char *c = std::strchr(op, ':');
  return c == nullptr ? "" : c + 1;
}
}  // namespace

int main(int argc, char **argv) {
  if (argc < 3) return 2;
  const char *dir = argv[1];
  for (int i = 2; i < argc; i++) {
    const char *op = argv[i];
    if (std::strcmp(op, "ids") == 0) {
      std::printf("ids %s\n", castle_scenes::ids_csv(dir).c_str());
    } else if (std::strcmp(op, "missing") == 0) {
      std::printf("missing %s\n", castle_scenes::missing_csv(dir).c_str());
    } else if (std::strncmp(op, "begin:", 6) == 0) {
      const bool known = castle_scenes::begin(arg_of(op), 0, dir);
      std::printf("begin known=%d\n", known ? 1 : 0);
      print_state("state");
    } else if (std::strcmp(op, "cues") == 0) {
      const bool ok = castle_scenes::load_cues(dir);
      std::printf("cues loaded=%d count=%u\n", ok ? 1 : 0,
                  (unsigned) castle_cues::count());
    } else if (std::strncmp(op, "start:", 6) == 0) {
      // castle_scenes.yaml's scene_run, in its own order and nothing else:
      // one manifest row, the play call, then the cue file while the audio
      // pipeline spins up.
      const bool known = castle_scenes::begin(arg_of(op), 0, dir);
      std::printf("begin known=%d\n", known ? 1 : 0);
      if (known) {
        std::printf("audio %s\n", castle_scenes::audio());
        std::printf("cues loaded=%d count=%u\n", castle_scenes::load_cues(dir) ? 1 : 0,
                    (unsigned) castle_cues::count());
      }
      print_state("state");
    } else if (std::strcmp(op, "state") == 0) {
      print_state("state");
    } else if (std::strncmp(op, "clock:", 6) == 0) {
      castle_scenes::start_clock(atoll(arg_of(op)));
      std::printf("clock ok\n");
    } else if (std::strncmp(op, "fin:", 4) == 0) {
      std::printf("fin %d\n", castle_scenes::finished(atoll(arg_of(op))) ? 1 : 0);
    } else if (std::strcmp(op, "end") == 0) {
      // J2 (grade report 2026-09-17 pm): the `if loops` at the end of
      // scene_run, both branches. `then` re-executes the script with the
      // running id, which leaves every one of these numbers alone; `else` —
      // which did not exist until v5.69 — runs `cues_end`, and cues_end is
      // castle_scenes::stop() plus zeroing castle_web::g_cues. So the ONE
      // line of C the branch is worth is the stop, and what a test has to be
      // able to see is that the PSRAM went back with it.
      if (!castle_scenes::loops()) castle_scenes::stop();
      std::printf("end active=%d armed=%d cues=%u running=%s\n",
                  castle_cues::active() ? 1 : 0,
                  castle_scenes::armed() ? 1 : 0,
                  (unsigned) castle_cues::count(), castle_scenes::running());
    } else if (std::strcmp(op, "active") == 0) {
      std::printf("active %d\n", castle_cues::active() ? 1 : 0);
    } else if (std::strcmp(op, "stop") == 0) {
      castle_scenes::stop();
      print_state("stop");
    } else if (std::strncmp(op, "raw:", 4) == 0) {
      const bool ok = castle_cues::load(arg_of(op), 0, dir);
      std::printf("raw loaded=%d cues=%u\n", ok ? 1 : 0,
                  (unsigned) castle_cues::count());
    } else if (std::strcmp(op, "base") == 0) {
      castle_cues::apply_base(px);
      std::printf("base");
      for (int z = 0; z < 3; z++) std::printf(" %d/%.2f", effect[z], level[z]);
      std::printf("\n");
    } else if (std::strcmp(op, "zones") == 0) {
      std::printf("zones");
      for (int z = 0; z < 3; z++) std::printf(" %d/%.2f", effect[z], level[z]);
      std::printf("\n");
    } else if (std::strncmp(op, "tick:", 5) == 0) {
      const int n = castle_cues::tick(px, true, atoll(arg_of(op)));
      std::printf("tick %d\n", n);
    } else if (std::strncmp(op, "rm:", 3) == 0) {
      const std::string path = std::string(dir) + "/" + arg_of(op);
      std::printf("rm %s %s\n", arg_of(op), std::remove(path.c_str()) == 0 ? "ok" : "fail");
    } else {
      std::printf("?%s\n", op);
      return 3;
    }
  }
  return 0;
}
