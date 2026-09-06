#pragma once
// esp_err_t and the handful of codes the castle's headers name.

#include <cstdint>

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_FAIL (-1)

#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NOT_FOUND 0x105

/// esp_http_server's own base (ESP_ERR_HTTPD_BASE = 0xb000).
#define ESP_ERR_HTTPD_BASE 0xb000
#define ESP_ERR_HTTPD_HANDLERS_FULL (ESP_ERR_HTTPD_BASE + 1)
#define ESP_ERR_HTTPD_HANDLER_EXISTS (ESP_ERR_HTTPD_BASE + 2)
#define ESP_ERR_HTTPD_INVALID_REQ (ESP_ERR_HTTPD_BASE + 3)
#define ESP_ERR_HTTPD_RESULT_TRUNC (ESP_ERR_HTTPD_BASE + 4)
#define ESP_ERR_HTTPD_RESP_HDR (ESP_ERR_HTTPD_BASE + 5)
#define ESP_ERR_HTTPD_RESP_SEND (ESP_ERR_HTTPD_BASE + 6)
#define ESP_ERR_HTTPD_ALLOC_MEM (ESP_ERR_HTTPD_BASE + 7)

inline const char *esp_err_to_name(esp_err_t code) {
  switch (code) {
    case ESP_OK: return "ESP_OK";
    case ESP_FAIL: return "ESP_FAIL";
    case ESP_ERR_NO_MEM: return "ESP_ERR_NO_MEM";
    case ESP_ERR_INVALID_ARG: return "ESP_ERR_INVALID_ARG";
    case ESP_ERR_INVALID_STATE: return "ESP_ERR_INVALID_STATE";
    case ESP_ERR_NOT_FOUND: return "ESP_ERR_NOT_FOUND";
    default: return "ESP_ERR_UNKNOWN";
  }
}
