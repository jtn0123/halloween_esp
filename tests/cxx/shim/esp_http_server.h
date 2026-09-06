#pragma once
// esp_http_server, in process and without a socket.
//
// The firmware's handlers are ordinary functions of a httpd_req_t; the only
// thing standing between them and a host test is the server that builds one.
// This is that server: the route table, the wildcard matcher, the 404/405
// verdicts, the body reader and the response accumulator — ported from
// ESP-IDF 5.5.5 (components/esp_http_server/src/httpd_uri.c, httpd_parse.c,
// httpd_txrx.c), which is the framework esphome pulls for this board.
//
// Ported, not invented: httpd_uri_match_wildcard and httpd_query_key_value
// decide what a request means (which handler, which ?f=), and a plausible
// re-derivation of either would make the harness agree with itself rather
// than with the porch. The pieces the firmware never reads — sockets,
// sessions, chunked framing, LRU purge — are simply absent.

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <strings.h>
#include <sys/types.h>
#include <vector>

#include <esp_err.h>
#include <esp_log.h>

// ── methods (http_parser's numbering) ───────────────────────────────────
typedef enum {
  HTTP_DELETE = 0,
  HTTP_GET = 1,
  HTTP_HEAD = 2,
  HTTP_POST = 3,
  HTTP_PUT = 4,
  HTTP_OPTIONS = 6,
  HTTP_PATCH = 28,
  HTTP_ANY = 0xff,
} httpd_method_t;

#define HTTPD_RESP_USE_STRLEN ((ssize_t) -1)
#define HTTPD_SOCK_ERR_FAIL (-1)
#define HTTPD_SOCK_ERR_INVALID (-2)
#define HTTPD_SOCK_ERR_TIMEOUT (-3)

#define HTTPD_TYPE_JSON "application/json"
#define HTTPD_TYPE_TEXT "text/html"
#define HTTPD_TYPE_OCTET "application/octet-stream"

/// CONFIG_HTTPD_MAX_URI_LEN. A longer request line never reaches a handler.
#define HTTPD_MAX_URI_LEN 512

typedef void *httpd_handle_t;

struct httpd_req {
  httpd_handle_t handle;
  int method;
  const char *uri;
  size_t content_len;
  void *aux;         //!< the shim's per-request state
  void *user_ctx;
  void *sess_ctx;
};
typedef struct httpd_req httpd_req_t;

typedef struct httpd_uri {
  const char *uri;
  httpd_method_t method;
  esp_err_t (*handler)(httpd_req_t *r);
  void *user_ctx;
} httpd_uri_t;

typedef bool (*httpd_uri_match_func_t)(const char *tpl, const char *uri, size_t len);

typedef struct httpd_config {
  unsigned task_priority;
  size_t stack_size;
  uint16_t server_port;
  uint16_t ctrl_port;
  uint16_t max_open_sockets;
  uint16_t max_uri_handlers;
  uint16_t max_resp_headers;
  uint16_t backlog_conn;
  bool lru_purge_enable;
  int recv_wait_timeout;
  int send_wait_timeout;
  void *global_user_ctx;
  httpd_uri_match_func_t uri_match_fn;
} httpd_config_t;

#define HTTPD_DEFAULT_CONFIG()                                                 \
  {                                                                            \
    /*task_priority*/ 5, /*stack_size*/ 4096, /*server_port*/ 80,              \
        /*ctrl_port*/ 32768, /*max_open_sockets*/ 7,                           \
        /*max_uri_handlers*/ 8, /*max_resp_headers*/ 8, /*backlog_conn*/ 5,    \
        /*lru_purge_enable*/ false, /*recv_wait_timeout*/ 5,                   \
        /*send_wait_timeout*/ 5, /*global_user_ctx*/ nullptr,                  \
        /*uri_match_fn*/ nullptr                                               \
  }

// ── httpd_uri_match_wildcard — IDF 5.5.5 httpd_uri.c, line for line ─────
inline bool httpd_uri_match_wildcard(const char *tpl, const char *uri, size_t len) {
  const size_t tpl_len = strlen(tpl);
  size_t exact_match_chars = tpl_len;
  // (IDF casts these to `const char`; the qualifier on a cast result is
  // meaningless in C++ and g++ -Wextra says so, so it moves to the object.)
  const char last = (char) (tpl_len > 0 ? tpl[tpl_len - 1] : 0);
  const char prevlast = (char) (tpl_len > 1 ? tpl[tpl_len - 2] : 0);
  const bool asterisk = last == '*' || (prevlast == '*' && last == '?');
  const bool quest = last == '?' || (prevlast == '?' && last == '*');
  if (exact_match_chars < (size_t) (asterisk + quest * 2)) return false;
  exact_match_chars -= (size_t) (asterisk + quest * 2);
  if (len < exact_match_chars) return false;
  if (!quest) {
    if (!asterisk && len != exact_match_chars) return false;
    return strncmp(tpl, uri, exact_match_chars) == 0;
  }
  if (len > exact_match_chars && tpl[exact_match_chars] != uri[exact_match_chars])
    return false;
  if (strncmp(tpl, uri, exact_match_chars) != 0) return false;
  return asterisk || len <= exact_match_chars + 1;
}

namespace castle_shim {

struct Hdr {
  std::string name, value;
};

/// What a request produced: the status line, the headers in the order the
/// handler set them, and every byte it sent (send + all the chunks).
struct Response {
  int status = 200;
  std::string status_line = "200 OK";
  std::string type = HTTPD_TYPE_TEXT;   //!< httpd's own default
  std::vector<Hdr> hdrs;
  std::string body;
};

/// One request in flight: the body the caller supplied and how much of the
/// declared Content-Length is still owed.
struct ReqCtx {
  std::string body;
  size_t pos = 0;
  size_t remaining = 0;
  Response resp;
};

struct Route {
  std::string uri;
  httpd_method_t method;
  esp_err_t (*handler)(httpd_req_t *);
  void *user_ctx;
};

struct Server {
  uint16_t port = 0;
  httpd_config_t cfg{};
  std::vector<Route> routes;
};

inline std::vector<Server *> &servers() {
  static std::vector<Server *> v;
  return v;
}

inline Server *server_on(uint16_t port) {
  for (Server *s : servers())
    if (s->port == port) return s;
  return nullptr;
}

inline ReqCtx *ctx_of(httpd_req_t *r) { return static_cast<ReqCtx *>(r->aux); }

}  // namespace castle_shim

// ── lifecycle ───────────────────────────────────────────────────────────
inline esp_err_t httpd_start(httpd_handle_t *handle, const httpd_config_t *cfg) {
  if (handle == nullptr || cfg == nullptr) return ESP_ERR_INVALID_ARG;
  if (castle_shim::server_on(cfg->server_port) != nullptr) return ESP_FAIL;
  auto *s = new castle_shim::Server();
  s->port = cfg->server_port;
  s->cfg = *cfg;
  castle_shim::servers().push_back(s);
  *handle = static_cast<httpd_handle_t>(s);
  return ESP_OK;
}

namespace castle_shim {

/// httpd_find_uri_handler: first match wins, the method decides between a
/// hit and a 405, and the search stops at max_uri_handlers — which is why
/// sd_web.h's comment about the last three routes vanishing is testable.
inline const Route *find_route(const Server *s, const char *path, size_t len,
                               int method, int *err) {
  if (err != nullptr) *err = 404;
  const size_t cap = s->cfg.max_uri_handlers;
  for (size_t i = 0; i < s->routes.size() && i < cap; i++) {
    const Route &r = s->routes[i];
    const bool hit = s->cfg.uri_match_fn != nullptr
                         ? s->cfg.uri_match_fn(r.uri.c_str(), path, len)
                         : (strlen(r.uri.c_str()) == len &&
                            strncmp(r.uri.c_str(), path, len) == 0);
    if (!hit) continue;
    if (r.method == method || r.method == HTTP_ANY) {
      if (err != nullptr) *err = 0;
      return &r;
    }
    if (err != nullptr) *err = 405;
  }
  return nullptr;
}

}  // namespace castle_shim

inline esp_err_t httpd_register_uri_handler(httpd_handle_t handle,
                                            const httpd_uri_t *u) {
  if (handle == nullptr || u == nullptr) return ESP_ERR_INVALID_ARG;
  auto *s = static_cast<castle_shim::Server *>(handle);
  if (castle_shim::find_route(s, u->uri, strlen(u->uri), u->method, nullptr) != nullptr)
    return ESP_ERR_HTTPD_HANDLER_EXISTS;
  if (s->routes.size() >= s->cfg.max_uri_handlers) return ESP_ERR_HTTPD_HANDLERS_FULL;
  s->routes.push_back({u->uri, u->method, u->handler, u->user_ctx});
  return ESP_OK;
}

// ── response ────────────────────────────────────────────────────────────
inline esp_err_t httpd_resp_set_status(httpd_req_t *r, const char *status) {
  castle_shim::Response &resp = castle_shim::ctx_of(r)->resp;
  resp.status_line = status;
  resp.status = atoi(status);
  return ESP_OK;
}

inline esp_err_t httpd_resp_set_type(httpd_req_t *r, const char *type) {
  castle_shim::ctx_of(r)->resp.type = type;
  return ESP_OK;
}

inline esp_err_t httpd_resp_set_hdr(httpd_req_t *r, const char *field,
                                    const char *value) {
  castle_shim::ctx_of(r)->resp.hdrs.push_back({field, value});
  return ESP_OK;
}

inline esp_err_t httpd_resp_send(httpd_req_t *r, const char *buf, ssize_t len) {
  if (buf == nullptr) return ESP_ERR_INVALID_ARG;
  castle_shim::ctx_of(r)->resp.body.append(
      buf, len == HTTPD_RESP_USE_STRLEN ? strlen(buf) : (size_t) len);
  return ESP_OK;
}

inline esp_err_t httpd_resp_send_chunk(httpd_req_t *r, const char *buf, ssize_t len) {
  if (buf == nullptr) return ESP_OK;   // the terminating zero-length chunk
  return httpd_resp_send(r, buf, len);
}

// ── request body ────────────────────────────────────────────────────────
inline int httpd_req_recv(httpd_req_t *r, char *buf, size_t buf_len) {
  if (buf == nullptr) return HTTPD_SOCK_ERR_INVALID;
  castle_shim::ReqCtx *c = castle_shim::ctx_of(r);
  if (c->remaining < buf_len) buf_len = c->remaining;
  if (buf_len == 0) return 0;
  const size_t avail = c->body.size() - c->pos;
  // A body that stopped arriving: the socket's recv_wait_timeout expires
  // and httpd_recv hands the handler HTTPD_SOCK_ERR_TIMEOUT, which is how
  // write_body's "short write" leg and h_ota's abort leg are reached.
  if (avail == 0) return HTTPD_SOCK_ERR_TIMEOUT;
  const size_t n = avail < buf_len ? avail : buf_len;
  memcpy(buf, c->body.data() + c->pos, n);
  c->pos += n;
  c->remaining -= n;
  return (int) n;
}

// ── query strings — IDF 5.5.5 httpd_parse.c ─────────────────────────────
inline esp_err_t httpd_req_get_url_query_str(httpd_req_t *r, char *buf, size_t buf_len) {
  if (r == nullptr || buf == nullptr) return ESP_ERR_INVALID_ARG;
  const char *q = strchr(r->uri, '?');
  if (q == nullptr) return ESP_ERR_NOT_FOUND;
  q++;
  const size_t data_len = strlen(q);
  const size_t copy_len = data_len < buf_len - 1 ? data_len : buf_len - 1;
  if (copy_len < data_len) return ESP_ERR_HTTPD_RESULT_TRUNC;
  memcpy(buf, q, copy_len);
  buf[copy_len] = '\0';
  return ESP_OK;
}

inline esp_err_t httpd_query_key_value(const char *qry_str, const char *key,
                                       char *val, size_t val_size) {
  if (qry_str == nullptr || key == nullptr || val == nullptr)
    return ESP_ERR_INVALID_ARG;
  const char *qry_ptr = qry_str;
  while (strlen(qry_ptr)) {
    // The '=' is looked for FIRST, so a pair with no '=' of its own eats
    // the next pair's: "?junk&v=50" finds no v. Faithfully strange.
    const char *val_ptr = strchr(qry_ptr, '=');
    if (val_ptr == nullptr) break;
    const size_t offset = (size_t) (val_ptr - qry_ptr);
    if (offset != strlen(key) || strncasecmp(qry_ptr, key, offset) != 0) {
      qry_ptr = strchr(val_ptr, '&');
      if (qry_ptr == nullptr) break;
      qry_ptr++;
      continue;
    }
    ++val_ptr;
    qry_ptr = strchr(val_ptr, '&');
    if (qry_ptr == nullptr) qry_ptr = val_ptr + strlen(val_ptr);
    const size_t val_len = (size_t) (qry_ptr - val_ptr);
    const size_t copy_len = val_len < val_size - 1 ? val_len : val_size - 1;
    if (copy_len < val_len) return ESP_ERR_HTTPD_RESULT_TRUNC;
    memcpy(val, val_ptr, copy_len);
    val[copy_len] = '\0';
    return ESP_OK;
  }
  return ESP_ERR_NOT_FOUND;
}

namespace castle_shim {

/// httpd_resp_send_err's table (IDF 5.5.5 httpd_txrx.c) — the verdicts no
/// handler of ours ever sees, because the server answers them itself.
inline Response idf_error(int status) {
  Response r;
  r.status = status;
  r.type = HTTPD_TYPE_TEXT;
  switch (status) {
    case 404:
      r.status_line = "404 Not Found";
      r.body = "Nothing matches the given URI";
      break;
    case 405:
      r.status_line = "405 Method Not Allowed";
      r.body = "Specified method is invalid for this resource";
      break;
    case 414:
      r.status_line = "414 URI Too Long";
      r.body = "URI is too long";
      break;
    default:
      r.status_line = "500 Internal Server Error";
      r.body = "Server has encountered an unexpected error";
  }
  return r;
}

/// One request, start to finish: what the socket layer would have done,
/// then the handler, then whatever it wrote.
inline Response dispatch(uint16_t port, int method, const std::string &uri,
                         size_t declared_len, const std::string &body) {
  Server *s = server_on(port);
  if (s == nullptr) return idf_error(500);
  if (uri.size() > HTTPD_MAX_URI_LEN) return idf_error(414);
  const size_t path_len = uri.find('?') == std::string::npos
                              ? uri.size()
                              : uri.find('?');
  int err = 0;
  const Route *route = find_route(s, uri.c_str(), path_len, method, &err);
  if (route == nullptr) return idf_error(err);

  ReqCtx ctx;
  ctx.body = body;
  ctx.remaining = declared_len;
  httpd_req_t req{};
  req.handle = static_cast<httpd_handle_t>(s);
  req.method = method;
  req.uri = uri.c_str();
  req.content_len = declared_len;
  req.aux = &ctx;
  req.user_ctx = route->user_ctx;
  route->handler(&req);
  return ctx.resp;
}

}  // namespace castle_shim
