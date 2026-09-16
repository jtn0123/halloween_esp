// The castle's two read-only readouts, both of them rings in RAM:
//
//   GET /api/bootlog  — boot_log.h, the lines the mount and the listing
//                       printed before the API was up, as text.
//   GET /api/events   — sd_web_state.h, what the MAIN LOOP actually did:
//                       one entry per executed command, plus the audio
//                       clock's start/end and dropped light frames.
//
// Split out of sd_web.h (the 500-line rule, and an honest seam: neither
// handler touches the card, a route or an ESPHome object). Both read state
// the main loop writes, under the same rules as h_status — atomics, or a
// copy taken under the ring's own lock so the loop never waits on a
// browser. Nothing here writes the SD card: a card write from a request is
// a stall in the audio pipeline, which is exactly what /api/events exists
// to explain.
#pragma once

#include <esp_http_server.h>
#include <array>
#include <cstdio>
#include <memory>
#include <string>

#include "boot_log.h"
#include "sd_web_state.h"
#include "sd_web_util.h"

namespace castle_web {

/// The ring as JSON, oldest first:
///   [{"t":<uptime_ms>,"e":"play|scene|...","a":"<arg or empty>"}, ...]
inline std::string events_json(const Event *evs, size_t n) {
  std::string out = "[";
  for (size_t i = 0; i < n; i++) {
    std::array<char, 48> head{};
    snprintf(head.data(), head.size(), R"(%s{"t":%lld,"e":")", i > 0 ? "," : "",
             evs[i].t_ms);
    out += head.data();
    out += event_kind_str(evs[i].kind);
    out += R"(","a":")";
    out += json_escape(evs[i].arg);
    out += R"("})";
  }
  out += "]";
  return out;
}

inline esp_err_t h_events(httpd_req_t *req) {
  // 64 entries is ~3 KB — too much for the httpd task's stack next to the
  // reply, so the snapshot is one heap block, taken and released per
  // request. The RING itself never grows; this is the copy.
  const auto snap = std::unique_ptr<std::array<Event, kEventRing>>(
      new (std::nothrow) std::array<Event, kEventRing>);
  if (snap == nullptr) return reply_err(req, "500 Internal Server Error", "no memory");
  const size_t n = copy_events(snap->data());
  return reply_json(req, events_json(snap->data(), n));
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
  std::array<char, 80> hdr{};
  snprintf(hdr.data(), hdr.size(), "boot log: %u lines, %u dropped\n", (unsigned) held,
           (unsigned) castle_log::g_dropped);
  httpd_resp_send_chunk(req, hdr.data(), HTTPD_RESP_USE_STRLEN);
  for (size_t i = 0; i < held; i++) {
    const char *line =
        castle_log::g_buf + ((first + i) % castle_log::LINES) * castle_log::WIDTH;
    httpd_resp_send_chunk(req, line, HTTPD_RESP_USE_STRLEN);
    httpd_resp_send_chunk(req, "\n", 1);
  }
  return httpd_resp_send_chunk(req, nullptr, 0);
}

}  // namespace castle_web
