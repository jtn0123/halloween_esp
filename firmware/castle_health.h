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

#include <atomic>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <mutex>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <nvs.h>
#include <esp_ota_ops.h>
#include <esp_timer.h>
#include <esp_system.h>
#include "castle_rtc.h"
#include "esphome/core/log.h"

namespace castle_health {

static const char *const TAG = "health";

inline uint32_t g_boots = 0;
inline uint32_t g_crashes = 0;
inline esp_reset_reason_t g_reason = ESP_RST_UNKNOWN;

/// A8 (v5.61): reads off the card that FAILED, this boot.
///
/// Not in NVS with the two counters above, deliberately: those answer "how
/// has the season been going" and survive power loss, this one answers "is
/// the card going bad RIGHT NOW" and a reboot is exactly the event that
/// makes the old number meaningless. A flake mid-song is a truncated
/// response the listener hears as a stop (sd_web_site.h aborts the transfer
/// rather than framing a short body as a success); this is the number that
/// says it happened, and how often, without reading the log.
///
/// Written by the httpd tasks — two of them since sd_web_stream.h, three
/// since the upload worker — so it is an atomic rather than a bare unsigned.
inline std::atomic<unsigned> g_sd_read_errors{0};

/// L4 (v5.62): the count kept no story. "sd_read_errors: 3" says the card
/// misbehaved and not one word about WHERE, so the next question — is it one
/// bad file or the whole card — could not be asked without reading the log
/// that nobody reads. The last failing path and the offset it died at sit
/// beside the count now, and the count itself is mirrored into RTC memory
/// (castle_rtc.h) so the boot line after a reset can report the life that
/// just ended instead of a fresh zero.
inline std::mutex g_sd_mu;
inline std::string g_sd_last_path;      // guarded by g_sd_mu
inline unsigned long g_sd_last_offset = 0;

/// Called where a read failed, once per failed transfer: the path it was
/// serving and how many bytes had gone out before it tore.
inline void note_sd_read_error(const char *path, unsigned long offset) {
  g_sd_read_errors.fetch_add(1);
  {
    std::scoped_lock lk(g_sd_mu);
    g_sd_last_path = path == nullptr ? "" : path;
    g_sd_last_offset = offset;
  }
  castle_rtc::note_sd_error();
}

/// "<path>@<offset>", or "" when this boot has seen no read error. Read by
/// /api/health from the httpd task, so it copies under the lock.
inline std::string sd_last_error() {
  std::scoped_lock lk(g_sd_mu);
  if (g_sd_last_path.empty()) return "";
  return g_sd_last_path + "@" + std::to_string(g_sd_last_offset);
}

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

/// L2 (v5.62): an ISO stamp when SNTP has answered, and the uptime when it
/// has not. Before this every line the castle wrote said only how long the
/// board had been on, which cannot be lined up against "the porch went dark
/// some time after nine" — the one thing the operator actually remembers.
inline void stamp(char *out, size_t n) {
  // ::time — `esphome::time` is a component namespace and main.cpp pulls
  // esphome in wholesale, so the bare name does not resolve there.
  const time_t now = ::time(nullptr);
  // 2020-01-01. The RTC reads 1970 until sntp lands, and an epoch in the
  // seventies is worse than no stamp at all because it looks like one.
  if (now > 1577836800) {
    struct tm tm_now{};
    localtime_r(&now, &tm_now);
    strftime(out, n, "%Y-%m-%dT%H:%M:%S", &tm_now);
    return;
  }
  snprintf(out, n, "up+%llus",
           (unsigned long long) (esp_timer_get_time() / 1000000));
}

/// L8 (v5.62): which app slot is running, and whether the bootloader is
/// still holding it on probation. A castle that quietly rolled back to the
/// previous image looks exactly like one that never took the update — the
/// version string is the OLD one and nothing says why.
inline const char *ota_state_str() {
  const esp_partition_t *run = esp_ota_get_running_partition();
  if (run == nullptr) return "unknown";
  esp_ota_img_states_t st{};
  if (esp_ota_get_state_partition(run, &st) != ESP_OK) return "unknown";
  switch (st) {
    case ESP_OTA_IMG_NEW: return "new";
    case ESP_OTA_IMG_PENDING_VERIFY: return "pending-verify";
    case ESP_OTA_IMG_VALID: return "valid";
    case ESP_OTA_IMG_INVALID: return "invalid";
    case ESP_OTA_IMG_ABORTED: return "aborted";
    default: return "undefined";
  }
}

inline const char *running_label() {
  const esp_partition_t *run = esp_ota_get_running_partition();
  return run == nullptr ? "?" : run->label;
}

/// Call once, early. Bumps the boot counter; bumps the crash counter when the
/// previous life ended badly.
inline void init() {
  // BEFORE anything else touches it: decide whether the block in RTC slow
  // memory is the previous life's, while it still is (castle_rtc.h, L1).
  castle_rtc::begin();
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

/// Append the boot line to the card's log — and, since v5.62, the tail of
/// the previous life's event ring underneath it (L1). Rotates at ~200 KB so
/// six weeks of nightly power cycles cannot fill anything. Call AFTER the
/// card mounts and BEFORE the show starts — a failure here is logged and
/// shrugged off; the NVS counters are the truth.
///
/// This is the ONE card write outside an upload, and it stays that way: a
/// write on the main loop or from a request handler stalls the audio
/// pipeline and the pixel refill (sd_web_state.h, sd_web_events.h,
/// boot_log.h all say so). Nothing about the dump below changes that — it
/// happens once, in the boot window, and then the ring starts fresh.
/// The story is on the card (or there is no card): this life gets the ring
/// to itself, stamped with the slot it is running from. Called on BOTH
/// paths out of the boot lambda — a boot with no card must still not leave
/// the previous life's lines in the window for the next reset to re-dump.
inline void finish_boot() {
  castle_rtc::set_partition(running_label());
  castle_rtc::start_fresh();
}

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
    finish_boot();
    return;
  }
  char when[32]{};
  stamp(when, sizeof(when));
  // L4/L8: the card errors the life that just ended saw (the RAM count is
  // always 0 here — it died with that life), the slot this image is running
  // from and whether the bootloader still has it on probation.
  fprintf(f, "%s boot #%u v%s reason=%s crashes=%u sd_errors_prev=%u part=%s ota=%s\n",
          when, (unsigned) g_boots, version, reason_str(), (unsigned) g_crashes,
          (unsigned) castle_rtc::prev_sd_errors(), running_label(), ota_state_str());
  // A rollback is only visible by comparing slots across the reset: the
  // version string after one is the OLD image's and says nothing at all.
  const char *was = castle_rtc::prev_part();
  if (was[0] != '\0' && strcmp(was, running_label()) != 0)
    fprintf(f, "  the image changed slots across this reset: %s -> %s%s\n", was,
            running_label(), was_crash() ? " (after a crash: likely a rollback)" : "");
  const size_t held = castle_rtc::prev_held();
  if (held == 0) {
    fprintf(f, "  no previous life in RTC memory (cold boot or new ring layout)\n");
  } else {
    fprintf(f, "  the last %u things the previous life did (uptime ms, oldest first):\n",
            (unsigned) held);
    for (size_t i = 0; i < held; i++) {
      const castle_rtc::Slot &e = castle_rtc::prev_slot(i);
      fprintf(f, "  %10u %s %s\n", (unsigned) e.t_ms,
              castle_rtc::kind_str((castle_rtc::Kind) e.kind), e.arg);
    }
  }
  fclose(f);
  finish_boot();
}

}  // namespace castle_health
