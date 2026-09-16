#pragma once
// The castle web server's helper layer — URL decoding, name validation and
// JSON plumbing shared by every handler. Split from sd_web.h purely for the
// 500-line rule; the seam is honest: nothing here touches a route, a card
// or ESPHome state. tests/test_firmware_contract.py parses this file along
// with the handler headers, so the emulator's port stays byte-exact.

#include <esp_http_server.h>
#include <algorithm>
#include <array>
#include <cctype>
#include <cstdlib>
#include <string>
#include <string_view>

namespace castle_web {

// ── helpers ─────────────────────────────────────────────────────────────

/// %20 and friends. Uploaded names arrive URL-encoded in the path.
/// A `%` that is not two hex digits is a failure (empty result), not NUL.
inline std::string url_decode(const char *s) {
  std::string out;
  const std::string_view in{s};
  for (size_t i = 0; i < in.size(); i++) {
    const char c = in[i];
    if (c == '%' && i + 2 < in.size()) {
      const auto a = static_cast<unsigned char>(in[i + 1]);
      if (const auto b = static_cast<unsigned char>(in[i + 2]);
          !isxdigit(a) || !isxdigit(b)) {
        return {};
      }
      const std::array<char, 3> hex{{in[i + 1], in[i + 2], '\0'}};
      out.push_back(static_cast<char>(strtol(hex.data(), nullptr, 16)));
      i += 2;
    } else if (c == '+') {
      out.push_back(' ');
    } else {
      out.push_back(c);
    }
  }
  return out;
}

/// One path component: no slashes, nothing hidden. `..` is refused only as
/// the whole name (it starts with `.`); `foo..bar` is a legal FAT name.
inline bool safe_name(const std::string &n) {
  if (n.empty() || n.size() >= 100 || n[0] == '.' ||
      std::any_of(n.begin(), n.end(), [](char ch) { return ch == '/'; }))
    return false;
  // Names go out inside /api/files and /api/status JSON. json_escape keeps
  // the parse alive whatever the card holds; this keeps a quote, backslash
  // or control byte from ever getting ONTO the card through us. Since v5.46
  // that includes everything above ASCII: json_escape passes a high byte
  // through raw, so one lone 0x80 in a name made the whole body invalid
  // UTF-8 and every Python client of the castle raised instead of parsing
  // (make publish, the desk's device panel). ASCII names only.
  return std::none_of(n.begin(), n.end(), [](unsigned char c) {
    return c < 0x20 || c >= 0x80 || c == 0x7f || c == '"' || c == '\\';
  });
}

/// The filename after a fixed prefix like "/api/files/".
inline std::string name_from_uri(const httpd_req_t *req, const char *prefix) {
  const std::string_view uri{req->uri};
  const std::string_view pre{prefix};
  std::string n = url_decode(
      uri.size() >= pre.size() ? uri.data() + pre.size() : "");
  if (const auto q = n.find('?'); q != std::string::npos) n.resize(q);
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
  constexpr char kHex[] = "0123456789abcdef";
  for (unsigned char c : s) {
    switch (c) {
      case '"': out += R"(\")"; break;
      case '\\': out += R"(\\)"; break;
      case '\n': out += R"(\n)"; break;
      case '\r': out += R"(\r)"; break;
      case '\t': out += R"(\t)"; break;
      case '\b': out += R"(\b)"; break;
      case '\f': out += R"(\f)"; break;
      default:
        if (c < 0x20) {
          out += R"(\u00)";
          out.push_back(kHex[c >> 4]);
          out.push_back(kHex[c & 0xf]);
        } else {
          out.push_back((char) c);
        }
    }
  }
  return out;
}

/// httpd_req_get_url_query_str into 200 bytes: a longer query is TRUNC, not
/// a silently empty parameter (that used to look like "need ?f=").
inline bool query_truncated(httpd_req_t *req) {
  std::array<char, 200> q{};
  return httpd_req_get_url_query_str(req, q.data(), q.size()) ==
         ESP_ERR_HTTPD_RESULT_TRUNC;
}

inline std::string query_param(httpd_req_t *req, const char *key) {
  std::array<char, 200> q{};
  if (httpd_req_get_url_query_str(req, q.data(), q.size()) != ESP_OK) return "";
  std::array<char, 120> val{};
  if (httpd_query_key_value(q.data(), key, val.data(), val.size()) != ESP_OK)
    return "";
  return url_decode(val.data());
}

/// A path that may contain subdirectories but must stay inside /sd:
/// no empty, `.` or `..` segments, and no name that starts with `.`.
inline bool safe_subpath(const std::string &p) {
  if (p.empty() || p.size() > 140 || p[0] == '/') return false;
  size_t i = 0;
  while (i <= p.size()) {
    size_t j = p.find('/', i);
    if (j == std::string::npos) j = p.size();
    if (const std::string s = p.substr(i, j - i); s.empty() || s[0] == '.')
      return false;
    if (j == p.size()) break;
    i = j + 1;
  }
  return true;
}

/// POST /api/pir: desk tokens only. Mutates armed to "1" or "0".
inline bool pir_armed_ok(std::string &a) {
  if (a.empty()) return true;
  for (char &c : a) c = (char) tolower((unsigned char) c);
  if (a == "1" || a == "true" || a == "on") {
    a = "1";
    return true;
  }
  if (a == "0" || a == "false" || a == "off") {
    a = "0";
    return true;
  }
  return false;
}

inline bool pir_cooldown_ok(std::string_view c) {
  return c.empty() || c == "30" || c == "60" || c == "120";
}

}  // namespace castle_web
