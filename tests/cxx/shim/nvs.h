#pragma once
// NVS, in memory. castle_health::init() keeps the season's boot and crash
// counters here; nothing in the web layer writes it, and the harness sets
// the counters directly, so this only has to exist and behave.

#include <cstdint>
#include <map>
#include <string>

#include <esp_err.h>

typedef uint32_t nvs_handle_t;

typedef enum {
  NVS_READONLY = 0,
  NVS_READWRITE = 1,
} nvs_open_mode_t;

namespace castle_shim {
inline std::map<std::string, uint32_t> &nvs_store() {
  static std::map<std::string, uint32_t> m;
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
  if (it == castle_shim::nvs_store().end()) return ESP_ERR_NOT_FOUND;
  *out = it->second;
  return ESP_OK;
}

inline esp_err_t nvs_set_u32(nvs_handle_t h, const char *key, uint32_t v) {
  (void) h;
  castle_shim::nvs_store()[key] = v;
  return ESP_OK;
}

inline esp_err_t nvs_commit(nvs_handle_t h) {
  (void) h;
  return ESP_OK;
}

inline void nvs_close(nvs_handle_t h) { (void) h; }
