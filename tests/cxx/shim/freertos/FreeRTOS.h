#pragma once
// FreeRTOS, minus the scheduler. The castle's web layer uses it for two
// things — vTaskDelay(1) between chunks, so the watched main loop gets a
// tick, and (since v5.61) a queue and a task for the upload worker. A host
// harness has no main loop to starve and no second thread to run a task on,
// so the delay counts and the queue is a plain FIFO the harness drains in
// line; see freertos/queue.h and tests/cxx/web_check.cpp.

typedef unsigned int TickType_t;

typedef int BaseType_t;
typedef unsigned int UBaseType_t;

#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define pdFAIL 0
#define portMAX_DELAY ((TickType_t) 0xffffffff)

/// The device's tick is 100 Hz (CONFIG_FREERTOS_HZ default on IDF 5.x is
/// 1000 for the S2 build; either way the harness converts and ignores).
#define configTICK_RATE_HZ 1000
#define portTICK_PERIOD_MS (1000 / configTICK_RATE_HZ)
#define pdMS_TO_TICKS(ms) ((TickType_t) ((ms) / portTICK_PERIOD_MS))
