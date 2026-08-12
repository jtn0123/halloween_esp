// A tiny HTTP server on the device: manage the SD card from the Mac, and a
// control page anyone on the WiFi can open.
//
// WHY IT EXISTS. The card lives in a FeatherWing socket behind the castle
// wall. Every "copy a file to it" used to mean: power down, extract the card,
// walk to the Mac, copy, walk back, reseat, reboot. This server replaces that
// with `tools/sd_sync.py push` — the card never leaves the slot.
//
// TWO KINDS OF WORK, TWO RULES:
//
//   Filesystem work (list/upload/delete) happens RIGHT HERE in the httpd
//   task. That is safe: ESP-IDF's FATFS layer takes a per-volume lock, so a
//   concurrent castle_sd::load() on the main loop blocks briefly instead of
//   corrupting. Playback is unaffected — it reads from PSRAM, not the card.
//
//   ESPHome work (play a file, run a scene, stop) must NOT happen here.
//   Scripts, scheduler, media player — none of it is thread-safe outside the
//   main loop. So those requests only record a pending action, and a 200 ms
//   `interval:` in the YAML picks it up and executes it on the loop. The
//   HTTP reply means "queued", not "done"; the log says what happened.
//
// THE PAGE. GET / serves /sd/site/index.html when the card has one — so the
// real UI ships to the card like any other file, updated by sync instead of
// reflash — and falls back to a built-in bare-bones page so a blank card
// still gives you buttons.

#pragma once

#include <esp_http_server.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <atomic>
#include <mutex>
#include <string>
#include <cstdio>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esphome/core/log.h"
#include "boot_log.h"
#include "sd_audio.h"

namespace castle_web {

static const char *const TAG = "castle_web";

inline httpd_handle_t g_server = nullptr;

// ── pending action, handed from httpd task to the main loop ─────────────
enum ActionType { NONE = 0, PLAY = 1, SCENE = 2, STOP = 3, VOLUME = 4, LIGHT = 5 };

/// Mirrored from the media player by the YAML interval (the httpd task must
/// not touch ESPHome objects), so /api/status can report the real volume and
/// the page's slider can start where the amp actually is.
inline std::atomic<int> g_volume{70};
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

// ── /api/status ─────────────────────────────────────────────────────────
inline esp_err_t h_status(httpd_req_t *req) {
  char buf[340];
  snprintf(buf, sizeof(buf),
           "{\"version\":\"%s\",\"compiled\":\"%s %s\",\"uptime_s\":%lld,"
           "\"sd_mounted\":%s,\"psram_free_kb\":%u,\"heap_free_kb\":%u,"
           "\"volume\":%d}",
           CASTLE_VERSION, __DATE__, __TIME__,
           (long long) (esp_timer_get_time() / 1000000),
           castle_sd::g_mounted ? "true" : "false",
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024),
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
           g_volume.load());
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

// ── PUT /api/files/<name>, PUT /api/site/<name> — upload raw body ───────
inline esp_err_t write_body(httpd_req_t *req, const char *path);

inline esp_err_t h_upload(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  std::string name = name_from_uri(req, "/api/files/");
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad filename");
  char path[160];
  snprintf(path, sizeof(path), "/sd/%s", name.c_str());
  return write_body(req, path);
}

// The site directory is how the good web page gets to the device: build it on
// the Mac, PUT it here, and GET / starts serving it instead of the fallback.
// A page update is a file copy, not a reflash.
inline esp_err_t h_site_put(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  std::string name = name_from_uri(req, "/api/site/");
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad filename");
  mkdir("/sd/site", 0775);  // EEXIST is fine; any other failure surfaces below
  char path[160];
  snprintf(path, sizeof(path), "/sd/site/%s", name.c_str());
  return write_body(req, path);
}

inline esp_err_t write_body(httpd_req_t *req, const char *path) {
  FILE *f = fopen(path, "wb");
  if (f == nullptr) return reply_err(req, "500 Internal Server Error", "cannot create file");

  // 8 KB chunks: big enough that the SD write is the bottleneck, small
  // enough to live on this task's stack budget (heap, actually — see start()).
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
  }
  free(buf);
  fclose(f);
  if (!ok) {
    unlink(path);  // a half-written MP3 is worse than a missing one
    ESP_LOGE(TAG, "upload of %s failed at %u bytes", path, (unsigned) written);
    return reply_err(req, "500 Internal Server Error", "short write");
  }
  ESP_LOGI(TAG, "uploaded %s (%u KB)", path, (unsigned) (written / 1024));
  char body[200];
  snprintf(body, sizeof(body), "{\"path\":\"%s\",\"bytes\":%u}", path,
           (unsigned) written);
  return reply_json(req, body);
}

// ── DELETE /api/files/<name> ────────────────────────────────────────────
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

// ── POST /api/play?f=<name>, /api/scene?s=<id>, /api/stop ───────────────
inline std::string query_param(httpd_req_t *req, const char *key) {
  char q[160] = {0};
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return "";
  char val[120] = {0};
  if (httpd_query_key_value(q, key, val, sizeof(val)) != ESP_OK) return "";
  return url_decode(val);
}

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

// ── POST /api/volume?v=<0..100> ─────────────────────────────────────────
inline esp_err_t h_volume(httpd_req_t *req) {
  std::string v = query_param(req, "v");
  int pct = v.empty() ? -1 : atoi(v.c_str());
  if (pct < 0 || pct > 100) return reply_err(req, "400 Bad Request", "need ?v=0..100");
  set_pending(VOLUME, std::to_string(pct));
  return reply_json(req, "{\"queued\":true}");
}

// ── POST /api/light?c=<RRGGBB | show | off> ─────────────────────────────
// A manual override for the pixel chain. "show" hands control back to the
// scene engine's effect; a hex colour parks the chain on that colour — which
// today means the one onboard pixel, and later means all 21.
inline esp_err_t h_light(httpd_req_t *req) {
  std::string c = query_param(req, "c");
  bool hex6 = c.size() == 6 && c.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos;
  if (!hex6 && c != "show" && c != "off")
    return reply_err(req, "400 Bad Request", "need ?c=RRGGBB, show, or off");
  set_pending(LIGHT, c);
  return reply_json(req, "{\"queued\":true}");
}

// ── /api/bootlog — the ring buffer, as text ─────────────────────────────
// This is the same data as the "Dump boot log" button, but pulled instead of
// pushed: no API subscription, no log-level negotiation, just the bytes.
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

// ── GET / and /site/* — the page ────────────────────────────────────────

inline const char *content_type(const std::string &p) {
  auto ends = [&p](const char *s) {
    size_t n = strlen(s);
    return p.size() >= n && p.compare(p.size() - n, n, s) == 0;
  };
  // charset matters: the desk is a megabyte of UTF-8, and its <meta charset>
  // sits too deep in the file for the browser's pre-scan — without the header
  // every · and ² on the page renders as mojibake.
  if (ends(".html")) return "text/html; charset=utf-8";
  if (ends(".js")) return "application/javascript";
  if (ends(".css")) return "text/css";
  if (ends(".svg")) return "image/svg+xml";
  if (ends(".png")) return "image/png";
  if (ends(".json")) return "application/json";
  if (ends(".mp3")) return "audio/mpeg";
  return "application/octet-stream";
}

/// Stream a file off the card. Returns false if it does not exist.
inline bool send_sd_file(httpd_req_t *req, const char *path) {
  FILE *f = fopen(path, "rb");
  if (f == nullptr) return false;
  httpd_resp_set_type(req, content_type(path));
  static constexpr size_t CHUNK = 4096;
  char *buf = (char *) malloc(CHUNK);
  if (buf == nullptr) { fclose(f); reply_err(req, "500 Internal Server Error", "no memory"); return true; }
  size_t got;
  while ((got = fread(buf, 1, CHUNK, f)) > 0) {
    if (httpd_resp_send_chunk(req, buf, got) != ESP_OK) break;
  }
  free(buf);
  fclose(f);
  httpd_resp_send_chunk(req, nullptr, 0);
  return true;
}

// The fallback page, for a card with no /site/ on it (or no card at all).
// Deliberately spartan: the good page lives on the card, this one only has to
// prove the server works and give you buttons that press.
inline const char kFallbackPage[] = R"HTML(<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Castle</title>
<style>body{font:16px system-ui;background:#14101c;color:#e8e0f0;margin:2rem auto;max-width:40rem;padding:0 1rem}
button{background:#3a2a55;color:inherit;border:0;border-radius:8px;padding:.6rem 1rem;margin:.2rem;cursor:pointer}
button:hover{background:#503a75}li{margin:.3rem 0;list-style:none}#files{padding:0}
small{color:#9a8fb0}h1{font-size:1.3rem}</style>
<h1>🏰 Castle <small id=v></small></h1>
<div id=scenes></div>
<button onclick="api('/api/stop')">■ Stop</button>
<h3>SD card</h3><ul id=files></ul><pre id=log></pre>
<script>
const S=['vigil','storm','seance','ballroom','descent','visitation','approach','crypt'];
const api=(u,m)=>fetch(u,{method:m||'POST'});
scenes.innerHTML=S.map(s=>`<button onclick="api('/api/scene?s=${s}')">${s}</button>`).join('');
fetch('/api/status').then(r=>r.json()).then(s=>v.textContent=s.version+' · '+(s.sd_mounted?'SD ok':'no SD'));
fetch('/api/files').then(r=>r.json()).then(fs=>files.innerHTML=fs.filter(f=>!f.dir).map(f=>
 `<li><button onclick="api('/api/play?f=${encodeURIComponent(f.name)}')">▶</button> ${f.name} <small>${(f.size/1024)|0} KB</small></li>`).join(''))
 .catch(()=>files.innerHTML='<li><small>no card</small></li>');
</script>)HTML";

inline esp_err_t h_root(httpd_req_t *req) {
  if (castle_sd::g_mounted && send_sd_file(req, "/sd/site/index.html")) return ESP_OK;
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, kFallbackPage, HTTPD_RESP_USE_STRLEN);
}

inline esp_err_t h_site(httpd_req_t *req) {
  std::string name = name_from_uri(req, "/site/");
  if (!safe_name(name)) return reply_err(req, "400 Bad Request", "bad path");
  char path[160];
  snprintf(path, sizeof(path), "/sd/site/%s", name.c_str());
  if (!castle_sd::g_mounted || !send_sd_file(req, path))
    return reply_err(req, "404 Not Found", "not on card");
  return ESP_OK;
}

// ── startup ─────────────────────────────────────────────────────────────
inline void start() {
  if (g_server != nullptr) return;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.uri_match_fn = httpd_uri_match_wildcard;
  cfg.max_uri_handlers = 16;
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
  reg("/api/files", HTTP_GET, h_list);
  reg("/api/files/*", HTTP_PUT, h_upload);
  reg("/api/site/*", HTTP_PUT, h_site_put);
  reg("/api/files/*", HTTP_DELETE, h_delete);
  reg("/api/play", HTTP_POST, h_play);
  reg("/api/scene", HTTP_POST, h_scene);
  reg("/api/stop", HTTP_POST, h_stop);
  reg("/api/volume", HTTP_POST, h_volume);
  reg("/api/light", HTTP_POST, h_light);
  reg("/api/bootlog", HTTP_GET, h_bootlog);
  reg("/site/*", HTTP_GET, h_site);
  reg("/", HTTP_GET, h_root);
  ESP_LOGI(TAG, "web server up on port %d", cfg.server_port);
}

}  // namespace castle_web
