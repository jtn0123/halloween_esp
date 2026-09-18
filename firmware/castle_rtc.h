#pragma once
// L1 (v5.62): the one thing that survives the crash you are investigating.
//
// THE PROBLEM. Everything the castle knows about what it was doing lives in
// plain RAM: the 64-entry event ring (sd_web_state.h), the card's read-error
// count (castle_health.h), the boot-log ring (boot_log.h). A panic or a
// watchdog reset wipes all three, and the only trace left on the card is
// "boot #N ... crashes=M" — the count goes up and the story is gone. The
// next morning you know it fell over and nothing else.
//
// WHAT THIS IS. A compact copy of the event ring in RTC SLOW memory, which
// the digital core's reset does not clear: 64 entries x 16 B = 1 KB of the
// S3's 8 KB RTC slow segment, plus a small header. Not heap, not dram0 —
// the diet in castle.yaml is untouched by it. A magic word and a checksum
// over the whole block tell a warm reboot (the block is ours and intact)
// from a cold power-on (the segment is whatever the last power cycle left).
//
// WHO WRITES IT. castle_web::record_event mirrors every ring line here, and
// the callers that have no web layer in reach (the PIR, the buttons) call
// record() directly. WHO READS IT: castle_health::log_boot_to_sd, once,
// after the card mounts and before the show starts — the only card write
// this firmware makes outside an upload, and deliberately so (a card write
// on the main loop stalls the audio pipeline and the pixel refill, which is
// the very class of glitch the ring exists to explain).
//
// WHY ITS OWN HEADER. castle_health.h is in castle.yaml's includes list and
// must compile without the SD web layer; sd_web_state.h is in the SD
// build's. This is the piece both of them need, so it sits under both.

#include <cstdint>
#include <cstdio>
#include <cstddef>
#include <cstring>
#include <mutex>

#include <esp_attr.h>

namespace castle_rtc {

/// One line of the ring, as /api/events spells it. The RAM ring keeps a
/// 48-byte arg; this copy keeps 10 characters, which is every scene id the
/// show has and the head of a track name. 16 bytes flat, so the whole ring
/// is exactly 1 KB.
enum class Kind : uint8_t {
  PLAY = 0, SCENE, STOP, VOLUME, SHOW, BLACKOUT, RESTART,
  LIGHT_EVICTED,   // light frames dropped from the one slot, arg = how many
  SOUND,           // the speaker started: arg = the track, v5.62 (L10)
  SILENT,          // playback ended on its own: arg = elapsed ms (L10)
  // v5.62 (L3): the show's other three sources of action, which until now
  // were recorded by nothing at all — the ring only ever saw the web
  // mailbox, so a night driven by motion and buttons read as empty.
  SCENE_START,     // run_scene actually started one, whoever asked
  PIR,             // motion, and the scene it fired
  PIR_COOLDOWN,    // motion suppressed: still inside the cooldown
  PIR_OFF,         // motion while disarmed
  BUTTON,          // a physical button on the board
  // v5.62 (L6/L11): the network, and the requests the server refused.
  WIFI_UP,
  WIFI_DOWN,
  HTTP_ERR,
  // v5.67: the card could not say what a scene is, or had no cues for it,
  // and the castle fell back to its compiled-in look. The one failure the
  // card-loaded show has that the compiled one could not: added at the END
  // so a reboot into this firmware still reads the previous life's ring.
  SCENE_MISSING,
};

inline const char *kind_str(Kind k) {
  switch (k) {
    case Kind::PLAY: return "play";
    case Kind::SCENE: return "scene";
    case Kind::STOP: return "stop";
    case Kind::VOLUME: return "volume";
    case Kind::SHOW: return "show";
    case Kind::BLACKOUT: return "blackout";
    case Kind::RESTART: return "restart";
    case Kind::LIGHT_EVICTED: return "light_evicted";
    case Kind::SOUND: return "sound";
    case Kind::SILENT: return "silent";
    case Kind::SCENE_START: return "scene_start";
    case Kind::PIR: return "pir";
    case Kind::PIR_COOLDOWN: return "pir_cooldown";
    case Kind::PIR_OFF: return "pir_off";
    case Kind::BUTTON: return "button";
    case Kind::WIFI_UP: return "wifi_up";
    case Kind::WIFI_DOWN: return "wifi_down";
    case Kind::HTTP_ERR: return "http_err";
    case Kind::SCENE_MISSING: return "scene_missing";
  }
  return "";
}

inline constexpr size_t kRing = 64;
//: Characters of an arg kept beside the NUL. A scene id fits whole.
inline constexpr size_t kArg = 11;
//: "castle ring, layout 1". Change it when the struct below changes, or a
//: reboot into new firmware would read the old layout as a valid story.
inline constexpr uint32_t kMagic = 0x5CA5'7E01u;

struct Slot {
  uint32_t t_ms;     // uptime of the life that recorded it
  uint8_t kind;
  char arg[kArg];
};
static_assert(sizeof(Slot) == 16, "the ring is sized in whole 16-byte slots");

struct Block {
  uint32_t magic;
  uint32_t check;
  uint32_t written;     // total ever recorded; the live window is the last kRing
  uint32_t sd_errors;   // L4: card reads that failed, carried across the reset
  char part[16];        // L8: the app partition that life was running from
  Slot ev[kRing];
};
//: compute_check() walks `part` and `ev` as one run of bytes; no padding
//: may creep in between them, and the whole block must stay 1 KB + header.
static_assert(sizeof(Block) == 32 + sizeof(Slot) * kRing, "the block is packed");
static_assert(offsetof(Block, ev) == offsetof(Block, part) + 16, "part abuts ev");

/// RTC_NOINIT_ATTR, not RTC_DATA_ATTR: the bootloader must NOT re-initialise
/// this on a warm start, because surviving the warm start is the whole job.
inline RTC_NOINIT_ATTR Block g_rtc;

/// Set by begin() before anything this life overwrites: was the block a
/// previous life's, intact? False on a cold power-on (the segment is
/// whatever was left in it) and after a layout change.
inline bool g_prev_valid = false;
inline uint32_t g_prev_written = 0;
//: A copy, because set_partition() overwrites the live field long before
//: the boot line is written and the comparison would then always match.
inline char g_prev_part[16]{};

inline std::mutex g_mu;

inline uint32_t mix(uint32_t h, uint32_t v) {
  h ^= v;
  h *= 16777619u;
  return h;
}

/// FNV over the whole block but its own check word. 1 KB per append is a
/// few microseconds at 240 MHz and the ring records single-digit lines a
/// minute; an incremental sum would be cheaper and much easier to get
/// subtly wrong.
inline uint32_t compute_check() {
  uint32_t h = 2166136261u;
  h = mix(h, g_rtc.magic);
  h = mix(h, g_rtc.written);
  h = mix(h, g_rtc.sd_errors);
  const auto *p = reinterpret_cast<const unsigned char *>(g_rtc.part);
  for (size_t i = 0; i < sizeof(g_rtc.part) + sizeof(g_rtc.ev); i++) h = mix(h, p[i]);
  return h == 0 ? 1u : h;
}

inline void seal() { g_rtc.check = compute_check(); }

inline void clear() {
  memset(&g_rtc, 0, sizeof(g_rtc));
  g_rtc.magic = kMagic;
  seal();
}

/// Call once, early (castle_health::init). Decides whether the block in RTC
/// memory is a previous life's — and leaves it ALONE if it is, so the dump
/// after the card mounts still has it. Nothing records events between here
/// and there: the ring's writers are the main loop, the PIR and the
/// buttons, none of which run before boot priority -200.
inline void begin() {
  g_prev_valid = g_rtc.magic == kMagic && g_rtc.check == compute_check();
  g_prev_written = g_prev_valid ? g_rtc.written : 0;
  if (g_prev_valid) memcpy(g_prev_part, g_rtc.part, sizeof(g_prev_part));
  g_prev_part[sizeof(g_prev_part) - 1] = '\0';
  if (!g_prev_valid) clear();
}

/// How many of the previous life's lines are still in the window.
inline size_t prev_held() {
  return g_prev_written < kRing ? g_prev_written : kRing;
}

inline uint32_t prev_sd_errors() { return g_prev_valid ? g_rtc.sd_errors : 0; }
inline const char *prev_part() { return g_prev_part; }

/// One line, oldest-first index `i` into the previous life's window.
inline const Slot &prev_slot(size_t i) {
  const size_t first = g_prev_written - prev_held();
  return g_rtc.ev[(first + i) % kRing];
}

inline void record(Kind k, const char *arg, long long now_us) {
  std::scoped_lock lk(g_mu);
  Slot &s = g_rtc.ev[g_rtc.written % kRing];
  s.t_ms = (uint32_t) (now_us / 1000);
  s.kind = (uint8_t) k;
  const size_t n = arg == nullptr ? 0 : strnlen(arg, kArg - 1);
  if (n > 0) memcpy(s.arg, arg, n);
  memset(s.arg + n, 0, kArg - n);
  g_rtc.written++;
  seal();
}

/// L4: the card's read-error count, carried across the reset that the count
/// in RAM cannot survive. The boot line reports the PREVIOUS life's.
inline void note_sd_error() {
  std::scoped_lock lk(g_mu);
  g_rtc.sd_errors++;
  seal();
}

/// L8: which app slot this life is running from, so the boot line after a
/// rollback can say the image changed underneath you.
inline void set_partition(const char *label) {
  std::scoped_lock lk(g_mu);
  snprintf(g_rtc.part, sizeof(g_rtc.part), "%s", label == nullptr ? "" : label);
  seal();
}

/// Start this life's window at 0, keeping the header. Called once the
/// previous life's tail has been written to the card.
inline void start_fresh() {
  std::scoped_lock lk(g_mu);
  memset(g_rtc.ev, 0, sizeof(g_rtc.ev));
  g_rtc.written = 0;
  g_rtc.sd_errors = 0;
  seal();
}

}  // namespace castle_rtc
