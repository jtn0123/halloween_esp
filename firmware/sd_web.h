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
#include <esp_rom_crc.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <algorithm>
#include <vector>

#include "sd_web_util.h"
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
  // B1: the ids this BUILD was compiled with (seeded at boot, same list
  // /api/scene checks). `missing` can only speak about these — the desk
  // diffs them against scenes.yaml to spot a stale board before a pick
  // answers "unknown scene".
  out += "\",\"scenes\":\"";
  {
    std::string ids;
    for (const auto &id : g_scene_ids) {
      if (!ids.empty()) ids += ",";
      ids += id;
    }
    out += json_escape(ids);
  }
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
  // B2: ?d=<subdir> lists inside the card (scenes/, site/) — the desk could
  // never SEE the directory that holds the show. Validated like /sd/ paths.
  std::string sub = query_param(req, "d");
  char dirpath[160];
  if (sub.empty()) {
    snprintf(dirpath, sizeof(dirpath), "/sd");
  } else {
    if (!safe_subpath(sub)) return reply_err(req, "400 Bad Request", "bad path");
    snprintf(dirpath, sizeof(dirpath), "/sd/%s", sub.c_str());
  }
  DIR *d = opendir(dirpath);
  if (d == nullptr) return reply_err(req, "404 Not Found", "no such directory");
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
    snprintf(full, sizeof(full), "%s/%s", dirpath, e->d_name);
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
  // B3/E3: refuse what cannot fit, before the first byte — "short write"
  // at 80% of a full card told the operator nothing. 64 KB of slack keeps
  // FAT metadata and the .part sidecar honest.
  unsigned sd_total = 0, sd_free = 0;
  sd_space_kb(sd_total, sd_free);
  if (sd_total > 0 && req->content_len / 1024 + 64 > sd_free)
    return reply_err(req, "507 Insufficient Storage", "not enough room on the card");
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
  unsigned chunks = 0;
  uint32_t crc = 0;
  bool ok = true;
  while (remaining > 0) {
    int got = httpd_req_recv(req, buf, remaining < CHUNK ? remaining : CHUNK);
    if (got <= 0) { ok = false; break; }
    if (fwrite(buf, 1, got, f) != (size_t) got) { ok = false; break; }
    // B5: a cheap running checksum, returned to the sender — "bytes
    // matched" catches truncation but not a bad SD sector, which is a live
    // hypothesis in docs/ISSUE-scene-start-audio.md. sd_sync compares.
    crc = esp_rom_crc32_le(crc, (const uint8_t *) buf, got);
    remaining -= got;
    written += got;
    // The third appearance of this bug class (h_ota and send_sd_file were
    // the first two): back-to-back recv+SD-write on the httpd task starves
    // the watched main loop and the watchdog resets the castle mid-upload.
    // One tick per 32 KB (every 4th chunk, G6) keeps it fed at 4x the old
    // per-chunk cadence — RE-VERIFY ON THE BENCH before trusting a big
    // push on show night; if uploads reboot the board, go back to per-chunk.
    if ((++chunks & 3u) == 0) vTaskDelay(1);
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
  snprintf(body, sizeof(body), "{\"path\":\"%s\",\"bytes\":%u,\"crc32\":\"%08lx\"}",
           path, (unsigned) written, (unsigned long) crc);
  return reply_json(req, body);
}

/// PUT into /sd, /sd/site or /sd/scenes depending on the route. The scenes
/// directory is where the show's own tracks live (see audio_sd.yaml).
inline esp_err_t h_put(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  const char *dir = "";
  const char *prefix = "/api/files/";
  if (strncmp(req->uri, "/api/site/", 10) == 0) {
    dir = "site/"; prefix = "/api/site/";
    // E3: a desk page has a known plausible size (3.3 MB today); a mistake
    // must not eat the card. The free-space check in write_body bounds the
    // rest.
    if (req->content_len > 8u * 1024 * 1024)
      return reply_err(req, "413 Payload Too Large", "site file too large");
  }
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
  if (!light_spec_ok(c))    // RRGGBB|show|off, optionally "<zone>:" first
    return reply_err(req, "400 Bad Request", "need ?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]");
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
