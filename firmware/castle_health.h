#pragma once
// Long-term stability telemetry: who rebooted us, how often, and a paper
// trail that survives power loss.
//
// The boot log ring (boot_log.h) answers "what happened THIS boot"; this file
// answers "how has the season been going" — the question you ask in November
// when the porch prop has been power-cycled nightly for six weeks. Counters
// live in NVS (same flash the WiFi credentials use, already initialised by
// ESPHome), and one line per boot is appended to the SD card once the card is
// up — safely after the fragile window, which is the ring's job to cover.

#include <cstdio>
#include <sys/stat.h>
#include <unistd.h>
#include <nvs.h>
#include <esp_system.h>
#include "esphome/core/log.h"

namespace castle_health {

static const char *const TAG = "health";

inline uint32_t g_boots = 0;
inline uint32_t g_crashes = 0;
inline esp_reset_reason_t g_reason = ESP_RST_UNKNOWN;

inline const char *reason_str() {
  switch (g_reason) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_SW: return "software";        // OTA restarts land here
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "int-watchdog";
    case ESP_RST_TASK_WDT: return "task-watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep-sleep";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO: return "sdio";
    default: return "unknown";
  }
}

/// True for the reset reasons that mean "the firmware fell over" rather than
/// "someone turned it off/on or flashed it".
inline bool was_crash() {
  return g_reason == ESP_RST_PANIC || g_reason == ESP_RST_INT_WDT ||
         g_reason == ESP_RST_TASK_WDT || g_reason == ESP_RST_WDT ||
         g_reason == ESP_RST_BROWNOUT;
}

/// Call once, early. Bumps the boot counter; bumps the crash counter when the
/// previous life ended badly.
inline void init() {
  g_reason = esp_reset_reason();
  nvs_handle_t h;
  if (nvs_open("castle", NVS_READWRITE, &h) != ESP_OK) {
    ESP_LOGW(TAG, "NVS unavailable — counters lost");
    return;
  }
  nvs_get_u32(h, "boots", &g_boots);
  nvs_get_u32(h, "crashes", &g_crashes);
  g_boots++;
  if (was_crash()) g_crashes++;
  nvs_set_u32(h, "boots", g_boots);
  nvs_set_u32(h, "crashes", g_crashes);
  nvs_commit(h);
  nvs_close(h);
  ESP_LOGI(TAG, "boot #%u (%u crash%s so far), reset: %s", (unsigned) g_boots,
           (unsigned) g_crashes, g_crashes == 1 ? "" : "es", reason_str());
}

/// Append one line to the card's log, rotating at ~200 KB so six weeks of
/// nightly power cycles cannot fill anything. Call AFTER the card mounts —
/// a failure here is logged and shrugged off; the NVS counters are the truth.
inline void log_boot_to_sd(const char *version) {
  mkdir("/sd/logs", 0775);
  struct stat st{};
  if (stat("/sd/logs/castle.log", &st) == 0 && st.st_size > 200 * 1024) {
    unlink("/sd/logs/castle.log.1");
    rename("/sd/logs/castle.log", "/sd/logs/castle.log.1");
  }
  FILE *f = fopen("/sd/logs/castle.log", "a");
  if (f == nullptr) {
    ESP_LOGW(TAG, "cannot append to /sd/logs/castle.log");
    return;
  }
  fprintf(f, "boot #%u v%s reason=%s crashes=%u\n", (unsigned) g_boots,
          version, reason_str(), (unsigned) g_crashes);
  fclose(f);
}

}  // namespace castle_health
