#pragma once
#include <freertos/FreeRTOS.h>

#include <string>
#include <vector>

namespace castle_shim {
/// How many ticks the firmware asked to sleep for, cumulative. Not compared
/// against anything — it exists so the delay is a call with an effect rather
/// than an empty macro, and so a handler that stopped yielding is visible.
inline unsigned long &yielded() {
  static unsigned long n = 0;
  return n;
}
}  // namespace castle_shim

/// A no-op that counts. Real time would turn a 2 MB OTA into a 2-second
/// test for no gain: the point of the delay is scheduling, and there is no
/// second task here to schedule.
inline void vTaskDelay(TickType_t ticks) { castle_shim::yielded() += ticks; }

/// xTaskCreatePinnedToCore, recorded and NOT run. The harness has one
/// thread; the upload worker's loop would never give it back, and the work
/// the task would have done is driven a job at a time by
/// castle_web::upload_pump() from tests/cxx/web_check.cpp instead. What is
/// kept is the fact that the firmware asked, and the priority and core it
/// asked for — tests/test_firmware_contract.py reads those out of the
/// source, and this keeps the CALL compiling and honest.
namespace castle_shim {
struct SpawnedTask {
  std::string name;
  unsigned priority = 0;
  int core = -1;
};
inline std::vector<SpawnedTask> &tasks() {
  static std::vector<SpawnedTask> v;
  return v;
}
}  // namespace castle_shim

inline BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name,
                                          unsigned stack, void *arg,
                                          UBaseType_t priority, void **handle,
                                          BaseType_t core) {
  (void) fn;
  (void) stack;
  (void) arg;
  if (handle != nullptr) *handle = nullptr;
  castle_shim::tasks().push_back({name, (unsigned) priority, (int) core});
  return pdPASS;
}
