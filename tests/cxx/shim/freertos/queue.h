#pragma once
// A FreeRTOS queue with no scheduler behind it: a plain FIFO of fixed-size
// items, byte for byte as xQueueSend copies them.
//
// The firmware uses exactly one (sd_web_upload.h, the A9 upload worker), and
// what the harness needs from it is the HAND-OFF, not the concurrency: that
// h_put copies the job in and returns, and that the worker later finds
// exactly that job. With one thread there is nobody to block for, so a
// receive on an empty queue returns pdFALSE however long it was asked to
// wait — tests/cxx/web_check.cpp drives upload_pump() itself, once per
// request, while the request's body is still alive.

#include <cstring>
#include <deque>
#include <string>
#include <vector>

#include <freertos/FreeRTOS.h>

namespace castle_shim {
struct Queue {
  size_t item_size = 0;
  size_t depth = 0;
  std::deque<std::string> items;
};
}  // namespace castle_shim

typedef castle_shim::Queue *QueueHandle_t;

inline QueueHandle_t xQueueCreate(UBaseType_t depth, UBaseType_t item_size) {
  auto *q = new castle_shim::Queue();
  q->item_size = item_size;
  q->depth = depth;
  return q;
}

inline void vQueueDelete(QueueHandle_t q) { delete q; }

inline BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait) {
  (void) wait;   // nothing else runs here, so waiting for room cannot help
  if (q == nullptr || q->items.size() >= q->depth) return pdFALSE;
  q->items.emplace_back(static_cast<const char *>(item), q->item_size);
  return pdTRUE;
}

inline BaseType_t xQueueReceive(QueueHandle_t q, void *out, TickType_t wait) {
  (void) wait;
  if (q == nullptr || q->items.empty()) return pdFALSE;
  memcpy(out, q->items.front().data(), q->item_size);
  q->items.pop_front();
  return pdTRUE;
}

inline UBaseType_t uxQueueMessagesWaiting(QueueHandle_t q) {
  return q == nullptr ? 0 : (UBaseType_t) q->items.size();
}
