#pragma once
#include <freertos/FreeRTOS.h>

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
