#pragma once
// esp_reset_reason: what castle_health turns into "last_reset" and
// "was_crash". The harness sets it from the environment (CASTLE_RESET) so
// the crash-counting branches can be driven without crashing anything.

#include <cstdlib>
#include <cstring>

typedef enum {
  ESP_RST_UNKNOWN = 0,
  ESP_RST_POWERON = 1,
  ESP_RST_EXT = 2,
  ESP_RST_SW = 3,
  ESP_RST_PANIC = 4,
  ESP_RST_INT_WDT = 5,
  ESP_RST_TASK_WDT = 6,
  ESP_RST_WDT = 7,
  ESP_RST_DEEPSLEEP = 8,
  ESP_RST_BROWNOUT = 9,
  ESP_RST_SDIO = 10,
} esp_reset_reason_t;

inline esp_reset_reason_t esp_reset_reason() {
  const char *v = getenv("CASTLE_RESET");
  if (v == nullptr || *v == '\0') return ESP_RST_POWERON;
  return (esp_reset_reason_t) atoi(v);
}

inline void esp_restart() { abort(); }
