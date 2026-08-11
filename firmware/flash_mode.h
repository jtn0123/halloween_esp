// Reboot into the ROM's USB download mode, from software.
//
// WHY THIS EXISTS: getting firmware onto this board has meant physically
// holding BOOT and tapping RESET. That is fine at a desk and useless the
// moment the castle is on a porch and you are not in the same building — and
// it has already blocked work twice.
//
// Neither usual escape hatch is available here. There is no USB serial
// console (the ESP32-S2 has no USB Serial/JTAG peripheral, so the application
// never enumerates a port), and OTA is off because the embedded audio makes
// the binary too large for two app slots.
//
// But the ROM bootloader checks a bit in an always-on RTC register during
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

}  // namespace castle_sd
