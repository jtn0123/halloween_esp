#pragma once
// The serving half of the castle's web server: static files off the card,
// the built-in fallback page, and the /sd/ streaming route.
//
// Split from sd_web.h purely for the 500-line rule; sd_web.h includes this
// and registers these handlers from its start(). The split line is "bytes
// out" (here) vs "control in" (there).
//
// THE STREAMING ROUTE IS LOAD-BEARING. GET /sd/<path> streams any file on
// the card — and the media pipeline's own URL reader is a client: scenes on
// the SD build play by fetching http://127.0.0.1/sd/scenes/<track>.mp3 from
// this very handler. Loopback never touches the radio; the SPI card read is
// the only real I/O. That one route is what turned "whole file into PSRAM"
// into true streaming with no cap and no custom AudioReader.

#include <esp_http_server.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <sys/stat.h>

#include "esphome/core/log.h"
#include "sd_audio.h"

namespace castle_web {

esp_err_t reply_err(httpd_req_t *req, const char *status, const char *msg);
std::string url_decode(const char *s);

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
  if (ends(".wav")) return "audio/wav";
  return "application/octet-stream";
}

/// A path that may contain subdirectories but must stay inside /sd:
/// no "..", no leading dot segments, no absolute escapes.
inline bool safe_subpath(const std::string &p) {
  if (p.empty() || p.size() > 140 || p[0] == '/' || p[0] == '.') return false;
  return p.find("..") == std::string::npos;
}

/// Stream a file off the card. Returns false if it does not exist. Content
/// type from the name; optional Content-Encoding for pre-compressed assets.
inline bool send_sd_file(httpd_req_t *req, const char *path,
                         const char *encoding = nullptr,
                         const char *type_override = nullptr) {
  FILE *f = fopen(path, "rb");
  if (f == nullptr) return false;
  httpd_resp_set_type(req, type_override ? type_override : content_type(path));
  if (encoding != nullptr) httpd_resp_set_hdr(req, "Content-Encoding", encoding);
  static constexpr size_t CHUNK = 4096;
  char *buf = (char *) malloc(CHUNK);
  if (buf == nullptr) {
    fclose(f);
    reply_err(req, "500 Internal Server Error", "no memory");
    return true;
  }
  size_t got;
  while ((got = fread(buf, 1, CHUNK, f)) > 0) {
    if (httpd_resp_send_chunk(req, buf, got) != ESP_OK) break;
  }
  free(buf);
  fclose(f);
  httpd_resp_send_chunk(req, nullptr, 0);
  return true;
}

// ── GET /sd/* — stream any card file (subdirectories allowed) ───────────
inline esp_err_t h_sd_get(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  std::string rel = url_decode(req->uri + strlen("/sd/"));
  auto q = rel.find('?');
  if (q != std::string::npos) rel.resize(q);
  if (!safe_subpath(rel)) return reply_err(req, "400 Bad Request", "bad path");
  char path[200];
  snprintf(path, sizeof(path), "/sd/%s", rel.c_str());
  if (!send_sd_file(req, path)) return reply_err(req, "404 Not Found", "no such file");
  return ESP_OK;
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
  if (castle_sd::g_mounted) {
    // Prefer the pre-compressed desk: ~3x fewer bytes over the radio, and
    // every browser this decade sends Accept-Encoding: gzip. sd_sync pushes
    // both forms, so a stale .gz cannot shadow a newer plain file.
    if (send_sd_file(req, "/sd/site/index.html.gz", "gzip",
                     "text/html; charset=utf-8"))
      return ESP_OK;
    if (send_sd_file(req, "/sd/site/index.html")) return ESP_OK;
  }
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, kFallbackPage, HTTPD_RESP_USE_STRLEN);
}

inline esp_err_t h_site(httpd_req_t *req) {
  std::string rel = url_decode(req->uri + strlen("/site/"));
  auto q = rel.find('?');
  if (q != std::string::npos) rel.resize(q);
  if (!safe_subpath(rel)) return reply_err(req, "400 Bad Request", "bad path");
  char path[200];
  snprintf(path, sizeof(path), "/sd/site/%s", rel.c_str());
  if (!castle_sd::g_mounted || !send_sd_file(req, path))
    return reply_err(req, "404 Not Found", "not on card");
  return ESP_OK;
}

}  // namespace castle_web
