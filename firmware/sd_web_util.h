#pragma once
// The castle web server's helper layer — URL decoding, name validation and
// JSON plumbing shared by every handler. Split from sd_web.h purely for the
// 500-line rule; the seam is honest: nothing here touches a route, a card
// or ESPHome state. tests/test_firmware_contract.py parses this file along
// with the handler headers, so the emulator's port stays byte-exact.

#include <esp_http_server.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace castle_web {

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

/// A path that may contain subdirectories but must stay inside /sd:
/// no "..", no leading dot segments, no absolute escapes. Used by the
/// /sd/ and /site/ serving routes (sd_web_site.h) and /api/files?d=.
inline bool safe_subpath(const std::string &p) {
  if (p.empty() || p.size() > 140 || p[0] == '/' || p[0] == '.') return false;
  return p.find("..") == std::string::npos;
}

}  // namespace castle_web
