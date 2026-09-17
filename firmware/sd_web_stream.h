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

#include "sd_audio.h"      // castle_sd::g_quiesce
#include "sd_web_util.h"   // reply_err

namespace castle_stream {

inline httpd_handle_t g_stream = nullptr;

//: The real /sd/ handler (castle_web::h_sd_get), behind the gate below.
inline esp_err_t (*g_sd_get)(httpd_req_t *) = nullptr;

/// A1 (v5.61): the quiesce gate this server used to be exempt from.
///
/// castle_sd::g_quiesce means "flash is being written" (sd_web_ota.h). Every
/// flash write suspends the cache, so anything that runs beside the burning
/// task executes from a cache that is not there and eats the breathing ticks
/// h_ota inserts to keep the watchdog fed — which is why the eInk panel's
/// task had to learn this flag in v5.44 and why sd_audio.h's comment says
/// "anything that runs beside the main loop again must honour it". This
/// server was the one thing that never did: a browser (or a scene that
/// started mid-update) could pull a 2 MB track off the SPI card, at
/// priority 5, through the whole of an OTA.
///
/// A REFUSAL rather than a wait, and a fast one: an upload takes tens of
/// seconds, so parking the decoder for it would buffer-underrun anyway, and
/// a castle that is 90 seconds from rebooting into new firmware has nothing
/// to say with the old show's audio. The client hears the track fail to
/// start — which is the truth — instead of the reply never coming.
inline esp_err_t gate(httpd_req_t *req) {
  if (castle_sd::g_quiesce)
    return castle_web::reply_err(req, "503 Service Unavailable",
                                 "updating — card reads are paused");
  return g_sd_get(req);
}

inline void start(esp_err_t (*sd_get)(httpd_req_t *)) {
  if (g_stream != nullptr) return;
  g_sd_get = sd_get;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 8080;
  // Its own ctrl_port, or the two instances fight over the default UDP
  // control socket and the second one never starts.
  cfg.ctrl_port = 32769;
  cfg.uri_match_fn = httpd_uri_match_wildcard;
  cfg.stack_size = 6144;
  cfg.lru_purge_enable = true;
  // A1 (v5.61): OFF the audio-critical band. HTTPD_DEFAULT_CONFIG leaves
  // task_priority at 5, which is where ESP-IDF's I2S and decode tasks live
  // — so this server, which exists to FEED the decoder, was scheduled as
  // its equal and round-robined against it. On a card read that takes
  // longer than one tick (the SPI bus is shared and 20 MHz) the decoder
  // waited behind the very task fetching its bytes, and the porch heard
  // it as a stutter at the top of a track. 4 is below everything audio,
  // above the ESPHome loop task at 1 (esphome/components/esp32/core.cpp)
  // — this must still outrun the main loop or a busy show starves the
  // song — and nowhere near WiFi/lwIP. The control plane's own choice and
  // the reasoning behind it are in sd_web.h's start().
  cfg.task_priority = 4;
  // Core 0, with the control plane, because ESPHome pins its loop task —
  // and the audio work the loop drives — to core 1. Serving bytes must not
  // compete for the core that decodes them.
  cfg.core_id = 0;
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
    u.handler = gate;
    httpd_register_uri_handler(g_stream, &u);
  } else {
    ESP_LOGE("castle_stream", "stream server failed to start — playback will wedge the API");
  }
}

}  // namespace castle_stream
