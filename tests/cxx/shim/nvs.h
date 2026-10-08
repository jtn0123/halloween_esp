#pragma once
// NVS, in memory. castle_health::init() keeps the season's boot and crash
// counters here, and since v5.74 the web layer keeps the owner's settings
// (sd_web_prefs.h: the castle key as a string, boot_play as a u32) — so the
// string half has to behave too, including the NOT_FOUND an erase of a key
// that was never set answers.

#include <cstdint>
#include <cstring>
#include <map>
#include <string>

#include <esp_err.h>

typedef uint32_t nvs_handle_t;

#define ESP_ERR_NVS_BASE 0x1100
#define ESP_ERR_NVS_NOT_FOUND (ESP_ERR_NVS_BASE + 0x02)

typedef enum {
  NVS_READONLY = 0,
  NVS_READWRITE = 1,
} nvs_open_mode_t;

namespace castle_shim {
inline std::map<std::string, uint32_t> &nvs_store() {
  static std::map<std::string, uint32_t> m;
  return m;
}
inline std::map<std::string, std::string> &nvs_strings() {
  static std::map<std::string, std::string> m;
  return m;
}
}  // namespace castle_shim

inline esp_err_t nvs_open(const char *ns, nvs_open_mode_t mode, nvs_handle_t *out) {
  (void) ns;
  (void) mode;
  *out = 1;
  return ESP_OK;
}

inline esp_err_t nvs_get_u32(nvs_handle_t h, const char *key, uint32_t *out) {
  (void) h;
  auto it = castle_shim::nvs_store().find(key);
  if (it == castle_shim::nvs_store().end()) return ESP_ERR_NVS_NOT_FOUND;
  *out = it->second;
  return ESP_OK;
}

inline esp_err_t nvs_set_u32(nvs_handle_t h, const char *key, uint32_t v) {
  (void) h;
  castle_shim::nvs_store()[key] = v;
  return ESP_OK;
}

/// IDF's contract: `len` in is the buffer, out is the length WITH the NUL.
inline esp_err_t nvs_get_str(nvs_handle_t h, const char *key, char *out, size_t *len) {
  (void) h;
  auto it = castle_shim::nvs_strings().find(key);
  if (it == castle_shim::nvs_strings().end()) return ESP_ERR_NVS_NOT_FOUND;
  const size_t need = it->second.size() + 1;
  if (out == nullptr) {
    *len = need;
    return ESP_OK;
  }
  if (*len < need) return ESP_ERR_INVALID_ARG;   // IDF: ESP_ERR_NVS_INVALID_LENGTH
  std::memcpy(out, it->second.c_str(), need);
  *len = need;
  return ESP_OK;
}

inline esp_err_t nvs_set_str(nvs_handle_t h, const char *key, const char *v) {
  (void) h;
  castle_shim::nvs_strings()[key] = v;
  return ESP_OK;
}

inline esp_err_t nvs_erase_key(nvs_handle_t h, const char *key) {
  (void) h;
  const bool had = castle_shim::nvs_strings().erase(key) + castle_shim::nvs_store().erase(key) > 0;
  return had ? ESP_OK : ESP_ERR_NVS_NOT_FOUND;
}

inline esp_err_t nvs_commit(nvs_handle_t h) {
  (void) h;
  return ESP_OK;
}

inline void nvs_close(nvs_handle_t h) { (void) h; }
