#pragma once
// The castle's LOCAL time, from a POSIX TZ string the owner sets (v5.75,
// docs/PRODUCTION-TODO.md §1.4). Quiet hours are local times, and so is the
// clock the owner's page shows — an evening show is "after seven", never
// "after 02:00 UTC".
//
// WHY NOT setenv("TZ") + localtime_r. Three reasons, any one enough:
//   - ESPHome 2026.8 took setenv/tzset out of embedded builds to save flash
//     (esphome/core/time.cpp) and pre-parses the zone at COMPILE time — the
//     zone of whichever machine ran the build. A buyer's castle built by CI
//     would keep the runner's zone for ever.
//   - newlib's tzset parses with sscanf, and a zone that will not parse is
//     silently UTC: there is no "bad tz" to give the owner back.
//   - The C harness (tests/cxx) runs on the host's libc, not newlib. The code
//     the board runs would not be the code the tests run.
// So this is the whole job in ~150 lines, the same bytes on the board and in
// tests/cxx/tz_check.cpp; tools/castle_emu_tz.py is its port, and Python's
// zoneinfo is the oracle both are held to (tests/test_castle_tz_cxx.py).
//
// THE GRAMMAR (POSIX.1-2017 §8.3, minus the parts nobody's zone needs):
//   std offset [dst [offset] ,rule ,rule]
//   name    3+ letters, or <3+ of [A-Za-z0-9+-]> for "<+0530>"
//   offset  [+-]hh[:mm[:ss]], hh 0..24 — WEST of Greenwich, so "PST8" is
//           UTC-8, and dst defaults to one hour ahead of std
//   rule    Mm.w.d | Jn | n, then [/time]; time is [+-]hh[:mm[:ss]] with hh
//           0..167 (RFC 8536's extension, which tzdata itself emits)
// A dst name WITHOUT rules is refused rather than guessed: glibc assumes the
// US rules, newlib assumes something else, and an owner pasting "EST5EDT"
// deserves a "bad tz" and the full string, not a castle an hour out half the
// year. No chars outside the grammar, nothing longer than kTzMax.

#include <cstdint>
#include <ctime>
#include <string_view>

namespace castle_tz {

inline constexpr size_t kTzMax = 63;

/// Jn counts 1..365 and never Feb 29; n counts 0..365 and does.
enum class RuleKind : uint8_t { JULIAN1, JULIAN0, MONTH };

struct Rule {
  RuleKind kind = RuleKind::MONTH;
  int day = 0;           // Jn / n: the day; M: the weekday, 0 = Sunday
  int month = 0;         // M only, 1..12
  int week = 0;          // M only, 1..5 (5 = the last one in the month)
  int32_t secs = 7200;   // local wall time of the change, default 02:00
};

struct Zone {
  int32_t std_east = 0;  // seconds EAST of UTC (POSIX spells west)
  int32_t dst_east = 0;
  bool has_dst = false;
  Rule start, end;
};

/// One broken-down local time. Our own struct, not `struct tm`, so nothing
/// here depends on what a platform's libc fills in.
struct Local {
  int year = 1970, month = 1, day = 1, hour = 0, minute = 0, second = 0;
  int wday = 4;          // 0 = Sunday; 1970-01-01 was a Thursday
  bool dst = false;
};

namespace detail {

inline bool digit(char c) { return c >= '0' && c <= '9'; }
inline bool alpha(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }

inline bool name(std::string_view s, size_t &i) {
  const size_t from = i;
  if (i < s.size() && s[i] == '<') {
    for (i++; i < s.size() && s[i] != '>'; i++)
      if (!alpha(s[i]) && !digit(s[i]) && s[i] != '+' && s[i] != '-') return false;
    if (i >= s.size() || i - from - 1 < 3) return false;
    i++;   // the '>'
    return true;
  }
  while (i < s.size() && alpha(s[i])) i++;
  return i - from >= 3;
}

/// Up to `max_digits` decimal digits as a number no bigger than `hi`.
inline bool number(std::string_view s, size_t &i, int max_digits, int hi, int &out) {
  const size_t from = i;
  out = 0;
  while (i < s.size() && digit(s[i]) && (int) (i - from) < max_digits)
    out = out * 10 + (s[i++] - '0');
  return i > from && out <= hi;
}

/// [+-]hh[:mm[:ss]] as seconds, with hh at most `max_h`.
inline bool hms(std::string_view s, size_t &i, int max_h, int32_t &out) {
  int sign = 1;
  if (i < s.size() && (s[i] == '+' || s[i] == '-')) sign = s[i++] == '-' ? -1 : 1;
  int h = 0, m = 0, sec = 0;
  if (!number(s, i, 3, max_h, h)) return false;
  if (i < s.size() && s[i] == ':') {
    i++;
    if (!number(s, i, 2, 59, m)) return false;
    if (i < s.size() && s[i] == ':') {
      i++;
      if (!number(s, i, 2, 59, sec)) return false;
    }
  }
  out = sign * (h * 3600 + m * 60 + sec);
  return true;
}

inline bool rule(std::string_view s, size_t &i, Rule &r) {
  if (i < s.size() && s[i] == 'M') {
    i++;
    r.kind = RuleKind::MONTH;
    if (!number(s, i, 2, 12, r.month) || r.month < 1) return false;
    if (i >= s.size() || s[i++] != '.') return false;
    if (!number(s, i, 1, 5, r.week) || r.week < 1) return false;
    if (i >= s.size() || s[i++] != '.') return false;
    if (!number(s, i, 1, 6, r.day)) return false;
  } else if (i < s.size() && s[i] == 'J') {
    i++;
    r.kind = RuleKind::JULIAN1;
    if (!number(s, i, 3, 365, r.day) || r.day < 1) return false;
  } else {
    r.kind = RuleKind::JULIAN0;
    if (!number(s, i, 3, 365, r.day)) return false;
  }
  r.secs = 7200;
  if (i < s.size() && s[i] == '/') {
    i++;
    if (!hms(s, i, 167, r.secs)) return false;
  }
  return true;
}

/// Days from 1970-01-01 to y-m-d (proleptic Gregorian), Hinnant's algorithm.
inline int64_t days_from_civil(int64_t y, int m, int d) {
  y -= m <= 2;
  const int64_t era = (y >= 0 ? y : y - 399) / 400;
  const int64_t yoe = y - era * 400;
  const int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + doe - 719468;
}

inline void civil_from_days(int64_t z, int &y, int &m, int &d) {
  z += 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const int64_t doe = z - era * 146097;
  const int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  const int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const int64_t mp = (5 * doy + 2) / 153;
  d = (int) (doy - (153 * mp + 2) / 5 + 1);
  m = (int) (mp < 10 ? mp + 3 : mp - 9);
  y = (int) (yoe + era * 400 + (m <= 2));
}

inline bool leap(int y) { return (y % 4 == 0 && y % 100 != 0) || y % 400 == 0; }

inline int month_days(int y, int m) {
  static constexpr int kDays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  return m == 2 && leap(y) ? 29 : kDays[m - 1];
}

/// The day (days since the epoch) rule `r` falls on in year `y`.
inline int64_t rule_day(const Rule &r, int y) {
  const int64_t jan1 = days_from_civil(y, 1, 1);
  if (r.kind == RuleKind::JULIAN0) return jan1 + r.day;
  if (r.kind == RuleKind::JULIAN1) return jan1 + r.day - 1 + (leap(y) && r.day >= 60);
  const int64_t first = days_from_civil(y, r.month, 1);
  const int wd_first = (int) ((first % 7 + 11) % 7);   // 0 = Sunday
  int mday = 1 + (r.day - wd_first + 7) % 7 + 7 * (r.week - 1);
  while (mday > month_days(y, r.month)) mday -= 7;
  return first + mday - 1;
}

}  // namespace detail

/// The whole string, or false. `out` is only meaningful on true.
inline bool parse(std::string_view s, Zone &out) {
  out = Zone{};
  if (s.empty() || s.size() > kTzMax) return false;
  size_t i = 0;
  int32_t west = 0;
  if (!detail::name(s, i) || !detail::hms(s, i, 24, west)) return false;
  out.std_east = -west;
  if (i == s.size()) return true;
  if (!detail::name(s, i)) return false;
  out.has_dst = true;
  out.dst_east = out.std_east + 3600;
  if (i < s.size() && s[i] != ',') {
    if (!detail::hms(s, i, 24, west)) return false;
    out.dst_east = -west;
  }
  if (i >= s.size() || s[i++] != ',') return false;   // no rules: refused
  if (!detail::rule(s, i, out.start)) return false;
  if (i >= s.size() || s[i++] != ',') return false;
  if (!detail::rule(s, i, out.end)) return false;
  return i == s.size();
}

/// UTC seconds → local wall time in zone `z`.
inline Local local(int64_t utc, const Zone &z) {
  int32_t east = z.std_east;
  bool dst = false;
  if (z.has_dst) {
    // The year as standard time sees it. A southern zone's summer spans
    // New Year, so both changes are taken from that same year and the test
    // flips: in DST from the start OR before the end.
    int y = 0, m = 0, d = 0;
    detail::civil_from_days((utc + z.std_east) / 86400 - ((utc + z.std_east) % 86400 < 0), y, m, d);
    const int64_t on = detail::rule_day(z.start, y) * 86400 + z.start.secs - z.std_east;
    const int64_t off = detail::rule_day(z.end, y) * 86400 + z.end.secs - z.dst_east;
    dst = on < off ? (utc >= on && utc < off) : (utc >= on || utc < off);
    if (dst) east = z.dst_east;
  }
  const int64_t t = utc + east;
  int64_t days = t / 86400;
  int64_t rem = t % 86400;
  if (rem < 0) {
    rem += 86400;
    days--;
  }
  Local out;
  detail::civil_from_days(days, out.year, out.month, out.day);
  out.hour = (int) (rem / 3600);
  out.minute = (int) (rem % 3600 / 60);
  out.second = (int) (rem % 60);
  out.wday = (int) ((days % 7 + 11) % 7);
  out.dst = dst;
  return out;
}

}  // namespace castle_tz
