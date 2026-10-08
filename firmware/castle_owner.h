#pragma once
// The owner's clock and the owner's limits (v5.75, docs/PRODUCTION-TODO.md
// §1.3 and §1.4): a timezone, a volume cap and quiet hours. Each was a
// compile-time fact or nothing at all; each is an NVS pref now, set through
// POST /api/settings (sd_web_prefs.h, which loads and saves them) behind the
// castle key when one is set.
//
// ALL THREE ARE OFF UNTIL SET, and off is exactly the castle before v5.75:
// no zone is UTC (which is what the board's libc clock already was — ESPHome
// never set TZ on it, see castle_tz.h), a cap of 100 is no cap, and no
// window is no quiet hours. The yard build behaves as it did.
//
// QUIET HOURS SILENCE THE SPEAKER AND NOTHING ELSE. Between the two local
// times the castle's volume is held at 0: a scene, a song, the evening
// playlist and the PIR all still run, lights and all — the porch stays a
// show, it just stops making noise at the neighbours. Decided that way
// because "lights off at night" is already a blackout or a stopped show,
// while "quiet but alive" was not something the castle could do. When the
// window ends, the level it took away is given back.
//
// No clock, no quiet hours. Until SNTP has answered (the same 2020 line
// castle_health::stamp draws) the castle cannot know whether it is 3 am, and
// a castle on a network with no internet would otherwise be mute for ever.
// The owner's page shows the clock, so "it is not being quiet" comes with
// its reason on the same screen.
//
// THE SPLIT. The httpd task writes the settings (atomics, and the zone under
// a mutex); the main loop's 200 ms tick calls owner_tick(), which is the ONLY
// place the clock is read and the one place a volume is pulled down. The
// handlers never touch the media player — they cannot (sd_web.h's rule).

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <string>
#include <string_view>

#include "castle_tz.h"

namespace castle_web {

inline constexpr int kQuietOff = -1;
/// castle_health::stamp's line: before 2020 the RTC has not been set.
inline constexpr long long kClockSet = 1577836800;

inline std::mutex g_owner_mu;
inline std::string g_tz;                  // "" = never set = UTC; g_owner_mu
inline castle_tz::Zone g_zone;            // g_tz, parsed; g_owner_mu
inline std::string g_local;               // "YYYY-MM-DD HH:MM" or ""; g_owner_mu
inline std::atomic<int> g_vol_max{100};   // percent; 100 = no cap
/// The window, ONE number so no reader sees half of a change: from * 1440 +
/// to, both in minutes after local midnight, or kQuietOff.
inline std::atomic<int> g_quiet{kQuietOff};
inline std::atomic<bool> g_quiet_now{false};       // owner_tick's verdict
// The main loop's own two, never read off it.
inline bool g_was_quiet = false;
inline int g_quiet_took = -1;             // the level quiet hours pulled down

inline bool tz_ok(std::string_view s) {
  castle_tz::Zone z;
  return castle_tz::parse(s, z);
}

/// 1..100, digits only. 0 is refused: a cap of nothing is quiet hours that
/// never end, and the castle already has a blackout.
inline bool vol_max_ok(std::string_view v, int &out) {
  if (v.empty() || v.size() > 3 || v.find_first_not_of("0123456789") != std::string_view::npos)
    return false;
  out = 0;
  for (const char c : v) out = out * 10 + (c - '0');
  return out >= 1 && out <= 100;
}

/// "HH:MM-HH:MM" (a window that may cross midnight), or "off" — packed as
/// g_quiet holds it.
inline bool quiet_ok(std::string_view q, int &packed) {
  if (q == "off") {
    packed = kQuietOff;
    return true;
  }
  if (q.size() != 11 || q[2] != ':' || q[5] != '-' || q[8] != ':') return false;
  int v[4] = {0, 0, 0, 0};
  static constexpr size_t kAt[4] = {0, 3, 6, 9};
  for (int k = 0; k < 4; k++) {
    const char a = q[kAt[k]];
    const char b = q[kAt[k] + 1];
    if (a < '0' || a > '9' || b < '0' || b > '9') return false;
    v[k] = (a - '0') * 10 + (b - '0');
  }
  if (v[0] > 23 || v[1] > 59 || v[2] > 23 || v[3] > 59) return false;
  const int from = v[0] * 60 + v[1];
  const int to = v[2] * 60 + v[3];
  packed = from * 1440 + to;
  return from != to;
}

/// The window as /api/status and /api/settings spell it, "" when off.
inline std::string quiet_str() {
  const int q = g_quiet.load();
  if (q < 0) return "";
  const int from = q / 1440;
  const int to = q % 1440;
  std::array<char, 16> buf{};
  snprintf(buf.data(), buf.size(), "%02d:%02d-%02d:%02d", from / 60, from % 60,
           to / 60, to % 60);
  return buf.data();
}

/// Taken by prefs_load and /api/settings; "" is UTC. The caller has already
/// checked tz_ok — a string that will not parse never gets this far.
inline void set_tz(const std::string &tz) {
  castle_tz::Zone z;
  if (!tz.empty() && !castle_tz::parse(tz, z)) return;
  std::scoped_lock lk(g_owner_mu);
  g_tz = tz;
  g_zone = z;
}

inline std::string tz_copy() {
  std::scoped_lock lk(g_owner_mu);
  return g_tz;
}

inline std::string local_copy() {
  std::scoped_lock lk(g_owner_mu);
  return g_local;
}

/// The loudest the speaker may be RIGHT NOW, in percent: the cap, or 0
/// inside quiet hours. Every place the castle sets a level asks this — the
/// web VOLUME action and the scene runner — and owner_tick enforces it on
/// whatever got past them (a raw song keeps the level it inherited).
inline int volume_ceiling_pct() {
  return g_quiet_now.load() ? 0 : g_vol_max.load();
}

/// The web VOLUME action's level for right now (castle_web_actions.yaml):
/// through the cap, 0 inside quiet hours — where it is also remembered as
/// the level to give back when they end, because it is the newest thing
/// the owner asked for. Main loop only, like owner_tick.
inline int volume_for(int asked_pct) {
  if (g_was_quiet) g_quiet_took = asked_pct;
  return std::min(asked_pct, volume_ceiling_pct());
}

/// True when minute-of-day `m` is inside the packed window [from, to),
/// across midnight too.
inline bool in_quiet(int m, int packed) {
  if (packed < 0) return false;
  const int from = packed / 1440;
  const int to = packed % 1440;
  return from < to ? (m >= from && m < to) : (m >= from || m < to);
}

/// ONE main-loop tick: read the clock, decide whether this is quiet time,
/// publish the local time for /api/status, and return the level (percent)
/// the speaker must be set to now — or -1 to leave it as it is.
inline int owner_tick(long long utc, int volume_pct) {
  bool quiet = false;
  {
    std::scoped_lock lk(g_owner_mu);
    if (utc > kClockSet) {
      const castle_tz::Local t = castle_tz::local(utc, g_zone);
      std::array<char, 24> buf{};
      snprintf(buf.data(), buf.size(), "%04d-%02d-%02d %02d:%02d", t.year, t.month,
               t.day, t.hour, t.minute);
      g_local = buf.data();
      quiet = in_quiet(t.hour * 60 + t.minute, g_quiet.load());
    } else {
      g_local.clear();
    }
  }
  g_quiet_now.store(quiet);
  int pull = -1;
  if (quiet && !g_was_quiet) g_quiet_took = volume_pct;
  if (!quiet && g_was_quiet) {
    // Over: give back what the window took — through the cap, and only if
    // the speaker is still where the window left it.
    if (volume_pct == 0 && g_quiet_took > 0) pull = std::min(g_quiet_took, g_vol_max.load());
    g_quiet_took = -1;
  }
  g_was_quiet = quiet;
  if (pull < 0 && volume_pct > volume_ceiling_pct()) pull = volume_ceiling_pct();
  return pull;
}

/// Back to the box: no zone, no cap, no window (factory reset, sd_web_prefs.h).
inline void owner_reset() {
  set_tz("");
  g_vol_max.store(100);
  g_quiet.store(kQuietOff);
}

}  // namespace castle_web
