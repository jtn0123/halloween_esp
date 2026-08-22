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
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <algorithm>
#include <vector>

#include "sd_web_state.h"
#include "sd_web_stream.h"
#include <atomic>
#include <mutex>
#include <string>
#include <cerrno>
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
// Scene ids the firmware actually has, seeded at boot from the pir_scene
// select (castle_sd.yaml). Empty means "not seeded yet" — then /api/scene
// falls back to accepting anything, which only lasts the first seconds.
inline std::vector<std::string> g_scene_ids;
inline void set_scene_ids(std::vector<std::string> ids) { g_scene_ids = std::move(ids); }

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
  if (n.empty() || n.size() >= 100 || n[0] == '.' ||
      n.find('/') != std::string::npos || n.find("..") != std::string::npos)
    return false;
  // Names go out inside /api/files and /api/status JSON. json_escape keeps
  // the parse alive whatever the card holds; this keeps a quote, backslash
  // or control byte from ever getting ONTO the card through us.
  for (unsigned char c : n)
    if (c < 0x20 || c == 0x7f || c == '"' || c == '\\') return false;
  return true;
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

/// The strings in /api/status and the names in /api/files are device-sourced
/// (card filenames, the boot manifest's missing list, a scene id) and go out
/// inside JSON string literals. safe_name keeps quotes out of anything the
/// desk uploads, but a file the Mac wrote straight onto the card is not the
/// desk's doing — so escape at the exit instead of trusting the entrance.
/// Same table as Python's json.dumps, which is what the emulator uses.
inline std::string json_escape(const std::string &s) {
  std::string out;
  out.reserve(s.size() + 8);
  for (unsigned char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      default:
        if (c < 0x20) {
          char u[8];
          snprintf(u, sizeof(u), "\\u%04x", c);
          out += u;
        } else {
          out.push_back((char) c);
        }
    }
  }
  return out;
}

inline std::string query_param(httpd_req_t *req, const char *key) {
  char q[200] = {0};
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return "";
  char val[120] = {0};
  if (httpd_query_key_value(q, key, val, sizeof(val)) != ESP_OK) return "";
  return url_decode(val);
}

// ── /api/status ─────────────────────────────────────────────────────────
/// Card capacity, cached: f_getfree walks the FAT when FSINFO is stale,
/// which can cost seconds on a big card — not a price every 15 s poll
/// should pay. A minute of staleness on "GB free" costs nothing.
inline void sd_space_kb(unsigned &total, unsigned &free_) {
  static int64_t at = -60 * 1000000LL;
  static unsigned t = 0, f = 0;
  if (castle_sd::g_mounted && esp_timer_get_time() - at > 60 * 1000000LL) {
    uint64_t tb = 0, fb = 0;
    if (esp_vfs_fat_info("/sd", &tb, &fb) == ESP_OK) {
      t = (unsigned) (tb / 1024);
      f = (unsigned) (fb / 1024);
    }
    at = esp_timer_get_time();
  }
  total = castle_sd::g_mounted ? t : 0;
  free_ = castle_sd::g_mounted ? f : 0;
}

inline esp_err_t h_status(httpd_req_t *req) {
  std::string scene, track, pir_scene, missing;
  {
    std::lock_guard<std::mutex> lk(g_state_mu);
    scene = g_scene; track = g_track; pir_scene = g_pir_scene;
    missing = g_missing;
  }
  unsigned sd_total = 0, sd_free = 0;
  sd_space_kb(sd_total, sd_free);
  // Numbers through snprintf, strings through json_escape into a
  // std::string: a fixed buffer truncated silently when the boot manifest
  // listed more than a few missing files, and every client's parse died.
  char buf[240];
  snprintf(buf, sizeof(buf),
           "{\"version\":\"%s\",\"compiled\":\"%s %s\",\"uptime_s\":%lld,"
           "\"sd_mounted\":%s,\"psram_free_kb\":%u,\"heap_free_kb\":%u,"
           "\"sd_total_kb\":%u,\"sd_free_kb\":%u,\"missing\":\"",
           CASTLE_VERSION, __DATE__, __TIME__,
           (long long) (esp_timer_get_time() / 1000000),
           castle_sd::g_mounted ? "true" : "false",
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024),
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
           sd_total, sd_free);
  std::string out = buf;
  out += json_escape(missing);
  snprintf(buf, sizeof(buf), "\",\"volume\":%d,\"scene\":\"", g_volume.load());
  out += buf;
  out += json_escape(scene);
  out += "\",\"track\":\"";
  out += json_escape(track);
  snprintf(buf, sizeof(buf),
           "\",\"show_on\":%s,\"pir\":{\"armed\":%s,\"cooldown_s\":%d,\"scene\":\"",
           g_show_on.load() ? "true" : "false",
           g_pir_armed.load() ? "true" : "false", g_pir_cooldown.load());
  out += buf;
  out += json_escape(pir_scene);
  out += "\"}}";
  return reply_json(req, out);
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
  unsigned skipped = 0;
  struct dirent *e;
  while ((e = readdir(d)) != nullptr) {
    if (e->d_name[0] == '.') continue;
    // A name safe_name refuses is one the desk could never have uploaded
    // and /api/play could never be asked for — the Mac wrote it straight
    // onto the card. Counted, not listed: the desk should not offer a
    // track the castle will then refuse by name.
    if (!safe_name(e->d_name)) { skipped++; continue; }
    char full[300];
    snprintf(full, sizeof(full), "/sd/%s", e->d_name);
    struct stat st{};
    long size = (stat(full, &st) == 0) ? (long) st.st_size : -1;
    char tail[48];
    snprintf(tail, sizeof(tail), "\",\"size\":%ld,\"dir\":%s}", size,
             (e->d_type == DT_DIR) ? "true" : "false");
    if (out.size() > 1) out += ",";
    out += "{\"name\":\"";
    out += json_escape(e->d_name);
    out += tail;
  }
  closedir(d);
  // One trailing {"skipped":N} element, only when N > 0. Every reader of
  // this array filters on name/dir, so an element with neither is invisible
  // to them — and visible to anyone wondering why a file is not listed.
  if (skipped > 0) {
    char t[40];
    snprintf(t, sizeof(t), "%s{\"skipped\":%u}", out.size() > 1 ? "," : "", skipped);
    out += t;
  }
  out += "]";
  return reply_json(req, out);
}

// ── uploads: PUT /api/files/<name>, /api/site/<name>, /api/scenes/<name> ─
/// Into `<path>.part` first; the real name changes hands only once every
/// byte is on the card. A WiFi drop at 80% of a re-send used to take the
/// PREVIOUS good copy down with it (the old code opened the real name for
/// writing and unlinked it on failure). The studio side was fixed for this
/// class in 3ccdd8b; this is the device side.
inline esp_err_t write_body(httpd_req_t *req, const char *path) {
  const std::string part = std::string(path) + ".part";
  FILE *f = fopen(part.c_str(), "wb");
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
    unlink(part.c_str());  // the sidecar only; whatever `path` held still plays
    ESP_LOGE(TAG, "upload of %s failed at %u bytes", path, (unsigned) written);
    return reply_err(req, "500 Internal Server Error", "short write");
  }
  // FAT's rename refuses to overwrite, so the old copy goes first. The
  // window between the two calls is a missing file, never a torn one.
  unlink(path);
  if (rename(part.c_str(), path) != 0) {
    ESP_LOGE(TAG, "rename %s -> %s failed (errno %d)", part.c_str(), path, errno);
    unlink(part.c_str());
    return reply_err(req, "500 Internal Server Error", "rename failed");
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
  // {"queued":true} for a scene that does not exist is a lie the desk then
  // toasts as success. The id list is seeded at boot from pir_scene's options.
  if (!g_scene_ids.empty() &&
      std::find(g_scene_ids.begin(), g_scene_ids.end(), s) == g_scene_ids.end())
    return reply_err(req, "404 Not Found", "unknown scene");
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
  // Digits only. atoi("abc") is 0, which turned a malformed request into a
  // silent mute — the kind of "worked, but wrong" a fuzz pass exists to find.
  const bool digits = !v.empty() && v.size() <= 3 &&
      v.find_first_not_of("0123456789") == std::string::npos;
  int pct = digits ? atoi(v.c_str()) : -1;
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

#include "sd_web_ota.h"
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
  // MUST exceed the reg() count below (23 today). At 20, the LAST THREE
  // registrations failed silently on the device — /sd/* (the very URL the
  // media pipeline streams scene audio through), /site/* and / — so the
  // cue desk 404'd and SD streaming was dead while every /api route worked.
  // Found on the live board 2026-08-15; headroom so the next route is free.
  cfg.max_uri_handlers = 32;
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
  // Playback must never queue behind the control plane — the decoder pulls
  // its audio through this second server. See sd_web_stream.h.
  castle_stream::start(h_sd_get);
  reg("/site/*", HTTP_GET, h_site);
  reg("/", HTTP_GET, h_root);
  ESP_LOGI(TAG, "web server up on port %d", cfg.server_port);
}

}  // namespace castle_web
