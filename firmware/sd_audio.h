// Mount the microSD card and say what is on it. That is the whole job.
//
// ESPHome has no SD component and its audio pipeline reads from exactly two
// places: a byte array in flash, or a URL over HTTP. There is no filesystem
// source — and there does not need to be one. The card is mounted here over
// SPI as a FATFS volume at /sd, and the castle's own web server
// (sd_web_site.h) serves any file on it at GET /sd/<path>. Playing a card
// file is then just the ordinary URL path, pointed at loopback:
//
//     media_player.make_call().set_media_url("http://127.0.0.1:8080/sd/<f>")
//
// — TRUE STREAMING: no PSRAM cap on track length, near-instant start, and
// the decoder pulls bytes from the card at exactly the rate it needs them.
// Loopback never touches the radio; the SPI read is the only real I/O.
// (castle_sd.yaml's PLAY action and its play_sd script are the two callers.)
//
// The earlier design — read the whole file into PSRAM and hand the decoder
// an AudioFile{ptr,len} — is gone. It capped a track at ~1.5 MB, froze the
// lights for the second the SPI read took, and at the end was never polled
// by anything. ~130 lines of dead code in the file nearest the RAM wall.
//
// WHAT THE CARD BUYS: it holds as many tracks as you like. Flash only ever
// holds one show's worth (~2.9 MB total), and every track competes with
// every other. The built-in scenes deliberately stay in flash: they are the
// show, they are small, and they must work when there is no card in the slot.

#pragma once

#include "esphome/core/log.h"

#include <driver/sdspi_host.h>
#include <driver/spi_common.h>
#include <esp_vfs_fat.h>
#include <sdmmc_cmd.h>
#include <cerrno>

#include <cstdio>
#include <cstring>
#include <string>
#include <dirent.h>
#include <sys/stat.h>

namespace castle_sd {

static const char *const TAG = "castle_sd";

inline sdmmc_card_t *g_card = nullptr;
inline bool g_mounted = false;
/// Raised by the web OTA while it burns flash. Background chores (the eInk
/// panel) must sit still: flash writes suspend the cache, and any ready task
/// above the main loop's priority eats the breathing ticks h_ota inserts so
/// the watchdog stays fed. sd_web_ota.h clears it on every way out of the
/// handler — failure, and success once the restart is queued.
inline volatile bool g_quiesce = false;

/// Mount the card. Safe to call when no card is present — it logs and returns
/// false, and the rest of the device carries on with the flash scenes.
inline bool mount(int cs, int sck, int mosi, int miso, int max_files = 4) {
  if (g_mounted) return true;

  sdmmc_host_t host = SDSPI_HOST_DEFAULT();
  // 20 MHz is conservative on purpose. FeatherWing SD sockets sit at the end
  // of stacked headers with no termination; the default 40 MHz reads fine on
  // a bench and intermittently on a porch.
  host.max_freq_khz = 20000;

  spi_bus_config_t bus{};
  bus.mosi_io_num = mosi;
  bus.miso_io_num = miso;
  bus.sclk_io_num = sck;
  bus.quadwp_io_num = -1;
  bus.quadhd_io_num = -1;
  bus.max_transfer_sz = 4096;

  esp_err_t err = spi_bus_initialize((spi_host_device_t) host.slot, &bus, SPI_DMA_CH_AUTO);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {   // INVALID_STATE = already up
    ESP_LOGE(TAG, "SPI bus init failed: %s", esp_err_to_name(err));
    return false;
  }

  sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
  slot.gpio_cs = (gpio_num_t) cs;
  slot.host_id = (spi_host_device_t) host.slot;

  esp_vfs_fat_sdmmc_mount_config_t mcfg{};
  mcfg.format_if_mount_failed = false;   // never reformat the user's card
  mcfg.max_files = max_files;
  mcfg.allocation_unit_size = 16 * 1024;

  err = esp_vfs_fat_sdspi_mount("/sd", &host, &slot, &mcfg, &g_card);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "no SD card mounted (%s) — flash scenes still work",
             esp_err_to_name(err));
    return false;
  }
  g_mounted = true;
  ESP_LOGI(TAG, "SD mounted: %s, %lluMB", g_card->cid.name,
           ((uint64_t) g_card->csd.capacity) * g_card->csd.sector_size / (1024 * 1024));
  return true;
}

/// Drop the filesystem and mount it again.
///
/// Exists because `opendir` came back EAGAIN — which ESP-IDF's FATFS layer
/// produces from FR_TIMEOUT, i.e. the volume lock was still held. A lock held
/// by an operation that never finished cannot be waited out; the mount has to
/// be torn down. Returns whatever the fresh mount said.
inline bool remount(int cs, int sck, int mosi, int miso) {
  if (g_mounted) {
    esp_err_t err = esp_vfs_fat_sdcard_unmount("/sd", g_card);
    ESP_LOGI(TAG, "unmount: %s", esp_err_to_name(err));
    g_mounted = false;
    g_card = nullptr;
  }
  return mount(cs, sck, mosi, miso);
}

/// Log what is actually on the card.
///
/// "Did it mount" is only half the question — a card that mounts but shows an
/// empty root means the files went somewhere else, or the card was formatted
/// in a way FATFS reads but the writer did not expect. Getting both answers
/// from one boot matters when each attempt costs a BOOT+RESET by hand.
inline void list_root(const char *dir = "/sd") {
  if (!g_mounted) {
    ESP_LOGW(TAG, "cannot list %s — no card mounted", dir);
    return;
  }
  DIR *d = opendir(dir);
  if (d == nullptr) {
    // errno is the whole diagnosis here. "Mounted but cannot open" is a
    // sentence with several very different causes — ENOENT means the VFS
    // registered under another path, ENOTDIR means something answered but is
    // not a directory, EIO means the card stopped talking after the mount —
    // and without the number they are indistinguishable from each other.
    ESP_LOGE(TAG, "mounted, but cannot open %s — errno %d (%s)",
             dir, errno, strerror(errno));
    // What the card said AT MOUNT TIME. CID and CSD are cached in the struct,
    // so this identifies the card but proves nothing about the link right now
    // — do not read it as "the card is still there".
    if (g_card != nullptr) {
      ESP_LOGI(TAG, "  card as identified at mount: %s, %lluMB, sector %u",
               g_card->cid.name,
               ((uint64_t) g_card->csd.capacity) * g_card->csd.sector_size / (1024 * 1024),
               (unsigned) g_card->csd.sector_size);
    }
    return;
  }
  int files = 0, playable = 0;
  struct dirent *e;
  while ((e = readdir(d)) != nullptr) {
    if (e->d_name[0] == '.') continue;          // skip . .. and Mac dotfiles
    char full[300];
    snprintf(full, sizeof(full), "%s/%s", dir, e->d_name);
    struct stat st {};
    long kb = (stat(full, &st) == 0) ? (long) (st.st_size / 1024) : -1;
    bool is_dir = (e->d_type == DT_DIR);
    std::string nm(e->d_name);
    for (auto &c : nm) c = tolower(c);
    bool audio = !is_dir && (nm.size() > 4) &&
                 (nm.rfind(".mp3") == nm.size() - 4 ||
                  nm.rfind(".wav") == nm.size() - 4);
    if (audio) playable++;
    files++;
    ESP_LOGI(TAG, "  %s%s  %ldKB%s", e->d_name, is_dir ? "/" : "", kb,
             audio ? "   <- playable" : "");
  }
  closedir(d);
  ESP_LOGI(TAG, "%s: %d entries, %d playable audio file(s)", dir, files, playable);
  if (files == 0) {
    ESP_LOGW(TAG, "card is mounted but empty — copy .mp3 files to its root");
  }
}

}  // namespace castle_sd
