// The owner's settings, kept in NVS: the optional castle key, and whether a
// power-on boot starts the show (v5.74, docs/PRODUCTION-TODO.md §1.3, §1.6).
//
// THE KEY. Off by default — empty is exactly every build before this one:
// no route asks for anything. Once an owner sets one, the routes that CHANGE
// the castle ask for it in an `X-Castle-Key` request header — the card
// writes and deletes (PUT/DELETE /api/files|site|scenes/*), the firmware
// (PUT /api/ota), the PIR settings, these settings, and the key itself.
// Reading the castle and running the show stay open: status, health, play,
// scene, stop, volume, light, blackout. A castle on a porch is driven by
// whoever is standing at it; what a stranger on the Wi-Fi must not do is
// replace its firmware or wipe its card.
//
// A HEADER and not HTTP Basic, because both clients are code: a browser
// page's fetch() and Python's urllib each set one header in one line, and
// neither has to deal with a 401 that makes the browser pop its own login
// box (which is what `WWW-Authenticate: Basic` does). It is not a session
// and not a hash: the LAN is the trust boundary, as it always was, and the
// key is what stops the next house over or a guest's laptop.
//
// ESPHome's own OTA (port 3232) follows the key in the BUYER build, where
// `password: ""` compiles its auth path in and castle_buyer.h hands it the
// key whenever it changes. The yard's stays as it was, open. The native API
// (6053) follows nothing: its encryption key is compile-time in ESPHome.
// docs/notes/06-buyer-build.md §12.23 says so.
//
// BOOT PLAY. A power-on or crash boot starts the opening scene; v5.72 already
// keeps OTA and soft restarts silent. Now that is a setting, defaulting to the
// build's own answer (ON in the yard, OFF for a buyer — castle_sd_common.yaml
// boot_play_default) until somebody changes it.
//
// NVS is thread-safe in ESP-IDF, so these are written straight from the
// httpd task: nothing here touches an ESPHome object.

#pragma once

#include <nvs.h>

#include <atomic>
#include <cstring>
#include <mutex>
#include <string>
#include <string_view>

namespace castle_web {

inline constexpr const char *kPrefsNs = "castle_prefs";
inline constexpr size_t kKeyMax = 64;

inline std::mutex g_key_mu;
inline std::string g_key;                 // "" = no key = every route open
inline std::atomic g_boot_play{true};
/// The build's answer when NVS has none: set from the YAML before start().
inline bool g_boot_play_default = true;
inline bool g_prefs_loaded = false;
/// Bumped whenever g_key changes, so a main-loop follower (the buyer
/// build's ESPHome OTA password, castle_buyer.h) re-reads it only then.
inline std::atomic<unsigned> g_key_gen{0};

inline std::string key_copy() {
  std::scoped_lock lk(g_key_mu);
  return g_key;
}

/// 1..64 printable ASCII characters, no space: survives a header, a query
/// string and a shell without anyone having to think about quoting.
inline bool key_chars_ok(std::string_view k) {
  if (k.empty() || k.size() > kKeyMax) return false;
  for (const char c : k)
    if (c < 0x21 || c > 0x7e) return false;
  return true;
}

/// Read once, from start(): the buyer build calls start() again whenever
/// the captive portal hands port 80 back, and NVS has not changed.
inline void prefs_load() {
  if (g_prefs_loaded) return;
  g_prefs_loaded = true;
  g_boot_play.store(g_boot_play_default);
  nvs_handle_t h = 0;
  if (nvs_open(kPrefsNs, NVS_READONLY, &h) != ESP_OK) return;
  uint32_t bp = 0;
  if (nvs_get_u32(h, "boot_play", &bp) == ESP_OK) g_boot_play.store(bp != 0);
  char buf[kKeyMax + 1] = {0};
  size_t len = sizeof(buf);
  if (nvs_get_str(h, "key", buf, &len) == ESP_OK && key_chars_ok(buf)) {
    std::scoped_lock lk(g_key_mu);
    g_key = buf;
    g_key_gen.fetch_add(1);
  }
  nvs_close(h);
}

inline bool prefs_save_boot_play(bool on) {
  nvs_handle_t h = 0;
  if (nvs_open(kPrefsNs, NVS_READWRITE, &h) != ESP_OK) return false;
  esp_err_t err = nvs_set_u32(h, "boot_play", on ? 1 : 0);
  if (err == ESP_OK) err = nvs_commit(h);
  nvs_close(h);
  return err == ESP_OK;
}

/// "" erases the key, which is what "no key" is on a castle that never
/// had one — so a cleared castle and a new one read back the same.
inline bool prefs_save_key(const std::string &key) {
  nvs_handle_t h = 0;
  if (nvs_open(kPrefsNs, NVS_READWRITE, &h) != ESP_OK) return false;
  esp_err_t err = key.empty() ? nvs_erase_key(h, "key") : nvs_set_str(h, "key", key.c_str());
  if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
  if (err == ESP_OK) err = nvs_commit(h);
  nvs_close(h);
  return err == ESP_OK;
}

inline bool locked() {
  std::scoped_lock lk(g_key_mu);
  return !g_key.empty();
}

/// True when the request may change the castle: no key is set, or it
/// carries the right one. The compare runs the whole length either way.
inline bool key_ok(httpd_req_t *req) {
  std::string want;
  {
    std::scoped_lock lk(g_key_mu);
    if (g_key.empty()) return true;
    want = g_key;
  }
  const size_t n = httpd_req_get_hdr_value_len(req, "X-Castle-Key");
  if (n == 0 || n > kKeyMax) return false;
  char got[kKeyMax + 1] = {0};
  if (httpd_req_get_hdr_value_str(req, "X-Castle-Key", got, sizeof(got)) != ESP_OK)
    return false;
  if (strlen(got) != want.size()) return false;
  unsigned diff = 0;
  for (size_t i = 0; i < want.size(); i++)
    diff |= (unsigned) (got[i] ^ want[i]);
  return diff == 0;
}

inline esp_err_t reply_locked(httpd_req_t *req) {
  return reply_err(req, "401 Unauthorized", "castle key required");
}

// ── POST /api/settings?boot_play=1|0 ────────────────────────────────────
inline esp_err_t h_settings(httpd_req_t *req) {
  if (!key_ok(req)) return reply_locked(req);
  if (esp_err_t sent; !query_ok(req, {"boot_play"}, sent)) return sent;
  std::string bp = query_param(req, "boot_play");
  if (bp.empty()) return reply_err(req, "400 Bad Request", "need boot_play=");
  if (!pir_armed_ok(bp)) return reply_err(req, "400 Bad Request", "bad boot_play");
  const bool on = bp == "1";
  if (!prefs_save_boot_play(on))
    return reply_err(req, "500 Internal Server Error", "settings not saved");
  g_boot_play.store(on);
  return reply_json(req, on ? R"({"boot_play":true})" : R"({"boot_play":false})");
}

// ── POST /api/key?new=<key> | ?clear=1 ──────────────────────────────────
// Setting a key on an open castle needs nothing; changing or clearing one
// needs the current key, like every other change.
inline esp_err_t h_key(httpd_req_t *req) {
  if (!key_ok(req)) return reply_locked(req);
  if (esp_err_t sent; !query_ok(req, {"new", "clear"}, sent)) return sent;
  const std::string nk = query_param(req, "new");
  const bool clear = query_param(req, "clear") == "1";
  if (nk.empty() == !clear)
    return reply_err(req, "400 Bad Request", "need new=<key> or clear=1");
  if (!clear && !key_chars_ok(nk)) return reply_err(req, "400 Bad Request", "bad key");
  const std::string next = clear ? std::string() : nk;
  if (!prefs_save_key(next))
    return reply_err(req, "500 Internal Server Error", "settings not saved");
  {
    std::scoped_lock lk(g_key_mu);
    g_key = next;
    g_key_gen.fetch_add(1);
  }
  return reply_json(req, clear ? R"({"locked":false})" : R"({"locked":true})");
}

// ── POST /api/factory-reset?confirm=yes ─────────────────────────────────
// The castle as it left the box, without a reflash (PRODUCTION-TODO §1.4):
// the key, boot_play, the saved Wi-Fi network (the buyer build comes back
// up as its Castle-XXXX access point), ESPHome's restored states and the
// season's boot counters — the whole NVS partition. The card is untouched:
// the songs are the owner's, and a reset is about the CASTLE.
//
// The erase and the reboot are the board's: `g_on_factory_reset` is set
// from castle_sd_common.yaml and runs AFTER the reply is on the wire. The
// host harness leaves it null, so there the settings are forgotten in
// memory and the castle carries on — the reply is the contract.
inline void (*g_on_factory_reset)() = nullptr;

inline esp_err_t h_factory_reset(httpd_req_t *req) {
  if (!key_ok(req)) return reply_locked(req);
  if (esp_err_t sent; !query_ok(req, {"confirm"}, sent)) return sent;
  if (query_param(req, "confirm") != "yes")
    return reply_err(req, "400 Bad Request", "need confirm=yes");
  {
    std::scoped_lock lk(g_key_mu);
    g_key.clear();
    g_key_gen.fetch_add(1);
  }
  g_boot_play.store(g_boot_play_default);
  const esp_err_t sent = reply_json(req, R"({"resetting":true})");
  if (g_on_factory_reset != nullptr) g_on_factory_reset();
  return sent;
}

}  // namespace castle_web
