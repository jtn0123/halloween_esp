#pragma once
// The FAT volume's numbers. Only one of these is load-bearing:
// esp_vfs_fat_info feeds sd_space_kb, which feeds /api/status's
// sd_free_kb AND write_body's 507 precondition — the check that refuses an
// upload before the first byte rather than after 80% of it.
//
// CASTLE_SD_TOTAL_KB / CASTLE_SD_FREE_KB name what the card claims, so the
// "not enough room" leg can be driven without filling a real disk. Unset,
// the host's own free space is reported, which is honest and never 507s.

#include <cstdint>
#include <string>
#include <sys/statvfs.h>

#include <castle_shim_fs.h>
#include <esp_err.h>
#include <sdmmc_cmd.h>

typedef struct {
  bool format_if_mount_failed;
  int max_files;
  size_t allocation_unit_size;
} esp_vfs_fat_sdmmc_mount_config_t;

inline esp_err_t esp_vfs_fat_info(const char *base_path, uint64_t *total_bytes,
                                  uint64_t *free_bytes) {
  const unsigned long t_kb = castle_shim::env_ul("CASTLE_SD_TOTAL_KB", 0);
  const unsigned long f_kb = castle_shim::env_ul("CASTLE_SD_FREE_KB", 0);
  if (t_kb != 0 || f_kb != 0) {
    *total_bytes = (uint64_t) t_kb * 1024u;
    *free_bytes = (uint64_t) f_kb * 1024u;
    return ESP_OK;
  }
  struct statvfs st {};
  if (statvfs(castle_shim::map_path(base_path).c_str(), &st) != 0) return ESP_FAIL;
  *total_bytes = (uint64_t) st.f_blocks * st.f_frsize;
  *free_bytes = (uint64_t) st.f_bavail * st.f_frsize;
  return ESP_OK;
}

// Mounting is compiled, never called — the card is a directory already.
// The slot config arrives as void* so this header need not know the SDSPI
// driver's struct, which sd_audio.h has already included by now.
inline esp_err_t esp_vfs_fat_sdspi_mount(const char *base_path,
                                         const sdmmc_host_t *host,
                                         const void *slot_config,
                                         const esp_vfs_fat_sdmmc_mount_config_t *cfg,
                                         sdmmc_card_t **out_card) {
  (void) base_path;
  (void) host;
  (void) slot_config;
  (void) cfg;
  (void) out_card;
  return ESP_FAIL;
}

inline esp_err_t esp_vfs_fat_sdcard_unmount(const char *base_path,
                                            sdmmc_card_t *card) {
  (void) base_path;
  (void) card;
  return ESP_OK;
}
