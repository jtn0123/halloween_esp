#pragma once

#include <array>
#include <memory>
#include <string_view>
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
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <sys/stat.h>

#include "esphome/core/log.h"
#include "castle_health.h"
#include "sd_audio.h"
#include "fallback_scenes.h"

namespace castle_web {

esp_err_t reply_err(httpd_req_t *req, const char *status, const char *msg);
std::string url_decode(const char *s);

/// E4: one CSP on every page we serve — depth behind the escaping, not a
/// substitute for it (safe_name still admits '<' and '>', so a filename is
/// one missed esc() away from running). The desk is deliberately a single
/// self-contained file, so inline script/style must stay allowed; what the
/// header removes is everything ELSE an injected tag could do: no external
/// fetches, no foreign media, no form posts off-box.
inline void set_csp(httpd_req_t *req) {
  httpd_resp_set_hdr(req, "Content-Security-Policy",
                     "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                     "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                     "media-src 'self' data: blob:; connect-src 'self'");
}

inline const char *content_type(const std::string &p) {
  auto ends = [&p](std::string_view s) {
    return p.size() >= s.size() && p.compare(p.size() - s.size(), s.size(), s) == 0;
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
  if (ends(".opus")) return "audio/ogg";
  if (ends(".wav")) return "audio/wav";
  return "application/octet-stream";
}

/// What one attempt to stream a card file came to. A8 (v5.61) split this
/// out of a bool: "the file is not there" and "the card stopped answering
/// half way through" used to be the same `true`, and the caller had no way
/// to tell a finished transfer from an abandoned one.
enum class Sent {
  MISSING,   //!< no such file — the caller may try the next candidate
  WHOLE,     //!< every byte went out and the terminating chunk with it
  TORN,      //!< a read failed mid-file; the reply has been dealt with here
};

/// Stream a file off the card. Content type from the name; optional
/// Content-Encoding for pre-compressed assets.
///
/// A8 (v5.61): A READ ERROR IS NOT A SHORT FILE. The loop below used to
/// stop on any fread that returned 0 — end of file and "the SPI card
/// stopped answering" alike — and then send the terminating chunk, so a
/// track that died at 12% went out as a clean, complete, well-formed 200
/// holding 12% of a song. Every client believed it: the media player played
/// silence to the end of what it got and reported success, sd_sync's CRC
/// compare was the only thing in the whole system that would have noticed,
/// and it does not read this route. A card that is going bad looked exactly
/// like a short file.
///
/// So the failure is made visible in the only two ways HTTP allows:
///
///   nothing sent yet  → a real 500. The status line is still ours.
///   mid-body          → the chunked response is ABANDONED: no terminating
///                       0-length chunk, and ESP_FAIL back to httpd, which
///                       closes the socket. A chunked body that ends without
///                       its terminator is a protocol error every HTTP
///                       client in the world already knows how to report —
///                       curl says "transfer closed with outstanding read",
///                       the decoder errors instead of finishing early.
///
/// and counted in castle_health, so /api/health answers "is this card going
/// bad" with a number instead of a shrug.
inline Sent send_sd_file(httpd_req_t *req, const char *path,
                         const char *encoding = nullptr,
                         const char *type_override = nullptr) {
  FILE *f = fopen(path, "rb");
  if (f == nullptr) return Sent::MISSING;
  httpd_resp_set_type(req, type_override ? type_override : content_type(path));
  if (encoding != nullptr) httpd_resp_set_hdr(req, "Content-Encoding", encoding);
  static constexpr size_t CHUNK = 4096;
  // nothrow: exceptions are off in the ESP-IDF build, and a full heap must
  // answer 500 rather than abort the board.
  const auto buf = std::unique_ptr<std::array<char, CHUNK>>(new (std::nothrow) std::array<char, CHUNK>);
  if (buf == nullptr) {
    fclose(f);
    reply_err(req, "500 Internal Server Error", "no memory");
    return Sent::WHOLE;   // dealt with: the caller must not try elsewhere
  }
  size_t got = 0;
  size_t out = 0;
  while ((got = fread(buf->data(), 1, CHUNK, f)) > 0) {
    if (httpd_resp_send_chunk(req, buf->data(), got) != ESP_OK) break;
    out += got;
    // Yield between chunks. Without this, a bulk download (the 1 MB site
    // page) is hundreds of back-to-back SD reads + TCP sends on the httpd
    // task, and on this single-core S2 the watched main loop starves —
    // task-watchdog, reboot, and a castle that "goes offline" whenever a
    // browser holds the page open. Same disease, same cure as h_ota's
    // upload loop in sd_web.h; caps streaming at ~400 KB/s, far above
    // what audio playback (16 KB/s) or a page load needs.
    vTaskDelay(1);
  }
  // ferror, not feof: the ONE question the old loop never asked.
  const bool torn = ferror(f) != 0;
  fclose(f);
  if (torn) {
    castle_health::note_sd_read_error();
    ESP_LOGE("castle_web", "read error on %s after %u bytes — tearing the reply down",
             path, (unsigned) out);
    if (out == 0) {
      reply_err(req, "500 Internal Server Error", "card read failed");
      return Sent::WHOLE;   // a whole reply, just not a happy one
    }
    return Sent::TORN;      // no terminating chunk, on purpose
  }
  httpd_resp_send_chunk(req, nullptr, 0);
  return Sent::WHOLE;
}

// ── GET /sd/<path> — stream any card file (subdirectories allowed) ──────
inline esp_err_t h_sd_get(httpd_req_t *req) {
  if (!castle_sd::g_mounted) return reply_err(req, "503 Service Unavailable", "no SD card");
  std::string rel = url_decode(req->uri + std::string_view("/sd/").size());
  if (const auto q = rel.find('?'); q != std::string::npos) rel.resize(q);
  if (!safe_subpath(rel)) return reply_err(req, "400 Bad Request", "bad path");
  std::array<char, 200> path{};
  snprintf(path.data(), path.size(), "/sd/%s", rel.c_str());
  switch (send_sd_file(req, path.data())) {
    case Sent::MISSING: return reply_err(req, "404 Not Found", "no such file");
    // ESP_FAIL is what closes the socket under the half-sent body (A8).
    case Sent::TORN: return ESP_FAIL;
    case Sent::WHOLE: break;
  }
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
const S=[__FALLBACK_SCENES__];
const api=(u,m)=>fetch(u,{method:m||'POST'});
scenes.innerHTML=S.map(s=>`<button onclick="api('/api/scene?s=${s}')">${s}</button>`).join('');
fetch('/api/status').then(r=>r.json()).then(s=>v.textContent=s.version+' · '+(s.sd_mounted?'SD ok':'no SD'));
fetch('/api/files').then(r=>r.json()).then(fs=>files.innerHTML=fs.filter(f=>!f.dir).map(f=>
 `<li><button onclick="api('/api/play?f=${encodeURIComponent(f.name)}')">▶</button> ${f.name} <small>${(f.size/1024)|0} KB</small></li>`).join(''))
 .catch(()=>files.innerHTML='<li><small>no card</small></li>');
</script>)HTML";

inline esp_err_t h_root(httpd_req_t *req) {
  set_csp(req);
  if (castle_sd::g_mounted) {
    // Prefer the pre-compressed desk: ~3x fewer bytes over the radio, and
    // every browser this decade sends Accept-Encoding: gzip. sd_sync pushes
    // both forms. The .gz wins when both exist — a newer plain index.html
    // is ignored until the gzipped copy is replaced too (see README).
    // MISSING falls through to the next candidate; anything else is this
    // request's whole answer, torn or not (A8) — a desk page that died
    // half way must not be followed by a second, smaller desk page.
    Sent sent = send_sd_file(req, "/sd/site/index.html.gz", "gzip",
                             "text/html; charset=utf-8");
    if (sent == Sent::MISSING) sent = send_sd_file(req, "/sd/site/index.html");
    if (sent == Sent::TORN) return ESP_FAIL;
    if (sent == Sent::WHOLE) return ESP_OK;
  }
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  std::string page = kFallbackPage;
  static constexpr const char kMark[] = "__FALLBACK_SCENES__";
  if (const auto at = page.find(kMark); at != std::string::npos)
    page.replace(at, sizeof(kMark) - 1, kFallbackSceneIds);
  return httpd_resp_send(req, page.c_str(), page.size());
}

inline esp_err_t h_site(httpd_req_t *req) {
  set_csp(req);
  std::string rel = url_decode(req->uri + std::string_view("/site/").size());
  if (const auto q = rel.find('?'); q != std::string::npos) rel.resize(q);
  if (!safe_subpath(rel)) return reply_err(req, "400 Bad Request", "bad path");
  std::array<char, 200> path{};
  snprintf(path.data(), path.size(), "/sd/site/%s", rel.c_str());
  const Sent sent = castle_sd::g_mounted ? send_sd_file(req, path.data())
                                         : Sent::MISSING;
  if (sent == Sent::MISSING) return reply_err(req, "404 Not Found", "not on card");
  return sent == Sent::TORN ? ESP_FAIL : ESP_OK;
}

}  // namespace castle_web
