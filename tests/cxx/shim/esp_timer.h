#pragma once
// esp_timer_get_time: microseconds since boot, from a monotonic host clock.
//
// Two readers in the firmware and they want opposite things — /api/status
// prints it as uptime_s (a number the harness's caller ignores), and
// sd_space_kb caches the card's free space against it for 60 s.
//
// The clock therefore starts a few seconds in, not at zero. sd_space_kb's
// cache is primed with `at = -60 s` and refreshes when `now - at > 60 s`,
// STRICTLY greater: a clock reading exactly zero on the first request
// leaves the card's size at 0/0 forever, which is not a state any booted
// castle is ever in and would make the 507 precondition untestable.

#include <chrono>
#include <cstdint>

/// Microseconds the device had been up when the harness started.
inline constexpr int64_t kBootOffsetUs = 3 * 1000 * 1000;

inline int64_t esp_timer_get_time() {
  static const auto t0 = std::chrono::steady_clock::now();
  return kBootOffsetUs + std::chrono::duration_cast<std::chrono::microseconds>(
                             std::chrono::steady_clock::now() - t0)
                             .count();
}
