// The control half of the castle's web server (the serving half is
// sd_web_site.h): manage the SD card from the Mac, drive the show from any
// browser, flash new firmware, and read the device's own health.
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
#include <esp_ota_ops.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <atomic>
#include <mutex>
#include <string>
#include <cstdio>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esphome/core/log.h"
#include "boot_log.h"
#include "castle_health.h"
#include "sd_audio.h"

namespace castle_web {

static const char *const TAG = "castle_web";

inline httpd_handle_t g_server = nullptr;

// ── pending action, handed from httpd task to the main loop ─────────────
enum ActionType {
  NONE = 0, PLAY = 1, SCENE = 2, STOP = 3, VOLUME = 4, LIGHT = 5,
  PIRCFG = 6, RESTART = 7, SHOW = 8,   // arg "1" starts the playlist, "0" stops
  BLACKOUT = 9,                        // #25: everything off, NOW
};
struct Action {
  int type{NONE};
  std::string arg;
};
inline std::mutex g_mu;
inline Action g_pending{};

inline void set_pending(int type, std::string arg) {
  std::lock_guard<std::mutex> lk(g_mu);
  g_pending = {type, std::move(arg)};
}
/// Called by the YAML interval on the main loop. Returns NONE most of the time.
inline Action take_pending() {
  std::lock_guard<std::mutex> lk(g_mu);
  Action a = g_pending;
  g_pending = {NONE, ""};
  return a;
}

// ── state mirrored FROM the main loop, readable by handlers ─────────────
inline std::atomic<int> g_volume{70};
inline std::atomic<bool> g_pir_armed{true};
inline std::atomic<int> g_pir_cooldown{60};
inline std::atomic<bool> g_show_on{false};   // is the playlist running
inline std::mutex g_state_mu;
inline std::string g_scene;        // current scene id, "" until one runs
inline std::string g_track;        // current audio track, "" when idle
inline std::string g_pir_scene;    // what motion triggers
// #29: scene audio files the boot manifest check could not find on the
// card, comma-separated; empty = all present (the overwhelmingly normal
// case). Set once at boot by the generated manifest_check script.
inline std::string g_missing;

inline void set_missing(const std::string &csv) {
  std::lock_guard<std::mutex> lk(g_state_mu);
  g_missing = csv;
}

inline void mirror_show_state(const std::string &scene, const std::string &track,
                              const std::string &pir_scene) {
  std::lock_guard<std::mutex> lk(g_state_mu);
  g_scene = scene;
  g_track = track;
  g_pir_scene = pir_scene;
}

// ── helpers ─────────────────────────────────────────────────────────────

/// %20 and friends. Uploaded names arrive URL-encoded in the path.
inline std::string url_decode(const char *s) {
  std::string out;
  for (const char *p = s; *p; p++) {
    if (*p == '%' && p[1] && p[2]) {
      char hex[3] = {p[1], p[2], 0};
      out.push_back((char) strtol(hex, nullptr, 16));
      p += 2;
    } else if (*p == '+') {
      out.push_back(' ');
    } else {
      out.push_back(*p);
    }
  }
  return out;
}

/// A filename from a URL is untrusted input even on a porch prop. One path
/// component only: no slashes, no "..", nothing hidden.
inline bool safe_name(const std::string &n) {
  return !n.empty() && n.size() < 100 && n[0] != '.' &&
         n.find('/') == std::string::npos && n.find("..") == std::string::npos;
}

/// The filename after a fixed prefix like "/api/files/".
inline std::string name_from_uri(httpd_req_t *req, const char *prefix) {
  const char *p = req->uri + strlen(prefix);
  std::string n = url_decode(p);
  auto q = n.find('?');
  if (q != std::string::npos) n.resize(q);
  return n;
}

inline esp_err_t reply_json(httpd_req_t *req, const std::string &body) {
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, body.c_str(), body.size());
}

inline esp_err_t reply_err(httpd_req_t *req, const char *status, const char *msg) {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "text/plain");
  return httpd_resp_send(req, msg, HTTPD_RESP_USE_STRLEN);
}

inline std::string query_param(httpd_req_t *req, const char *key) {
  char q[200] = {0};
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return "";
  char val[120] = {0};
  if (httpd_query_key_value(q, key, val, sizeof(val)) != ESP_OK) return "";
  return url_decode(val);
}

// ── /api/status ─────────────────────────────────────────────────────────
inline esp_err_t h_status(httpd_req_t *req) {
  std::string scene, track, pir_scene, missing;
  {
    std::lock_guard<std::mutex> lk(g_state_mu);
    scene = g_scene; track = g_track; pir_scene = g_pir_scene;
    missing = g_missing;
  }
  char buf[680];
  snprintf(buf, sizeof(buf),
           "{\"version\":\"%s\",\"compiled\":\"%s %s\",\"uptime_s\":%lld,"
           "\"sd_mounted\":%s,\"psram_free_kb\":%u,\"heap_free_kb\":%u,"
           "\"missing\":\"%s\","
           "\"volume\":%d,\"scene\":\"%s\",\"track\":\"%s\",\"show_on\":%s,"
           "\"pir\":{\"armed\":%s,\"cooldown_s\":%d,\"scene\":\"%s\"}}",
           CASTLE_VERSION, __DATE__, __TIME__,
           (long long) (esp_timer_get_time() / 1000000),
           castle_sd::g_mounted ? "true" : "false",
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024),
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
           missing.c_str(),
           g_volume.load(), scene.c_str(), track.c_str(),
           g_show_on.load() ? "true" : "false",
           g_pir_armed.load() ? "true" : "false", g_pir_cooldown.load(),
           pir_scene.c_str());
  return reply_json(req, buf);
}

// ── /api/health — the season-long counters ──────────────────────────────
inline esp_err_t h_health(httpd_req_t *req) {
  char buf[200];
  snprintf(buf, sizeof(buf),
           "{\"boots\":%u,\"crashes\":%u,\"last_reset\":\"%s\","
           "\"was_crash\":%s}",
           (unsigned) castle_health::g_boots, (unsigned) castle_health::g_crashes,
           castle_health::reason_str(),
           castle_health::was_crash() ? "true" : "false");
  return reply_json(req, buf);
}

// ── /api/files — list the card root ─────────────────────────────────────
inline esp_err_t h_list(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  DIR *d = opendir("/sd");
  if (d == nullptr) return reply_err(req, "500 Internal Server Error", "opendir failed");
  std::string out = "[";
  struct dirent *e;
  while ((e = readdir(d)) != nullptr) {
    if (e->d_name[0] == '.') continue;
    char full[300];
    snprintf(full, sizeof(full), "/sd/%s", e->d_name);
    struct stat st{};
    long size = (stat(full, &st) == 0) ? (long) st.st_size : -1;
    char item[200];
    snprintf(item, sizeof(item), "%s{\"name\":\"%s\",\"size\":%ld,\"dir\":%s}",
             out.size() > 1 ? "," : "", e->d_name, size,
             (e->d_type == DT_DIR) ? "true" : "false");
    out += item;
  }
  closedir(d);
  out += "]";
  return reply_json(req, out);
}

// ── uploads: PUT /api/files/<name>, /api/site/<name>, /api/scenes/<name> ─
inline esp_err_t write_body(httpd_req_t *req, const char *path) {
  FILE *f = fopen(path, "wb");
  if (f == nullptr) return reply_err(req, "500 Internal Server Error", "cannot create file");
  static constexpr size_t CHUNK = 8192;
  char *buf = (char *) malloc(CHUNK);
  if (buf == nullptr) {
    fclose(f);
    return reply_err(req, "500 Internal Server Error", "no memory");
  }
  size_t remaining = req->content_len, written = 0;
  bool ok = true;
  while (remaining > 0) {
    int got = httpd_req_recv(req, buf, remaining < CHUNK ? remaining : CHUNK);
    if (got <= 0) { ok = false; break; }
    if (fwrite(buf, 1, got, f) != (size_t) got) { ok = false; break; }
    remaining -= got;
    written += got;
    // The third appearance of this bug class (h_ota and send_sd_file were
    // the first two): a megabyte of back-to-back recv+SD-write on the httpd
    // task starves the watched main loop, and the watchdog resets the castle
    // mid-upload. One tick per chunk is the whole cure.
    vTaskDelay(1);
  }
  free(buf);
  fclose(f);
  if (!ok) {
    unlink(path);  // a half-written MP3 is worse than a missing one
    ESP_LOGE(TAG, "upload of %s failed at %u bytes", path, (unsigned) written);
    return reply_err(req, "500 Internal Server Error", "short write");
  }
  ESP_LOGI(TAG, "uploaded %s (%u KB)", path, (unsigned) (written / 1024));
  char body[220];
  snprintf(body, sizeof(body), "{\"path\":\"%s\",\"bytes\":%u}", path,
           (unsigned) written);
  return reply_json(req, body);
}

/// PUT into /sd, /sd/site or /sd/scenes depending on the route. The scenes
/// directory is where the show's own tracks live (see audio_sd.yaml).
inline esp_err_t h_put(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  const char *dir = "";
  const char *prefix = "/api/files/";
  if (strncmp(req->uri, "/api/site/", 10) == 0) { dir = "site/"; prefix = "/api/site/"; }
  if (strncmp(req->uri, "/api/scenes/", 12) == 0) { dir = "scenes/"; prefix = "/api/scenes/"; }
  std::string name = name_from_uri(req, prefix);
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad filename");
  if (dir[0] != '\0') {
    char d[32];
    snprintf(d, sizeof(d), "/sd/%s", dir);
    d[strlen(d) - 1] = '\0';   // mkdir without the trailing slash
    mkdir(d, 0775);
  }
  char path[200];
  snprintf(path, sizeof(path), "/sd/%s%s", dir, name.c_str());
  return write_body(req, path);
}

inline esp_err_t h_delete(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  std::string name = name_from_uri(req, "/api/files/");
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad filename");
  char path[160];
  snprintf(path, sizeof(path), "/sd/%s", name.c_str());
  if (unlink(path) != 0) return reply_err(req, "404 Not Found", "no such file");
  ESP_LOGI(TAG, "deleted %s", path);
  return reply_json(req, "{\"deleted\":true}");
}

// ── show control: play/scene/stop/volume/light/pir — all queued ─────────
inline esp_err_t h_play(httpd_req_t *req) {
  std::string f = query_param(req, "f");
  if (!safe_name(f)) return reply_err(req, "400 Bad Request", "need ?f=<file>");
  set_pending(PLAY, f);
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_scene(httpd_req_t *req) {
  std::string s = query_param(req, "s");
  if (s.empty()) return reply_err(req, "400 Bad Request", "need ?s=<scene>");
  set_pending(SCENE, s);
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_stop(httpd_req_t *req) {
  set_pending(STOP, "");
  return reply_json(req, "{\"queued\":true}");
}

// Show-night handlers (playlist start/stop, blackout) live in
// sd_web_remote.h with the page that presses them.

inline esp_err_t h_volume(httpd_req_t *req) {
  std::string v = query_param(req, "v");
  int pct = v.empty() ? -1 : atoi(v.c_str());
  if (pct < 0 || pct > 100) return reply_err(req, "400 Bad Request", "need ?v=0..100");
  set_pending(VOLUME, std::to_string(pct));
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_light(httpd_req_t *req) {
  std::string c = query_param(req, "c");
  bool hex6 = c.size() == 6 && c.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos;
  if (!hex6 && c != "show" && c != "off")
    return reply_err(req, "400 Bad Request", "need ?c=RRGGBB, show, or off");
  set_pending(LIGHT, c);
  return reply_json(req, "{\"queued\":true}");
}

/// POST /api/pir?armed=0|1&cooldown=<s>&scene=<id> — any subset of the three.
/// Encoded "a|c|scene" for the main loop; empty field = leave alone.
inline esp_err_t h_pir(httpd_req_t *req) {
  std::string a = query_param(req, "armed");
  std::string c = query_param(req, "cooldown");
  std::string s = query_param(req, "scene");
  if (a.empty() && c.empty() && s.empty())
    return reply_err(req, "400 Bad Request", "need armed=, cooldown= or scene=");
  set_pending(PIRCFG, a + "|" + c + "|" + s);
  return reply_json(req, "{\"queued\":true}");
}

// ── PUT /api/ota — new firmware over plain HTTP ─────────────────────────
// The web-only update path: no esphome CLI, no Mac — any browser or curl can
// deliver a .bin. Written straight into the inactive OTA slot; the reboot is
// queued through the main loop so the HTTP reply gets out first. The
// bootloader's rollback net still applies: an image that never confirms
// itself (API connect) is reverted on the next reboot.
inline esp_err_t h_ota(httpd_req_t *req) {
  const esp_partition_t *part = esp_ota_get_next_update_partition(nullptr);
  if (part == nullptr) return reply_err(req, "500 Internal Server Error", "no OTA slot");
  if (req->content_len < 65536 || req->content_len > part->size)
    return reply_err(req, "400 Bad Request", "implausible image size");

  // Nothing else may touch the SPI bus or burn CPU while flash is being
  // written — the v5.12 upload watchdogged twice, with audio already
  // stopped, because the eInk panel picked that moment to refresh.
  castle_sd::g_quiesce = true;

  esp_ota_handle_t ota;
  // SEQUENTIAL_WRITES, not the image size: passing a size makes ota_begin
  // erase the whole 1.75 MB partition in one synchronous burn — several
  // seconds with the flash cache suspended, which starves the idle task and
  // trips the task watchdog. (boots:4 crashes:1 last_reset:"task-watchdog"
  // — the health endpoint's first real case was this very handler.)
  // Sequential mode erases each block just before writing it: same result,
  // watchdog-sized pauses.
  if (esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &ota) != ESP_OK) {
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "ota begin failed");
  }

  static constexpr size_t CHUNK = 8192;
  char *buf = (char *) malloc(CHUNK);
  size_t remaining = req->content_len;
  bool first = true, ok = buf != nullptr;
  while (ok && remaining > 0) {
    int got = httpd_req_recv(req, buf, remaining < CHUNK ? remaining : CHUNK);
    if (got <= 0) { ok = false; break; }
    if (first) {
      first = false;
      if ((uint8_t) buf[0] != 0xE9) { ok = false; break; }   // app image magic
    }
    if (esp_ota_write(ota, buf, got) != ESP_OK) { ok = false; break; }
    remaining -= got;
    // Breathe. Every flash write suspends the cache, blocking every task
    // that executes from flash — including the watched main loop. Sequential
    // erase alone did not fix the watchdog (boots:6 crashes:2 says so);
    // back-to-back writes starve it just as well. One tick per chunk lets
    // the loop run and feed its own watchdog. ~140 chunks × 10 ms adds a
    // polite 1.5 s to the flash; the alternative is a reboot at 60%.
    vTaskDelay(1);
  }
  free(buf);
  if (!ok || esp_ota_end(ota) != ESP_OK) {
    ESP_LOGE(TAG, "web OTA failed with %u bytes left", (unsigned) remaining);
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "ota write failed");
  }
  if (esp_ota_set_boot_partition(part) != ESP_OK) {
    castle_sd::g_quiesce = false;
    return reply_err(req, "500 Internal Server Error", "could not select slot");
  }
  ESP_LOGI(TAG, "web OTA complete (%u bytes) — rebooting", (unsigned) req->content_len);
  // Reply BEFORE queueing the restart, and give lwip a beat to flush the
  // segment — the first live test flashed perfectly but rebooted with the
  // response still in the TCP buffer, so the client saw only a timeout.
  esp_err_t r = reply_json(req, "{\"flashed\":true,\"rebooting\":true}");
  vTaskDelay(pdMS_TO_TICKS(250));
  set_pending(RESTART, "");
  return r;
}

// ── /api/bootlog — the ring buffer, as text ─────────────────────────────
inline esp_err_t h_bootlog(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/plain");
  if (castle_log::g_buf == nullptr) {
    const char *msg = castle_log::g_init_called ? "boot log: init ran but no memory\n"
                                                : "boot log: init never ran\n";
    return httpd_resp_send(req, msg, HTTPD_RESP_USE_STRLEN);
  }
  const size_t held = castle_log::g_head < castle_log::LINES ? castle_log::g_head
                                                             : castle_log::LINES;
  const size_t first = castle_log::g_head < castle_log::LINES
                           ? 0 : castle_log::g_head - castle_log::LINES;
  char hdr[80];
  snprintf(hdr, sizeof(hdr), "boot log: %u lines, %u dropped\n", (unsigned) held,
           (unsigned) castle_log::g_dropped);
  httpd_resp_send_chunk(req, hdr, HTTPD_RESP_USE_STRLEN);
  for (size_t i = 0; i < held; i++) {
    const char *line =
        castle_log::g_buf + ((first + i) % castle_log::LINES) * castle_log::WIDTH;
    httpd_resp_send_chunk(req, line, HTTPD_RESP_USE_STRLEN);
    httpd_resp_send_chunk(req, "\n", 1);
  }
  return httpd_resp_send_chunk(req, nullptr, 0);
}

}  // namespace castle_web

#include "sd_web_site.h"
#include "sd_web_remote.h"

namespace castle_web {

// ── startup ─────────────────────────────────────────────────────────────
inline void start() {
  if (g_server != nullptr) return;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.uri_match_fn = httpd_uri_match_wildcard;
  cfg.max_uri_handlers = 20;
  cfg.stack_size = 6144;   // default 4 KB is too tight for FATFS + our buffers
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
  reg("/api/files", HTTP_GET, h_list);
  reg("/api/files/*", HTTP_PUT, h_put);
  reg("/api/site/*", HTTP_PUT, h_put);
  reg("/api/scenes/*", HTTP_PUT, h_put);
  reg("/api/files/*", HTTP_DELETE, h_delete);
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
  reg("/site/*", HTTP_GET, h_site);
  reg("/", HTTP_GET, h_root);
  ESP_LOGI(TAG, "web server up on port %d", cfg.server_port);
}

}  // namespace castle_web
