// Play audio files from a microSD card.
//
// ESPHome has no SD component, and its audio pipeline reads from exactly two
// places: a byte array in flash, or a URL over HTTP. There is no filesystem
// source. But the flash path takes an `audio::AudioFile`, which is only:
//
//     struct AudioFile { const uint8_t *data; size_t length; AudioFileType type; };
//
// — a pointer and a length. So we don't need to touch the decoder at all. Read
// the file off the card into PSRAM, point an AudioFile at it, and hand it to
// the media player exactly as if it had come from flash.
//
// WHAT THIS IS NOT: streaming. The whole file lands in PSRAM before playback,
// so one track is capped by free PSRAM (~1.5 MB of the 2 MB, once buffers and
// the LED framebuffer have taken their share). At 96 kbps mono that's roughly
// two minutes. Real streaming would mean writing a custom AudioReader, which
// is a much larger job with much more to go wrong.
//
// WHAT IT BUYS: the card holds as many tracks as you like. Flash only ever
// holds one show's worth (~2.9 MB total), and every track competes with every
// other. Here the card is the library and PSRAM is the turntable.
//
// The built-in scenes deliberately stay in flash. They are the show, they are
// small, and they must work when there is no card in the slot.

#pragma once

#include "esphome/core/log.h"
#include "esphome/components/audio/audio.h"

#include <driver/sdspi_host.h>
#include <driver/spi_common.h>
#include <esp_vfs_fat.h>
#include <sdmmc_cmd.h>
#include <esp_heap_caps.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <dirent.h>
#include <sys/stat.h>

namespace castle_sd {

static const char *const TAG = "castle_sd";

// One slot. Playing a new file frees the previous one — two tracks would not
// fit in PSRAM anyway, and the media player only plays one at a time.
inline uint8_t *g_buf = nullptr;
inline esphome::audio::AudioFile g_file{};
inline sdmmc_card_t *g_card = nullptr;
inline bool g_mounted = false;

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
    ESP_LOGE(TAG, "mounted, but cannot open %s", dir);
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

// AudioFileType's members are compiled in conditionally — a build only gets
// the decoders its pipeline declares (see `format:` on the announcement
// pipeline in castle.yaml). Referencing WAV in an MP3-only build is a compile
// error, so every branch here is guarded by the same flag ESPHome uses.
//
// Worth knowing: WAV costs no decode CPU at all, it is a memcpy into the I2S
// ring. If MP3 stutters on this single-core board, adding WAV to the pipeline
// and putting .wav files on the card is the cheapest way out.
inline esphome::audio::AudioFileType type_from_name(const char *path) {
  using T = esphome::audio::AudioFileType;
  std::string p(path);
  for (auto &c : p) c = tolower(c);
  auto ends = [&p](const char *s) {
    size_t n = strlen(s);
    return p.size() >= n && p.compare(p.size() - n, n, s) == 0;
  };
#ifdef USE_AUDIO_WAV_SUPPORT
  if (ends(".wav")) return T::WAV;
#endif
#ifdef USE_AUDIO_FLAC_SUPPORT
  if (ends(".flac")) return T::FLAC;
#endif
#ifdef USE_AUDIO_MP3_SUPPORT
  if (ends(".mp3")) return T::MP3;
  return T::MP3;                     // sensible default for this build
#else
  ESP_LOGW(TAG, "%s: no MP3 decoder compiled in", path);
  return T::NONE;
#endif
}

/// Read a file from the card into PSRAM. Returns nullptr on any failure —
/// missing card, missing file, or not enough PSRAM — having logged why.
inline esphome::audio::AudioFile *load(const char *name) {
  if (!g_mounted) {
    ESP_LOGW(TAG, "load(%s): no card mounted", name);
    return nullptr;
  }
  char path[160];
  snprintf(path, sizeof(path), "/sd/%s", name);

  FILE *f = fopen(path, "rb");
  if (f == nullptr) {
    ESP_LOGW(TAG, "load: cannot open %s", path);
    return nullptr;
  }
  fseek(f, 0, SEEK_END);
  long len = ftell(f);
  fseek(f, 0, SEEK_SET);
  if (len <= 0) {
    ESP_LOGW(TAG, "load: %s is empty", path);
    fclose(f);
    return nullptr;
  }

  size_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
  if ((size_t) len + 32768 > free_psram) {
    ESP_LOGE(TAG, "load: %s is %ldKB but only %uKB PSRAM free — trim the track",
             path, len / 1024, (unsigned) (free_psram / 1024));
    fclose(f);
    return nullptr;
  }

  // Free the previous track only once we know this one will fit, so a failed
  // load doesn't also destroy what was working.
  if (g_buf != nullptr) {
    heap_caps_free(g_buf);
    g_buf = nullptr;
  }

  g_buf = (uint8_t *) heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
  if (g_buf == nullptr) {
    ESP_LOGE(TAG, "load: PSRAM alloc of %ldKB failed", len / 1024);
    fclose(f);
    return nullptr;
  }

  size_t got = fread(g_buf, 1, len, f);
  fclose(f);
  if (got != (size_t) len) {
    ESP_LOGE(TAG, "load: short read on %s (%u of %ld)", path, (unsigned) got, len);
    heap_caps_free(g_buf);
    g_buf = nullptr;
    return nullptr;
  }

  g_file.data = g_buf;
  g_file.length = len;
  g_file.file_type = type_from_name(name);
  ESP_LOGI(TAG, "loaded %s (%ldKB) into PSRAM", path, len / 1024);
  return &g_file;
}

}  // namespace castle_sd
