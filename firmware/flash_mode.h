// Reboot into the ROM's USB download mode, from software.
//
// WHY THIS EXISTS: getting firmware onto this board has meant physically
// holding BOOT and tapping RESET. That is fine at a desk and useless the
// moment the castle is on a porch and you are not in the same building — and
// it has already blocked work twice.
//
// OTA is the normal way in (PUT /api/ota, `make ota`) and has been since the
// all-in-flash build was retired: the SD build fits its slot with room to
// spare. This is the LAST resort, for when the network is gone and the
// application is the only thing still answering. It was the only other way
// on the ESP32-S2 Feather that ran the porch until 2026-09-17: that chip has
// no USB Serial/JTAG peripheral, so the application never enumerated a port.
// Every S3 build has the peripheral and a console on it (castle.yaml), so
// there IS a cable answer now — but the button is still nearer than the
// porch.
//
// The ROM bootloader, S2 and S3 alike, checks a bit in an always-on RTC
// register during
// early boot. That register survives a software reset, so setting it and
// restarting brings the chip up in download mode with its USB bootloader
// enumerated, ready for esptool over the wire.
//
// This is one-way on purpose. Once called, the device stays in download mode
// until something reflashes it — there is no application running to change
// its mind. So it belongs behind a deliberate action, never on a timer or an
// error path.

#pragma once

#include "esphome/core/log.h"

#include "esp_ota_ops.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/rtc_cntl_reg.h"

namespace castle_sd {

inline void reboot_to_download_mode() {
  REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
  esp_restart();
}

/// Confirm the running image, so the bootloader stops holding it on probation.
///
/// With CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE, a freshly-flashed OTA image
/// boots in PENDING_VERIFY. If it reboots without calling this, the bootloader
/// reverts to the previous image. That is the safety net for flashing a device
/// nobody can reach — a firmware that cannot get onto the network cannot
/// confirm itself, so it undoes itself.
///
/// Called from `api: on_client_connected`, deliberately, because a client
/// connecting proves everything the NEXT update depends on: the chip booted,
/// WiFi associated, and the API answers. Confirming at boot instead would
/// happily bless a brick.
///
/// v5.60 adds a SECOND trigger, for the same reason and on the same terms:
/// the first /api/status the web server answers (castle_sd_common.yaml's
/// 200 ms interval watches castle_web::g_status_served). The native API
/// client is a Home Assistant that this castle does not always have —
/// nothing on the porch requires one — and a firmware delivered by
/// PUT /api/ota to a castle with no HA was therefore never confirmed and
/// rolled back on the next power cycle, silently undoing an update that
/// worked. A served /api/status proves the identical chain: the chip
/// booted, WiFi associated, and the very server the next OTA arrives
/// through is answering. Still not at boot, still not on a timer.
///
/// Only the first call does work; after that the partition is no longer
/// pending and this is a cheap no-op.
inline void mark_firmware_healthy() {
  static bool confirmed = false;
  if (confirmed) return;

  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  if (running == nullptr || esp_ota_get_state_partition(running, &state) != ESP_OK) {
    confirmed = true;             // nothing to confirm; do not keep checking
    return;
  }
  if (state == ESP_OTA_IMG_PENDING_VERIFY) {
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
      ESP_LOGI("castle_ota", "image confirmed — rollback cancelled");
    } else {
      ESP_LOGW("castle_ota", "could not confirm image; it will roll back");
      return;                     // leave it pending: a retry may still succeed
    }
  }
  confirmed = true;
}

/// The factory reset's second half (POST /api/factory-reset, v5.74 — the
/// first half, forgetting the settings in memory and answering, is
/// sd_web_prefs.h). Runs on the httpd task once the reply is out: a moment
/// for the socket to flush, then the WHOLE NVS partition — Wi-Fi, the castle
/// key, boot_play, ESPHome's restored states, the season counters — and a
/// restart. esp_restart rather than App.safe_reboot, which would write
/// ESPHome's preferences straight back into the partition just erased. One
/// way, like download mode above, and behind the same kind of deliberate act.
inline void factory_reset_now() {
  ESP_LOGW("castle_reset", "factory reset: erasing NVS and restarting");
  vTaskDelay(pdMS_TO_TICKS(300));
  nvs_flash_deinit();
  nvs_flash_erase();
  esp_restart();
}

}  // namespace castle_sd
