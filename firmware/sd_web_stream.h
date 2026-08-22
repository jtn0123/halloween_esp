// The stream server: port 8080, /sd/* only, one job.
//
// esp_http_server answers requests one at a time from a single task, and SD
// playback IS an HTTP request to ourselves — the decoder streams
// http://127.0.0.1:8080/sd/... for the whole length of the song. On one
// server that request parked every /api call behind a three-minute download:
// the desk read it as "castle not reachable" the moment any track played
// (bench, 2026-08-19). Two servers, two tasks: port 80 stays a control plane
// that answers in milliseconds, this one is allowed to spend its life inside
// one transfer.
#pragma once

#include <esp_http_server.h>
#include <esp_log.h>

namespace castle_stream {

inline httpd_handle_t g_stream = nullptr;

inline void start(esp_err_t (*sd_get)(httpd_req_t *)) {
  if (g_stream != nullptr) return;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 8080;
  // Its own ctrl_port, or the two instances fight over the default UDP
  // control socket and the second one never starts.
  cfg.ctrl_port = 32769;
  cfg.uri_match_fn = httpd_uri_match_wildcard;
  cfg.stack_size = 6144;
  cfg.lru_purge_enable = true;
  // Socket budget (LWIP_MAX_SOCKETS is 16, castle.yaml): the default 7 per
  // server, twice, plus the player's own loopback fetch and the API blew the
  // pool — accept() failed with ENFILE, the reader saw 'connection reset'
  // and port 80 refused everything for the length of the song (v5.26 on
  // the bench). This server only ever has the player on it.
  cfg.max_open_sockets = 2;
  if (httpd_start(&g_stream, &cfg) == ESP_OK) {
    httpd_uri_t u{};
    u.uri = "/sd/*";
    u.method = HTTP_GET;
    u.handler = sd_get;
    httpd_register_uri_handler(g_stream, &u);
  } else {
    ESP_LOGE("castle_stream", "stream server failed to start — playback will wedge the API");
  }
}

}  // namespace castle_stream
