// firmware/castle_heard.h and the cue walker that follows it (v5.72), run on
// this machine against a real cue file.
//
//   heard_check <dir>
//
// <dir>/song.cue holds four strikes, at 0, 100, 1000 and 2000 ms of song
// (tests/test_heard_clock_cxx.py writes it with tools/cue_file.py). A fake
// speaker reports its 10 ms DMA buffers the way ESPHome's I2S task does, and
// every case asserts WHEN, on the render tick, each record fires — which is
// the whole point of the heard clock: a light fires when the listener
// reaches its time, not when a stopwatch beside the speaker says so.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

#include "castle_cues.h"

namespace {
constexpr long long MS = 1000;
int effect[3], center[3], overlay[3], palette[3], flash_mode[3], flash_epoch[3];
float flash[3], flash_col[12], flash_decay[3], flash_target[3], flash_rise[3],
    level[3], phase[3];
const castle_cues::Pixels px{effect, flash, flash_col, flash_decay, flash_target,
                             flash_rise, level, center, overlay, palette, phase,
                             flash_mode, flash_epoch};
int failures = 0;

void expect(bool ok, const char *what, long long got) {
  if (ok) return;
  std::printf("FAIL %s (got %lld)\n", what, got);
  failures++;
}

/// A speaker whose first real sample reaches the amplifier at `first`, and
/// which then plays one 10 ms buffer every 10 ms except inside [stall_from,
/// stall_to): a decoder starved by the card, padding the DMA with silence.
struct Speaker {
  long long first, stall_from = -1, stall_to = -1, played = -1;
  void feed(long long now) {
    if (now < first) return;
    if (played < 0) {
      castle_heard::on_played(1, first);
      played = first;
    }
    for (; played + 10 * MS <= now; played += 10 * MS) {
      const long long end = played + 10 * MS;
      if (end > stall_from && end <= stall_to) continue;  // silence: nothing reported
      castle_heard::on_played(441, end);
    }
  }
};

/// Tick every 16 ms from `from` until every record has fired, and return
/// the tick each record fired on (the speaker runs from `from`).
std::vector<long long> walk(Speaker &spk, long long from) {
  std::vector<long long> fired;
  for (long long now = from; castle_cues::g_next < castle_cues::g_count && now < from + 5000 * MS;
       now += 16 * MS) {
    spk.feed(now);
    for (int n = castle_cues::tick(px, true, now); n > 0; n--) fired.push_back((now - from) / MS);
  }
  return fired;
}

bool load(const char *dir, long long at) {
  return castle_cues::load("song.mp3", at, dir) && castle_cues::count() == 4;
}
}  // namespace

int main(int argc, char **argv) {
  if (argc != 2) return 2;
  const char *dir = argv[1];

  // 1. The warm-up. The speaker goes RUNNING on five DMA buffers of silence
  //    and the decoder's first sample is heard 70 ms later: nothing fires
  //    until then, and each record fires when the LISTENER reaches it. The
  //    stopwatch v5.71 walked would have been 70 ms ahead, and says so.
  if (!load(dir, 0)) return 3;
  Speaker warm{70 * MS};
  std::vector<long long> fired = walk(warm, 0);
  if (fired.size() != 4) return 4;
  expect(fired[0] == 80, "the first cue waits for the first sample (tick 80)", fired[0]);
  expect(fired[1] == 176, "t=100 fires at 100 ms of sound, not of stopwatch", fired[1]);
  expect(fired[3] == 2080, "t=2000 still lands 70 ms late on the stopwatch", fired[3]);
  expect(castle_cues::g_sync_lead_ms == 70, "lead is the warm-up", castle_cues::g_sync_lead_ms);
  expect(castle_cues::g_sync_drift_ms <= 1, "a steady song does not drift",
         castle_cues::g_sync_drift_ms);

  // 2. A starved decoder: 200 ms of silence padding at 600 ms. The sound
  //    falls 200 ms behind for good, and so do the lights — a stopwatch
  //    would have run on and fired every later light 200 ms early.
  const long long t2 = 10000 * MS;
  if (!load(dir, t2)) return 3;
  Speaker starved{t2 + 70 * MS, t2 + 600 * MS, t2 + 800 * MS};
  fired = walk(starved, t2);
  if (fired.size() != 4) return 4;
  expect(fired[2] >= 1270 && fired[2] < 1286, "t=1000 waits out the stall", fired[2]);
  expect(castle_cues::g_sync_lead_ms == 70, "lead is still the warm-up", castle_cues::g_sync_lead_ms);
  expect(castle_cues::g_sync_drift_ms >= 195 && castle_cues::g_sync_drift_ms <= 205,
         "drift is the stall", castle_cues::g_sync_drift_ms);

  // 3. While the sound stops, the clock holds within three DMA buffers.
  const castle_heard::Played last = castle_heard::read();
  const long long held = castle_heard::position_ms(last.at_us + 500 * MS);
  expect(held - castle_heard::position_ms(last.at_us) == 30, "hold is 30 ms", held);

  // 4. A new song owns nothing the last one played, and a speaker that never
  //    reports a sample falls back to the stopwatch after kHeardWaitMs.
  if (!load(dir, 20000 * MS)) return 3;
  expect(castle_cues::g_sync_lead_ms == -1, "a load forgets the last song's lead",
         castle_cues::g_sync_lead_ms);
  expect(castle_heard::position_ms(t2 + 6000 * MS) == -1, "nothing of it heard", 0);
  Speaker mute{1LL << 60};
  fired = walk(mute, 20000 * MS);
  if (fired.size() != 4) return 4;
  expect(fired[0] == 1008, "a silent speaker falls back at 1 s (tick 1008)", fired[0]);
  expect(castle_cues::g_sync_lead_ms == -1, "and measures nothing", castle_cues::g_sync_lead_ms);

  // 5. The two numbers are read as one while the speaker's task writes them:
  //    at_us is always frames' own end time, never a torn pair. The real
  //    writer reports every 10 ms; this one every microsecond or so, which
  //    is still thousands of writes landing inside the reader's loop.
  castle_heard::g_frames.store(0);
  castle_heard::g_at_us.store(0);
  castle_heard::g_last = {0, 0};
  std::atomic<bool> done{false};
  std::thread writer([&done] {
    for (long long k = 1; k <= 100000; k++) {
      castle_heard::on_played(441, k * 10 * MS);
      const auto until = std::chrono::steady_clock::now() + std::chrono::microseconds(1);
      while (std::chrono::steady_clock::now() < until) {
      }
    }
    done.store(true);
  });
  long long torn = 0, reads = 0;
  while (!done.load()) {
    const castle_heard::Played p = castle_heard::read();
    if ((long long) (p.frames / 441) * 10 * MS != p.at_us) torn++;
    reads++;
  }
  writer.join();
  expect(torn == 0, "no torn reads", torn);
  expect(reads > 1000, "the reader raced the writer", reads);

  // 6. A writer stopped half way (an interrupt between its two stores) for
  //    longer than the reader will spin: the last whole pair, not the torn one.
  const castle_heard::Played whole = castle_heard::read();
  castle_heard::g_seq.fetch_add(1);
  castle_heard::g_frames.store(whole.frames + 441);
  const castle_heard::Played stuck = castle_heard::read();
  expect(stuck.frames == whole.frames && stuck.at_us == whole.at_us,
         "a stuck writer reads as the last whole pair", (long long) stuck.frames);
  castle_heard::g_at_us.store(whole.at_us + 10 * MS);
  castle_heard::g_seq.fetch_add(1);
  expect(castle_heard::read().frames == whole.frames + 441, "and the next whole pair after it",
         (long long) castle_heard::read().frames);

  castle_cues::unload();
  if (failures != 0) return 1;
  std::printf("heard clock OK\n");
  return 0;
}
