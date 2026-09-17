// The audio clock of firmware/sd_web_state.h, run on this machine.
//
// One mailbox tick per call, the way castle_sd_common.yaml's interval calls
// it: `playing` is the media pipeline's word, `sounding` the speaker's.
// tests/test_audio_clock_cxx.py compiles and runs this; a failed check
// prints its line and exits 1.
#include <cstdio>
#include <cstdlib>

#include "sd_web_state.h"

namespace {
int failures = 0;
void check(bool ok, const char *what, int line) {
  if (!ok) { std::printf("FAIL line %d: %s\n", line, what); failures++; }
}
#define CHECK(x) check((x), #x, __LINE__)
constexpr long long MS = 1000;   // microseconds per millisecond

long long pos() { return castle_web::g_position_ms.load(); }
bool playing() { return castle_web::g_playing.load(); }
}  // namespace

int main() {
  using castle_web::mirror_audio;
  using castle_web::restart_audio_clock;

  // Idle board: nothing plays, nothing counts, nothing ends.
  CHECK(!mirror_audio(false, false, 1000 * MS));
  CHECK(!playing() && pos() == 0);

  // A play command arms the clock: playing at once (the page shows
  // "starting"), position held at 0 while the speaker is silent.
  restart_audio_clock(2000 * MS);
  CHECK(playing() && pos() == 0);
  CHECK(!mirror_audio(true, false, 2200 * MS));
  CHECK(pos() == 0);
  CHECK(!mirror_audio(true, false, 2400 * MS));
  CHECK(pos() == 0);

  // First tick the speaker runs: the clock starts HERE, not at the command.
  CHECK(!mirror_audio(true, true, 2600 * MS));
  CHECK(pos() == 0);
  CHECK(!mirror_audio(true, true, 2800 * MS));
  CHECK(pos() == 200);
  CHECK(!mirror_audio(true, true, 3600 * MS));
  CHECK(pos() == 1000);

  // Track ends on its own: the tick reports it once, position drops to 0.
  CHECK(mirror_audio(false, false, 3800 * MS));
  CHECK(!playing() && pos() == 0);
  CHECK(!mirror_audio(false, false, 4000 * MS));

  // A pipeline that came alive WITHOUT a command (the PIR scene, the
  // installed show) arms the same way and starts on sound.
  CHECK(!mirror_audio(true, false, 5000 * MS));
  CHECK(playing() && pos() == 0);
  CHECK(!mirror_audio(true, true, 5400 * MS));
  CHECK(!mirror_audio(true, true, 5600 * MS));
  CHECK(pos() == 200);

  // Play-while-playing: the command re-arms even though the pipeline never
  // went idle; the clock restarts on the next sounding tick.
  restart_audio_clock(6000 * MS);
  CHECK(playing() && pos() == 0);
  CHECK(!mirror_audio(true, true, 6200 * MS));
  CHECK(pos() == 0);
  CHECK(!mirror_audio(true, true, 6400 * MS));
  CHECK(pos() == 200);

  // A speaker that never runs: the 5.52 behaviour, counting from the
  // command, so a stalled I2S task cannot freeze every scrubber at 0:00.
  // (The main loop applies that fallback after SOUND_WAIT; here the clock
  // simply keeps reading 0 until it is told otherwise.)
  restart_audio_clock(7000 * MS);
  CHECK(!mirror_audio(true, false, 9000 * MS));
  CHECK(pos() == 0);

  // A command whose sound is slow: the YAML mirrors BEFORE it drains the
  // mailbox, so the tick after a play asks about a pipeline that has not
  // started yet. An armed clock must read "starting", not "ended" — a
  // browser following the castle would clear the track and skip the song.
  restart_audio_clock(10000 * MS);
  CHECK(!mirror_audio(false, false, 10200 * MS));
  CHECK(playing() && pos() == 0);
  CHECK(!mirror_audio(false, false, 11000 * MS));
  CHECK(playing() && pos() == 0);
  // Sound arrives late but inside the grace: the clock starts there.
  CHECK(!mirror_audio(true, true, 11200 * MS));
  CHECK(pos() == 0);
  CHECK(!mirror_audio(true, true, 11400 * MS));
  CHECK(pos() == 200);
  CHECK(mirror_audio(false, false, 11600 * MS));
  CHECK(!playing());

  // A command whose sound never comes ends ONCE, after the grace, and the
  // board is idle from then on — not an "ended" every 200 ms tick.
  restart_audio_clock(20000 * MS);
  CHECK(!mirror_audio(false, false, 21400 * MS));
  CHECK(playing());
  CHECK(mirror_audio(false, false, 21600 * MS));
  CHECK(!playing() && pos() == 0);
  CHECK(!mirror_audio(false, false, 21800 * MS));
  CHECK(!mirror_audio(false, false, 30000 * MS));

  if (failures == 0) std::printf("audio clock OK\n");
  return failures == 0 ? 0 : 1;
}
