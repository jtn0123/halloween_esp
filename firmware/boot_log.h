#pragma once
// Early-boot logs, held until something can ask for them.
//
// THE PROBLEM. The API is the only way logs leave this board — there is no
// usable serial console (see the logger block in castle.yaml for why, and for
// the crash that taught us). But the API is not up during boot, so everything
// before WiFi associates is invisible. That window contains the SD mount, its
// directory listing, and any component that fails during setup: exactly the
// things you want when the board comes up wrong.
//
// WHY NOT LOG TO THE SD CARD. It sounds like the obvious answer and it does not
// work for this. The card mounts at boot priority -200, near the end of setup —
// after the interesting part. A failure before that writes nothing, and a
// failure *of the card itself* obviously writes nothing. Worse, it would put
// SPI writes in the log path while the same bus is reading audio.
//
// WHAT THIS IS. A ring buffer in PSRAM, filled from the logger's own callback
// from the earliest boot priority ESPHome offers, dumped on demand once
// something is listening. A few KB of the 2 MB PSRAM, no bus traffic, and it
// captures the part nothing else can see.
//
// It is deliberately a *ring*: an early boot loop would otherwise fill any
// buffer with the same line and push out the cause. Keeping the most recent N
// and counting what was dropped keeps the tail, which is where the fault is.

#include <cstdio>
#include <cstring>
#include <esp_heap_caps.h>
#include "esphome/core/log.h"

namespace castle_log {

static const char *const TAG = "boot_log";

/// 48 x 128 B = 6 KB. Sized to be affordable from INTERNAL RAM, because that
/// is where it usually ends up: this allocates at boot priority 800, and PSRAM
/// is not mapped that early. Asking SPIRAM first and falling back is not
/// belt-and-braces — the fallback is the normal path, and the first attempt is
/// there for the day the init order changes.
constexpr size_t LINES = 48;
constexpr size_t WIDTH = 128;

inline char *g_buf = nullptr;
/// Total lines ever captured; the ring position is `g_head % LINES`.
inline size_t g_head = 0;
inline size_t g_dropped = 0;
/// Set while dumping, so printing the buffer does not append to it.
inline bool g_dumping = false;
/// Whether init() ever ran, and what it managed to get. Without this,
/// "buffer is null" cannot distinguish "init never ran" from "init ran
/// and both allocations failed" — two very different bugs.
inline bool g_init_called = false;
inline bool g_from_psram = false;

inline void init() {
  g_init_called = true;
  if (g_buf != nullptr) return;
  g_buf = static_cast<char *>(heap_caps_calloc(LINES, WIDTH, MALLOC_CAP_SPIRAM));
  g_from_psram = (g_buf != nullptr);
  if (g_buf == nullptr) {
    // Expected: PSRAM is not mapped at the priority this runs from. 6 KB of
    // internal RAM is affordable and is the whole point of the small ring.
    g_buf = static_cast<char *>(calloc(LINES, WIDTH));
  }
  if (g_buf == nullptr) {
    // Not fatal: the device runs fine without it, you just lose the boot log.
    ESP_LOGW(TAG, "no memory for the boot log — early logs will not be kept");
  }
}

inline void capture(int level, const char *tag, const char *message) {
  if (g_buf == nullptr || g_dumping) return;
  char *slot = g_buf + (g_head % LINES) * WIDTH;
  // The level is kept as its single-letter ESPHome prefix so a dumped line
  // reads like a log line rather than like a struct.
  static const char kLevel[] = {'?', 'E', 'W', 'I', 'C', 'D', 'V', 'V'};
  const char lv = (level >= 0 && level < (int) sizeof(kLevel)) ? kLevel[level] : '?';
  snprintf(slot, WIDTH, "[%c][%s] %s", lv, tag ? tag : "-", message ? message : "");
  g_head++;
  if (g_head > LINES) g_dropped = g_head - LINES;
}

/// Print everything held, oldest first. Safe to call repeatedly.
inline void dump() {
  if (g_buf == nullptr) {
    ESP_LOGW(TAG, "boot log unavailable — init %s, %u bytes wanted",
             g_init_called ? "ran but could not allocate" : "NEVER RAN",
             (unsigned) (LINES * WIDTH));
    return;
  }
  const size_t held = g_head < LINES ? g_head : LINES;
  g_dumping = true;
  ESP_LOGI(TAG, "──── boot log: %u lines held, %u dropped, in %s ────",
           (unsigned) held, (unsigned) g_dropped,
           g_from_psram ? "PSRAM" : "internal RAM");
  const size_t first = g_head < LINES ? 0 : g_head - LINES;
  for (size_t i = 0; i < held; i++) {
    ESP_LOGI(TAG, "  %s", g_buf + ((first + i) % LINES) * WIDTH);
  }
  ESP_LOGI(TAG, "──── end of boot log ────");
  g_dumping = false;
}

}  // namespace castle_log
