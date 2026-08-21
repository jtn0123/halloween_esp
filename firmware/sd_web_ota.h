// PUT /api/ota — new firmware over plain HTTP. Split from sd_web.h (500-line
// cap) along the one seam that handler never shared with the others: it is
// the only route that touches the flash, the only one that quiesces the rest
// of the device, and the only one whose success is a reboot. sd_web.h
// includes this and registers h_ota from its start().
#pragma once

#include <esp_http_server.h>
#include <esp_ota_ops.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <cstdlib>

#include "esphome/core/log.h"
#include "sd_audio.h"        // castle_sd::g_quiesce
#include "sd_web_state.h"    // set_pending(RESTART)

// reply_err/reply_json/TAG come from sd_web.h, which includes this header
// after defining them (the same arrangement as sd_web_site.h).
namespace castle_web {

// ── PUT /api/ota — new firmware over plain HTTP ─────────────────────────
// The web-only update path: no esphome CLI, no Mac — any browser or curl can
// deliver a .bin. Written straight into the inactive OTA slot; the reboot is
// queued through the main loop so the HTTP reply gets out first. The
// bootloader's rollback net still applies: an image that never confirms
// itself (API connect) is reverted on the next reboot.
inline esp_err_t h_ota(httpd_req_t *req) {
  const esp_partition_t *part = esp_ota_get_next_update_partition(nullptr);
  if (part == nullptr) return reply_err(req, "500 Internal Server Error", "no OTA slot");
  if (req->content_len < 65536 || req->content_len > part->size)
    return reply_err(req, "400 Bad Request", "implausible image size");

  // Nothing else may touch the SPI bus or burn CPU while flash is being
  // written — the v5.12 upload watchdogged twice, with audio already
  // stopped, because the eInk panel picked that moment to refresh.
  castle_sd::g_quiesce = true;

  esp_ota_handle_t ota;
  // SEQUENTIAL_WRITES, not the image size: passing a size makes ota_begin
  // erase the whole 1.75 MB partition in one synchronous burn — several
  // seconds with the flash cache suspended, which starves the idle task and
  // trips the task watchdog. (boots:4 crashes:1 last_reset:"task-watchdog"
  // — the health endpoint's first real case was this very handler.)
  // Sequential mode erases each block just before writing it: same result,
  // watchdog-sized pauses.
  if (esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &ota) != ESP_OK) {
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "ota begin failed");
  }

  static constexpr size_t CHUNK = 8192;
  char *buf = (char *) malloc(CHUNK);
  size_t remaining = req->content_len;
  bool first = true, ok = buf != nullptr;
  while (ok && remaining > 0) {
    int got = httpd_req_recv(req, buf, remaining < CHUNK ? remaining : CHUNK);
    if (got <= 0) { ok = false; break; }
    if (first) {
      first = false;
      if ((uint8_t) buf[0] != 0xE9) { ok = false; break; }   // app image magic
    }
    if (esp_ota_write(ota, buf, got) != ESP_OK) { ok = false; break; }
    remaining -= got;
    // Breathe. Every flash write suspends the cache, blocking every task
    // that executes from flash — including the watched main loop. Sequential
    // erase alone did not fix the watchdog (boots:6 crashes:2 says so);
    // back-to-back writes starve it just as well. One tick per chunk lets
    // the loop run and feed its own watchdog. ~140 chunks × 10 ms adds a
    // polite 1.5 s to the flash; the alternative is a reboot at 60%.
    vTaskDelay(1);
  }
  free(buf);
  if (!ok) {
    // A short body, bad magic or a write error leaves the handle open
    // unless it is abandoned explicitly — esp_ota_end would refuse the
    // partial image AND leave the slot locked until the next reboot, so
    // the following upload answered "ota begin failed" until then.
    esp_ota_abort(ota);
    ESP_LOGE(TAG, "web OTA failed with %u bytes left", (unsigned) remaining);
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "ota write failed");
  }
  if (esp_ota_end(ota) != ESP_OK) {   // validates the image; releases the handle either way
    ESP_LOGE(TAG, "web OTA: image rejected at end-of-write");
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "ota end failed");
  }
  if (esp_ota_set_boot_partition(part) != ESP_OK) {
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "could not select slot");
  }
  ESP_LOGI(TAG, "web OTA complete (%u bytes) — rebooting", (unsigned) req->content_len);
  // Reply BEFORE queueing the restart, and give lwip a beat to flush the
  // segment — the first live test flashed perfectly but rebooted with the
  // response still in the TCP buffer, so the client saw only a timeout.
  esp_err_t r = reply_json(req, "{\"flashed\":true,\"rebooting\":true}");
  vTaskDelay(pdMS_TO_TICKS(250));
  set_pending(RESTART, "");
  // Flash is written; the eInk task may move again. The restart is one
  // pending slot away, and a slot can be overwritten by the next request —
  // a castle that then failed to reboot must not stay frozen as well.
  castle_sd::g_quiesce = false;
  return r;
}

}  // namespace castle_web
