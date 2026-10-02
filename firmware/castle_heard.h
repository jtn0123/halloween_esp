#pragma once
// The heard clock (v5.72): where in the song the LISTENER is, counted from
// the samples the speaker has actually played.
//
// Until v5.71 a card show's clock started on the first 16 ms tick the
// speaker reported running, and then ran on esp_timer. Two things were wrong
// with that, and neither could be seen from outside the castle:
//
//   * "running" is not "heard". The I2S task goes to RUNNING after it has
//     preloaded its five 10 ms DMA buffers with SILENCE, and the decoder's
//     first real samples land behind those and behind however long the
//     decoder took to hand any over. Every light therefore fired at least
//     50 ms before its sound — by an amount that changed from play to play.
//   * A stopwatch does not stop. A card read that starves the decoder pads
//     the DMA with silence, the song falls behind the wall clock, and every
//     light after that fires early by the length of the gap, for the rest of
//     the song.
//
// ESPHome's I2S speaker already knows the truth: each time a DMA buffer
// finishes it reports how many REAL frames were in it and when the last of
// them left for the amplifier (speaker.h add_audio_output_callback — the
// timestamp is the ISR's, moved back over the buffer's trailing silence).
// Summing those frames is a position that counts only what was played:
// silence padding adds nothing, a stall stops it, and the first sample of a
// song is position 0 wherever in the pipeline's warm-up it came out.
//
// The callback runs in the speaker's own task (priority 19, either core);
// the reader is the main loop. One writer, one reader, and a pair of numbers
// that must be read together, so it is a sequence lock: the count is odd
// while a write is in flight and the reader retries.
#include <atomic>
#include <cstdint>

namespace castle_heard {

/// Between two reports the clock runs on, but only this far: three DMA
/// buffers. A healthy speaker reports every 10 ms, so this never binds while
/// the song plays — and when the decoder starves, the lights stop with the
/// sound within 30 ms instead of running ahead of it.
inline constexpr long long kHoldUs = 30000;

inline std::atomic<uint32_t> g_seq{0};
inline std::atomic<uint64_t> g_frames{0};   // real frames played since boot
inline std::atomic<long long> g_at_us{0};   // when the last of them was played
inline std::atomic<uint32_t> g_rate{44100}; // frames per second (castle_audio.yaml)
inline uint64_t g_base = 0;                 // main loop only: frames before this song

/// The speaker's audio-output callback: `frames` real frames finished
/// playing at `at_us` (esp_timer). The only writer.
inline void on_played(uint32_t frames, long long at_us) {
  g_seq.fetch_add(1);  // odd: a write is in flight
  g_frames.store(g_frames.load() + frames);
  g_at_us.store(at_us);
  g_seq.fetch_add(1);
}

struct Played {
  uint64_t frames;
  long long at_us;
};

/// The last pair read whole. The main loop is the only reader, so this is
/// its own; it is what a read that cannot get a clean pair falls back on.
inline Played g_last{0, 0};

/// The two numbers as one. A write is a few instructions, but the writer can
/// be interrupted in the middle of one for longer than a spin takes, so the
/// retries are bounded — and a read that runs out of them hands back the last
/// WHOLE pair rather than a torn one: at most one report old, never a count
/// from one buffer beside the time of another.
inline Played read() {
  for (int tries = 0; tries < 64; tries++) {
    const uint32_t before = g_seq.load();
    if (before & 1u) continue;
    const Played p{g_frames.load(), g_at_us.load()};
    if (g_seq.load() == before) {
      g_last = p;
      return p;
    }
  }
  return g_last;
}

/// A new song: everything played so far belongs to the one before it.
inline void arm() { g_base = read().frames; }

/// Milliseconds of THIS song the listener has heard at `now_us`, or -1 while
/// none of it has reached the amplifier.
inline long long position_ms(long long now_us) {
  const Played p = read();
  if (p.frames <= g_base) return -1;
  const uint32_t rate = g_rate.load() > 0 ? g_rate.load() : 44100;
  const long long played_us =
      (long long) ((p.frames - g_base) * 1000000ULL / rate);
  long long since = now_us - p.at_us;
  if (since < 0) since = 0;
  if (since > kHoldUs) since = kHoldUs;
  return (played_us + since) / 1000;
}

}  // namespace castle_heard
