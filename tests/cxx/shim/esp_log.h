#pragma once
// ESP_LOGx on the host: one line per call on stderr, so the harness's stdout
// stays a clean protocol stream and a failing case can still be read.
//
// The macros must really CONSUME their tag and arguments. A macro that threw
// them away would leave every `static const char *const TAG` in the firmware
// unused, and -Wall -Werror would refuse to compile the headers this harness
// exists to compile.
//
// No printf format attribute on purpose: the firmware's own %llu-with-
// uint64_t lines are correct for xtensa (where uint64_t is long long) and
// would warn on an LP64 host. Checking the device's format strings against
// the host's typedefs would fail the build over a non-bug.

#include <cstdarg>
#include <cstdio>

namespace castle_shim {

/// Set while the harness is answering a request, so a chatty handler cannot
/// interleave with a caller that is reading stderr for one case.
inline bool &log_enabled() {
  static bool on = true;
  return on;
}

inline void logf(char level, const char *tag, const char *fmt, ...) {
  if (!log_enabled()) return;
  fprintf(stderr, "[%c][%s] ", level, tag ? tag : "-");
  va_list ap;
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  va_end(ap);
  fputc('\n', stderr);
}

}  // namespace castle_shim

#define ESP_LOGE(tag, ...) ::castle_shim::logf('E', (tag), __VA_ARGS__)
#define ESP_LOGW(tag, ...) ::castle_shim::logf('W', (tag), __VA_ARGS__)
#define ESP_LOGI(tag, ...) ::castle_shim::logf('I', (tag), __VA_ARGS__)
#define ESP_LOGD(tag, ...) ::castle_shim::logf('D', (tag), __VA_ARGS__)
#define ESP_LOGV(tag, ...) ::castle_shim::logf('V', (tag), __VA_ARGS__)
