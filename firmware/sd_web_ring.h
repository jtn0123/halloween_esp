// The event ring and the counters reported through it — split out of
// sd_web_state.h, which had reached the 500-line cap (v5.67).
//
// The seam is a real one rather than arithmetic. sd_web_state.h is TWO ONE-WAY
// MAILBOXES between the httpd task and the main loop: a slot the desk writes
// and the loop drains, and a mirror the loop writes and the handlers read.
// What is here has a third shape — an append-only HISTORY of what the main
// loop did, plus the frame counters and the radio transition that are only
// interesting as lines in it. Nothing here reads the mailbox or the mirror,
// which is why sd_web_state.h can include it rather than the other way round.
//
// No httpd types, same as its parent: rendering /api/events is
// sd_web_events.h's job. No SD card either — a card write on the main loop
// stalls the audio pipeline and the pixel refill, which is the very class of
// glitch the ring exists to explain; the log on the card stays a boot-time
// affair (castle_health::log_boot_to_sd).
#pragma once

#include <esp_heap_caps.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <mutex>
#include <string>
#include <string_view>

#include "castle_rtc.h"

namespace castle_web {

// ── light frame counters ────────────────────────────────────────────────
// A synced import streams light frames faster than the 200 ms drain, so
// some of them never run. `applied` is what the main loop executed,
// `evicted` what the one slot dropped on the way in; /api/status carries
// both so a page can see it is over-sending instead of guessing.
//
// v5.60 (A6/C8): EVERY light frame that never reaches the main loop counts,
// not just the ones a later LIGHT replaced. Until now a frame that arrived
// behind a stop, a volume or a play was dropped silently, so a page could
// not reconcile "sent" against "applied + evicted" — the two numbers simply
// did not add up, and the one that was wrong was the one that looked right.
inline std::atomic<unsigned> g_light_applied{0};
inline std::atomic<unsigned> g_light_evicted{0};
// v5.63: cues in the card show loaded for the playing track (castle_cues.h),
// 0 when the track has none. A page that streams its own light frames reads
// this and keeps quiet: the castle is already running the song's lights.
inline std::atomic<unsigned> g_cues{0};
// ── the event ring (v5.59) ──────────────────────────────────────────────
// What the castle actually DID, 64 entries deep, in RAM and never growing.
// The main loop appends one line per command it executed; /api/events hands
// the ring back oldest-first. A page polling /api/status once a second can
// never see a scene that started and stopped between two polls — this is
// the record of the ticks in between.
//
// Nothing here writes the SD card. A card write on the main loop stalls the
// audio pipeline and the pixel refill, which is the very class of glitch
// the ring exists to explain; the log on the card stays a boot-time affair
// (castle_health::log_boot_to_sd).
// v5.62 (L1): the kind words and their numbering live in castle_rtc.h now,
// because the copy that survives a panic is written from there and read by
// castle_health.h — which is in castle.yaml's includes list and cannot see
// this header. One enum, two rings: the 48-byte-arg ring below, and the
// 10-character one in RTC slow memory that outlives the crash.
using EventKind = castle_rtc::Kind;

inline const char *event_kind_str(EventKind k) { return castle_rtc::kind_str(k); }

inline constexpr size_t kEventRing = 64;
//: Longest arg kept, NUL included. A scene id and a volume fit whole; a
//: 99-byte track name is truncated rather than growing the ring.
inline constexpr size_t kEventArgMax = 48;

struct Event {
  long long t_ms{0};             // uptime when the main loop ran it
  EventKind kind{EventKind::STOP};
  char arg[kEventArgMax]{};
  //: A12: the arg did not fit and what is above is a PREFIX. /api/events is
  //: sold as the record of what the castle actually did, and a 99-character
  //: track name came back cut to 47 with nothing to say so — a reader
  //: comparing it against /api/files saw two different songs.
  bool trunc{false};
};

//: J3 (grade report 2026-09-17): the ring is 64 × 64 B and used to be a
//: static std::array — 4 KB of internal RAM held for the life of the boot,
//: against a rule that says PSRAM for buffers. It is one allocation now, the
//: way boot_log.h does it, made on the first event rather than from a boot
//: hook so no ordering has to be arranged for it.
//:
//: The fallback is a SMALLER static ring, not a null pointer: a board with no
//: PSRAM (Feather #5323) still has to be able to say what it did, and 16
//: lines of history beats none. /api/events is unchanged either way — it
//: reports what is live, which is all it ever claimed to.
inline constexpr size_t kEventRingSmall = 16;
inline std::mutex g_events_mu;
inline Event *g_events = nullptr;
inline size_t g_event_slots = 0;
inline std::array<Event, kEventRingSmall> g_events_small{};
//: Total ever recorded; the live window is the last g_event_slots of them.
inline size_t g_events_written = 0;

/// First call wins; every later one is a pointer test. Called under
/// g_events_mu, so two tasks racing the first event cannot both allocate.
inline void events_init() {
  if (g_events != nullptr) return;
  g_events =
      static_cast<Event *>(heap_caps_calloc(kEventRing, sizeof(Event), MALLOC_CAP_SPIRAM));
  if (g_events != nullptr) {
    g_event_slots = kEventRing;
    return;
  }
  g_events = g_events_small.data();
  g_event_slots = kEventRingSmall;
}

inline void record_event(EventKind kind, std::string_view arg, long long now_us) {
  {
    std::scoped_lock lk(g_events_mu);
    events_init();
    Event &e = g_events[g_events_written % g_event_slots];
    e.t_ms = now_us / 1000;
    e.kind = kind;
    const size_t n = std::min(arg.size(), kEventArgMax - 1);
    if (n > 0) memcpy(e.arg, arg.data(), n);
    e.arg[n] = '\0';
    e.trunc = n < arg.size();
    g_events_written++;
  }
  // L1 (v5.62): the same line, compactly, in RTC slow memory — the only
  // copy that is still there after a panic or a watchdog reset. Outside the
  // lock above because castle_rtc has its own and the two are never nested.
  char small[castle_rtc::kArg]{};
  const size_t m = std::min(arg.size(), castle_rtc::kArg - 1);
  if (m > 0) memcpy(small, arg.data(), m);
  castle_rtc::record(kind, small, now_us);
}

/// Oldest first into `out` (which must hold kEventRing entries); returns how
/// many are live. A handler copies rather than formats under the lock: the
/// main loop must never wait on a browser.
inline size_t copy_events(Event *out) {
  std::scoped_lock lk(g_events_mu);
  if (g_events == nullptr) return 0;   // nothing has happened yet
  const size_t held = std::min(g_events_written, g_event_slots);
  const size_t first = g_events_written - held;
  for (size_t i = 0; i < held; i++) out[i] = g_events[(first + i) % g_event_slots];
  return held;
}

// ── light frame counters, the main loop's side ──────────────────────────
inline unsigned g_light_evicted_seen = 0;      // main loop only
inline long long g_light_evict_event_us = 0;   // main loop only

/// Main loop, once per tick: at most ONE light_evicted event per second,
/// carrying the number of frames dropped since the last one. Unrated, a
/// 4 Hz import would push every other event out of a 64-entry ring.
inline void note_light_evictions(long long now_us) {
  const unsigned total = g_light_evicted.load();
  if (total == g_light_evicted_seen) return;
  if (g_light_evict_event_us != 0 && now_us - g_light_evict_event_us < 1000000) return;
  record_event(EventKind::LIGHT_EVICTED, std::to_string(total - g_light_evicted_seen),
               now_us);
  g_light_evicted_seen = total;
  g_light_evict_event_us = now_us;
}

// ── L6 (v5.62): the network, in the ring ────────────────────────────────
// castle.yaml's `wifi:` block is the CORE — shared by every build, none of
// which is guaranteed the web layer — so the transition is watched from the
// mirror tick that already runs beside the ring rather than from an
// on_connect trigger the core would have to know about. One bool compare
// per 200 ms, and the ring finally says when the castle fell off the air:
// "it stopped answering at 21:14" is a different fault from "it crashed".
inline std::atomic g_rssi{0};           // dBm, 0 = not associated
inline bool g_wifi_seen = false;        // main loop only
inline bool g_wifi_known = false;       // main loop only: no line for boot

inline void mirror_wifi(bool connected, int rssi, long long now_us) {
  g_rssi.store(connected ? rssi : 0);
  if (g_wifi_known && connected == g_wifi_seen) return;
  // The first tick is the state at boot, not a transition — but an UP is
  // still worth a line, because it is the moment the API became reachable.
  if (!g_wifi_known && !connected) {
    g_wifi_known = true;
    g_wifi_seen = false;
    return;
  }
  g_wifi_known = true;
  g_wifi_seen = connected;
  record_event(connected ? EventKind::WIFI_UP : EventKind::WIFI_DOWN,
               connected ? std::to_string(rssi) : "", now_us);
}
}  // namespace castle_web
