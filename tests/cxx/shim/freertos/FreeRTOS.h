#pragma once
// FreeRTOS, minus the scheduler. The castle's web layer uses it for exactly
// one thing — vTaskDelay(1) between chunks, so the watched main loop gets a
// tick — and a host harness has no main loop to starve.

typedef unsigned int TickType_t;

/// The device's tick is 100 Hz (CONFIG_FREERTOS_HZ default on IDF 5.x is
/// 1000 for the S2 build; either way the harness converts and ignores).
#define configTICK_RATE_HZ 1000
#define portTICK_PERIOD_MS (1000 / configTICK_RATE_HZ)
#define pdMS_TO_TICKS(ms) ((TickType_t) ((ms) / portTICK_PERIOD_MS))
