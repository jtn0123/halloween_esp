#pragma once
// The OTA slot, as a file. h_ota's whole shape is decided by two numbers —
// the 64 KB floor it will not go under and part->size, the slot it will not
// go over — and the slot differs per build (the S2 Feather's 4 MB layout
// against the S3 carrier's 8 MB one). CASTLE_OTA_SLOT names it, so one
// binary can rehearse either board's refusal.
//
// The writes themselves are counted, not stored: the handler's verdict
// turns on the length it was promised, the first byte's 0xE9 and whether
// the body ran out, and none of those needs the image kept. The count is
// what the harness prints so a test can say the flash really happened.

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

#include <esp_err.h>

typedef struct {
  uint32_t address;
  uint32_t size;
  char label[17];
} esp_partition_t;

typedef uint32_t esp_ota_handle_t;

/// esp_ota_begin's size argument. The firmware passes this constant rather
/// than the image length on purpose (see sd_web_ota.h's watchdog note).
#define OTA_WITH_SEQUENTIAL_WRITES ((size_t) 0xffffffff)
#define OTA_SIZE_UNKNOWN ((size_t) 0xffffffff)

namespace castle_shim {

/// The inactive app slot. Default 0x1C0000 — the S2 Feather's ota_1.
inline const esp_partition_t *ota_partition() {
  static esp_partition_t p{};
  static bool init = false;
  if (!init) {
    const char *v = getenv("CASTLE_OTA_SLOT");
    p.address = 0x210000;
    p.size = (uint32_t) ((v != nullptr && *v != '\0') ? strtoul(v, nullptr, 0)
                                                      : 0x1C0000ul);
    snprintf(p.label, sizeof(p.label), "ota_1");
    init = true;
  }
  return &p;
}

/// Bytes the last (or current) OTA wrote, and whether a handle is open.
inline unsigned long &ota_written() {
  static unsigned long n = 0;
  return n;
}
inline bool &ota_open() {
  static bool b = false;
  return b;
}

}  // namespace castle_shim

inline const esp_partition_t *esp_ota_get_next_update_partition(const esp_partition_t *) {
  // An empty CASTLE_OTA_SLOT of "0" is the board with no free slot at all.
  return castle_shim::ota_partition()->size == 0 ? nullptr
                                                 : castle_shim::ota_partition();
}

inline esp_err_t esp_ota_begin(const esp_partition_t *part, size_t size,
                               esp_ota_handle_t *out) {
  (void) part;
  (void) size;
  castle_shim::ota_written() = 0;
  castle_shim::ota_open() = true;
  *out = 1;
  return ESP_OK;
}

inline esp_err_t esp_ota_write(esp_ota_handle_t h, const void *data, size_t size) {
  (void) h;
  (void) data;
  castle_shim::ota_written() += size;
  return ESP_OK;
}

inline esp_err_t esp_ota_abort(esp_ota_handle_t h) {
  (void) h;
  castle_shim::ota_open() = false;
  return ESP_OK;
}

inline esp_err_t esp_ota_end(esp_ota_handle_t h) {
  (void) h;
  castle_shim::ota_open() = false;
  return ESP_OK;
}

inline esp_err_t esp_ota_set_boot_partition(const esp_partition_t *part) {
  (void) part;
  return ESP_OK;
}
