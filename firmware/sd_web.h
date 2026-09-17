// The control half of the castle's web server (the serving half is
// sd_web_site.h, the firmware flasher sd_web_ota.h): manage the SD card from
// the Mac, drive the show from any browser, and read the device's own health.
//
// TWO KINDS OF WORK, TWO RULES:
//
//   Filesystem work (list/upload/delete) happens RIGHT HERE in the httpd
//   task. That is safe: ESP-IDF's FATFS layer takes a per-volume lock, so a
//   concurrent read on the main loop blocks briefly instead of corrupting.
//
//   ESPHome work (play, scenes, volume, light, PIR settings) must NOT happen
//   here. Scripts, entities, the media player — none of it is thread-safe
//   outside the main loop. Those requests only record a pending action, and
//   a 200 ms `interval:` in the YAML picks it up and executes it. The HTTP
//   reply means "queued", not "done"; the log says what happened.
//
//   The same interval mirrors state the OTHER way (volume, current scene,
//   current track) into atomics/guarded strings this file may read, so
//   /api/status answers without ever touching an ESPHome object.

#pragma once

#include <esp_http_server.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <ctime>
#include <algorithm>
#include <array>
#include <string_view>
#include <vector>

#include "sd_web_util.h"
#include "sd_web_state.h"
#include "sd_web_stream.h"
#include <atomic>
#include <mutex>
#include <string>
#include <cstdio>
#include <dirent.h>
#include <sys/stat.h>

#include "esphome/core/log.h"
#include "boot_log.h"
#include "castle_health.h"
#include "sd_audio.h"
#include "sd_space.h"

namespace castle_web {

static const char *const TAG = "castle_web";

inline httpd_handle_t g_server = nullptr;
// g_scene_ids, set_scene_ids() and scene_id_state() moved to sd_web_state.h
// in v5.60: the list is read by the httpd task and written by the boot
// lambda, so it belongs beside the mutex that now guards it (A5).

// ── /api/status ─────────────────────────────────────────────────────────
// Card capacity comes from sd_space.h, which only reads the card when a
// writer says it changed — h_status must stay cheap, it is polled.
inline esp_err_t h_status(httpd_req_t *req) {
  // ONE instant of everything the reply prints (sd_web_state.h): the track
  // name and the audio clock that describes it must not be read a lock
  // apart, or a poll on the tick a track ends carries one of them stale.
  const Status st = status_snapshot();
  unsigned sd_total = 0;
  unsigned sd_free = 0;
  sd_space_kb(sd_total, sd_free);
  // Numbers through snprintf, strings through json_escape into a
  // std::string: a fixed buffer truncated silently when the boot manifest
  // listed more than a few missing files, and every client's parse died.
  std::array<char, 288> buf{};
  snprintf(buf.data(), buf.size(),
           R"({"version":"%s","compiled":"%s %s","uptime_s":%lld,)"
           R"("sd_mounted":%s,"psram_free_kb":%u,"heap_free_kb":%u,)"
           R"("sd_total_kb":%u,"sd_free_kb":%u,"missing":")",
           ESPHOME_PROJECT_VERSION, __DATE__, __TIME__,
           (long long) (esp_timer_get_time() / 1000000),
           castle_sd::g_mounted ? "true" : "false",
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024),
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
           sd_total, sd_free);
  std::string out = buf.data();
  out += json_escape(st.missing);
  snprintf(buf.data(), buf.size(), R"(","volume":%d,"scene":")", st.volume);
  out += buf.data();
  out += json_escape(st.scene);
  out += R"(","track":")";
  out += json_escape(st.track);
  // B1: the ids this BUILD was compiled with (seeded at boot, same list
  // /api/scene checks). `missing` can only speak about these — the desk
  // diffs them against scenes.yaml to spot a stale board before a pick
  // answers "unknown scene".
  out += R"(","scenes":")";
  out += json_escape(st.scenes);
  // v5.52: `playing` is the pipeline's own word, `position_ms` the main
  // loop's clock since it came alive (sd_web_state.h). A browser that
  // follows the castle reads these instead of counting from its own click.
  // v5.59: the light frame counters. `light_applied` is what the main loop
  // ran, `light_evicted` what the one-slot mailbox dropped — a page
  // streaming colour faster than the 200 ms drain can see it, instead of
  // wondering why its frames look coarse. /api/events carries the rest.
  // L2 (v5.62): `epoch` is unix seconds, or 0 until SNTP has answered. The
  // ring's t_ms is uptime and always will be (it is written from an ISR-ish
  // hot path and a wall clock there would be a lie half the night); this is
  // the base a page needs to turn one into the other, and the only honest
  // way to ask "what time did the porch go dark".
  // L6: `rssi` in dBm, 0 when not associated — a castle at the end of the
  // garden answering slowly and a castle with a failing supply read the
  // same from the desk without it.
  // ::time, not time: main.cpp has `using namespace esphome;` and ESPHome
  // has a `time` COMPONENT namespace, which makes the bare name ambiguous.
  const time_t wall = ::time(nullptr);
  snprintf(buf.data(), buf.size(),
           R"(","show_on":%s,"playing":%s,"position_ms":%lld,)"
           R"("light_applied":%u,"light_evicted":%u,"epoch":%lld,"rssi":%d,)"
           R"("pir":{"armed":%s,"cooldown_s":%d,"scene":")",
           st.show_on ? "true" : "false",
           st.playing ? "true" : "false", st.position_ms,
           st.light_applied, st.light_evicted,
           (long long) (wall > 1577836800 ? wall : 0), st.rssi,
           st.pir_armed ? "true" : "false", st.pir_cooldown);
  out += buf.data();
  out += json_escape(st.pir_scene);
  out += R"("}})";
  // A2: this reply is the proof a web-OTA'd image needs — WiFi associated
  // and the web server came up. The main loop confirms the image on the
  // strength of it (castle_sd_common.yaml -> castle_sd::mark_firmware_healthy),
  // so an update delivered to a castle with no Home Assistant on the
  // network no longer rolls itself back on the next power cycle.
  g_status_served.store(true);
  return reply_json(req, out);
}

// ── /api/health — the season-long counters ──────────────────────────────
inline esp_err_t h_health(httpd_req_t *req) {
  std::array<char, 240> buf{};
  // A8 (v5.61): sd_read_errors — transfers off the card that FAILED and
  // were torn down rather than framed as a short success (sd_web_site.h).
  // This boot only, deliberately: the question it answers is "is the card
  // going bad right now", and the two NVS counters beside it already carry
  // the season. Zero is the normal reading; anything else is the one number
  // that explains a track that stops at 12% every time it plays.
  //
  // v5.62 (L4) adds `sd_last_error`: "<path>@<offset>" of the last transfer
  // that tore, "" when there has been none. A count alone could not tell
  // one bad file from a dying card, which is the only question worth
  // asking once the count is non-zero.
  //
  // v5.62 (L7) adds `heap_min_kb`: the LOW-WATER mark of internal heap,
  // which is the number that explains a crash. `heap_free_kb` in
  // /api/status is what is free NOW — after the allocation that failed has
  // been given back — so a castle that came within a hundred bytes of the
  // wall at 21:40 reads perfectly healthy at 23:00. docs/RUNBOOK.md points
  // the operator at heap for "audio starts then breaks up"; this is the
  // heap it should have meant.
  snprintf(buf.data(), buf.size(),
           R"({"boots":%u,"crashes":%u,"last_reset":"%s",)"
           R"("was_crash":%s,"sd_read_errors":%u,"heap_min_kb":%u,)"
           R"("sd_last_error":")",
           (unsigned) castle_health::g_boots, (unsigned) castle_health::g_crashes,
           castle_health::reason_str(),
           castle_health::was_crash() ? "true" : "false",
           castle_health::g_sd_read_errors.load(),
           (unsigned) (heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL) / 1024));
  std::string out = buf.data();
  out += json_escape(castle_health::sd_last_error());
  out += R"("})";
  return reply_json(req, out);
}

// ── /api/files — list the card root ─────────────────────────────────────
inline esp_err_t h_list(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  if (esp_err_t sent; !query_ok(req, {"d"}, sent)) return sent;
  // B2: ?d=<subdir> lists inside the card (scenes/, site/) — the desk could
  // never SEE the directory that holds the show. Validated like /sd/ paths.
  std::string sub = query_param(req, "d");
  std::array<char, 160> dirpath{};
  if (sub.empty()) {
    snprintf(dirpath.data(), dirpath.size(), "/sd");
  } else {
    if (!safe_subpath(sub)) return reply_err(req, "400 Bad Request", "bad path");
    snprintf(dirpath.data(), dirpath.size(), "/sd/%s", sub.c_str());
  }
  DIR *d = opendir(dirpath.data());
  if (d == nullptr) return reply_err(req, "404 Not Found", "no such directory");
  std::string out = "[";
  unsigned skipped = 0;
  const struct dirent *e = nullptr;
  while ((e = readdir(d)) != nullptr) {
    if (e->d_name[0] == '.') continue;
    // A name safe_name refuses is one the desk could never have uploaded
    // and /api/play could never be asked for — the Mac wrote it straight
    // onto the card. Counted, not listed: the desk should not offer a
    // track the castle will then refuse by name.
    if (!safe_name(e->d_name)) { skipped++; continue; }
    const std::string full = std::string(dirpath.data()) + "/" + e->d_name;
    struct stat st{};
    long size = -1;
    bool is_dir = false;
    if (stat(full.c_str(), &st) == 0) {
      size = (long) st.st_size;
      is_dir = S_ISDIR(st.st_mode);
    }
    std::array<char, 48> tail{};
    snprintf(tail.data(), tail.size(), R"(","size":%ld,"dir":%s})", size,
             is_dir ? "true" : "false");
    if (out.size() > 1) out += ",";
    out += R"({"name":")";
    out += json_escape(e->d_name);
    out += tail.data();
  }
  closedir(d);
  // One trailing {"skipped":N} element, only when N > 0. Every reader of
  // this array filters on name/dir, so an element with neither is invisible
  // to them — and visible to anyone wondering why a file is not listed.
  if (skipped > 0) {
    std::array<char, 40> t{};
    snprintf(t.data(), t.size(), R"(%s{"skipped":%u})", out.size() > 1 ? "," : "", skipped);
    out += t.data();
  }
  out += "]";
  return reply_json(req, out);
}

// ── show control: play/scene/stop/volume/light/pir — all queued ─────────
inline esp_err_t h_play(httpd_req_t *req) {
  if (esp_err_t sent; !query_ok(req, {"f"}, sent)) return sent;
  std::string f = query_param(req, "f");
  if (!safe_name(f)) return reply_err(req, "400 Bad Request", "need ?f=<file>");
  set_pending(ActionType::PLAY, f);
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_scene(httpd_req_t *req) {
  if (esp_err_t sent; !query_ok(req, {"s"}, sent)) return sent;
  std::string s = query_param(req, "s");
  if (s.empty()) return reply_err(req, "400 Bad Request", "need ?s=<scene>");
  // {"queued":true} for a scene that does not exist is a lie the desk then
  // toasts as success. The id list is seeded at boot from pir_scene's options.
  const int known = scene_id_state(s);
  if (known == 0)
    return reply_err(req, "503 Service Unavailable", "scene list not ready");
  if (known < 0) return reply_err(req, "404 Not Found", "unknown scene");
  set_pending(ActionType::SCENE, s);
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_stop(httpd_req_t *req) {
  set_pending(ActionType::STOP, "");
  return reply_json(req, "{\"queued\":true}");
}

// Show-night handlers (playlist start/stop, blackout) live in
// sd_web_remote.h with the page that presses them.

inline esp_err_t h_volume(httpd_req_t *req) {
  if (esp_err_t sent; !query_ok(req, {"v"}, sent)) return sent;
  std::string v = query_param(req, "v");
  // Digits only. atoi("abc") is 0, which turned a malformed request into a
  // silent mute — the kind of "worked, but wrong" a fuzz pass exists to find.
  const bool digits = !v.empty() && v.size() <= 3 &&
      v.find_first_not_of("0123456789") == std::string::npos;
  int pct = digits ? atoi(v.c_str()) : -1;
  if (pct < 0 || pct > 100) return reply_err(req, "400 Bad Request", "need ?v=0..100");
  set_pending(ActionType::VOLUME, std::to_string(pct));
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_light(httpd_req_t *req) {
  if (esp_err_t sent; !query_ok(req, {"c"}, sent)) return sent;
  std::string c = query_param(req, "c");
  if (!light_spec_ok(c))    // RRGGBB|show|off, optionally "<zone>:" first
    return reply_err(req, "400 Bad Request", "need ?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]");
  set_pending(ActionType::LIGHT, c);
  return reply_json(req, "{\"queued\":true}");
}

/// POST /api/pir?armed=0|1|true|false|on|off&cooldown=30|60|120&scene=<id>
/// — any subset of the three. Encoded "a|c|scene"; empty field = leave alone.
inline esp_err_t h_pir(httpd_req_t *req) {
  if (esp_err_t sent; !query_ok(req, {"armed", "cooldown", "scene"}, sent)) return sent;
  std::string a = query_param(req, "armed");
  std::string c = query_param(req, "cooldown");
  std::string s = query_param(req, "scene");
  if (a.empty() && c.empty() && s.empty())
    return reply_err(req, "400 Bad Request", "need armed=, cooldown= or scene=");
  if (!pir_armed_ok(a)) return reply_err(req, "400 Bad Request", "bad armed");
  if (!pir_cooldown_ok(c)) return reply_err(req, "400 Bad Request", "bad cooldown");
  // A4/C5/C7: the three fields ride to the main loop packed with '|', and
  // the YAML decoder unpacks them with find/rfind while the emulator uses
  // split — so a '|' inside a value is decoded differently by each. It is
  // refused here instead, at the one door it can come through.
  if (a.find('|') != std::string::npos || c.find('|') != std::string::npos ||
      s.find('|') != std::string::npos)
    return reply_err(req, "400 Bad Request", "bad separator");
  // And the scene is checked against the SAME list /api/scene checks: this
  // route used to pass any string straight through to pir_scene's select,
  // where an unknown option is a log line nobody reads.
  if (!s.empty()) {
    const int known = scene_id_state(s);
    if (known == 0)
      return reply_err(req, "503 Service Unavailable", "scene list not ready");
    if (known < 0) return reply_err(req, "404 Not Found", "unknown scene");
  }
  set_pending(ActionType::PIRCFG, a + "|" + c + "|" + s);
  return reply_json(req, "{\"queued\":true}");
}

}  // namespace castle_web

#include "sd_web_events.h"
#include "sd_web_ota.h"
#include "sd_web_upload.h"
#include "sd_web_site.h"
#include "sd_web_remote.h"

namespace castle_web {

// ── startup ─────────────────────────────────────────────────────────────
inline void start() {
  if (g_server != nullptr) return;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  // Its share of the 16-socket pool: desk polls, one upload, the phone
  // remote. The stream server keeps 2 (sd_web_stream.h), the API and the
  // player's loopback fetch take the rest.
  cfg.max_open_sockets = 4;
  cfg.uri_match_fn = httpd_uri_match_wildcard;
  // MUST exceed the reg() count below (25 today). At 20, the LAST THREE
  // registrations failed silently on the device — the /sd/ wildcard (the very
  // URL the media pipeline streams scene audio through), /site/ and / — so the
  // cue desk 404'd and SD streaming was dead while every /api route worked.
  // Found on the live board 2026-08-15; headroom so the next route is free.
  cfg.max_uri_handlers = 32;
  cfg.stack_size = 6144;   // default 4 KB is too tight for FATFS + our buffers
  // The control plane, off the main loop's back. A scene start is an SD read
  // and an MP3 decode on ESPHome's loop task, and at HTTPD_DEFAULT_CONFIG
  // (task_priority 5, tskNO_AFFINITY) a status poll landed behind all of it:
  // /api/status went unanswered for 1.8-3 s on the board and the desk read
  // the castle as hung. Priority 6 is one above the ESP32 loop task ESPHome
  // creates at priority 1 (esphome/components/esp32/core.cpp) with headroom
  // to spare, and far below the audio and WiFi/lwIP tasks, which must never
  // wait on a browser. The same file pins that loop task to core 1, so the
  // httpd is pinned to the OTHER core rather than competing for its time
  // slices. The stream server (sd_web_stream.h) is untouched.
  cfg.task_priority = 6;
  cfg.core_id = 0;
  cfg.lru_purge_enable = true;

  esp_err_t err = httpd_start(&g_server, &cfg);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "httpd failed to start: %s", esp_err_to_name(err));
    return;
  }
  auto reg = [](const char *uri, httpd_method_t m, esp_err_t (*fn)(httpd_req_t *)) {
    httpd_uri_t u{};
    u.uri = uri;
    u.method = m;
    u.handler = fn;
    httpd_register_uri_handler(g_server, &u);
  };
  reg("/api/status", HTTP_GET, h_status);
  reg("/api/health", HTTP_GET, h_health);
  reg("/api/events", HTTP_GET, h_events);
  reg("/api/files", HTTP_GET, h_list);
  reg("/api/files/*", HTTP_PUT, h_put);
  reg("/api/site/*", HTTP_PUT, h_put);
  reg("/api/scenes/*", HTTP_PUT, h_put);
  reg("/api/files/*", HTTP_DELETE, h_delete);
  reg("/api/site/*", HTTP_DELETE, h_delete);
  reg("/api/scenes/*", HTTP_DELETE, h_delete);
  reg("/api/play", HTTP_POST, h_play);
  reg("/api/scene", HTTP_POST, h_scene);
  reg("/api/stop", HTTP_POST, h_stop);
  reg("/api/show/start", HTTP_POST, h_show_start);
  reg("/api/show/stop", HTTP_POST, h_show_stop);
  reg("/api/blackout", HTTP_POST, h_blackout);
  reg("/api/blackout", HTTP_GET, h_blackout);   // bookmarkable
  reg("/remote", HTTP_GET, h_remote);
  reg("/api/volume", HTTP_POST, h_volume);
  reg("/api/light", HTTP_POST, h_light);
  reg("/api/pir", HTTP_POST, h_pir);
  reg("/api/ota", HTTP_PUT, h_ota);
  reg("/api/bootlog", HTTP_GET, h_bootlog);
  reg("/sd/*", HTTP_GET, h_sd_get);
  // Playback must never queue behind the control plane — the decoder pulls
  // its audio through this second server. See sd_web_stream.h.
  castle_stream::start(h_sd_get);
  // A9: the task uploads are handed to, so a publish cannot hold this
  // server's one task for the length of a 2 MB track (sd_web_upload.h).
  upload_start();
  reg("/site/*", HTTP_GET, h_site);
  reg("/", HTTP_GET, h_root);
  ESP_LOGI(TAG, "web server up on port %d", cfg.server_port);
}

}  // namespace castle_web
