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
// v5.75 adds the owner's clock and limits beside them — a timezone, a volume
// cap and quiet hours (castle_owner.h holds them and says what each does).
// Loaded and saved here with the rest, so NVS has one reader and one writer.
//
// NVS is thread-safe in ESP-IDF, so these are written straight from the
// httpd task: nothing here touches an ESPHome object.

#pragma once

#include <nvs.h>

#include "castle_owner.h"

#include <array>
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
  // v5.75. Each is checked again on the way in: NVS outlives firmware, and
  // a value this build would refuse at the door is not trusted from flash.
  char tz[castle_tz::kTzMax + 1] = {0};
  len = sizeof(tz);
  if (nvs_get_str(h, "tz", tz, &len) == ESP_OK && tz_ok(tz)) set_tz(tz);
  uint32_t vm = 0;
  if (nvs_get_u32(h, "vol_max", &vm) == ESP_OK && vm >= 1 && vm <= 100)
    g_vol_max.store((int) vm);
  uint32_t q = 0;
  if (nvs_get_u32(h, "quiet", &q) == ESP_OK && q < 1440u * 1440u &&
      q / 1440 != q % 1440)
    g_quiet.store((int) q);
  nvs_close(h);
}

/// One /api/settings request's worth, in one commit. A field that was not
/// in the request is not touched; `quiet` off and `tz` "" ERASE their key,
/// so a castle that turned them off reads back like one that never had them.
struct Settings {
  std::string boot_play, tz, vol_max, quiet;   // as validated; "" = not given
  int vol_max_pct = 100, quiet_packed = kQuietOff;
};

inline bool prefs_save(const Settings &s) {
  nvs_handle_t h = 0;
  if (nvs_open(kPrefsNs, NVS_READWRITE, &h) != ESP_OK) return false;
  esp_err_t err = ESP_OK;
  if (!s.boot_play.empty()) err = nvs_set_u32(h, "boot_play", s.boot_play == "1" ? 1 : 0);
  if (err == ESP_OK && !s.tz.empty()) err = nvs_set_str(h, "tz", s.tz.c_str());
  if (err == ESP_OK && !s.vol_max.empty())
    err = nvs_set_u32(h, "vol_max", (uint32_t) s.vol_max_pct);
  if (err == ESP_OK && !s.quiet.empty()) {
    err = s.quiet_packed < 0 ? nvs_erase_key(h, "quiet")
                             : nvs_set_u32(h, "quiet", (uint32_t) s.quiet_packed);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
  }
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

/// Every setting as one JSON object: /api/settings answers with it, so a
/// client sees what the castle now holds rather than an echo of its request.
inline std::string settings_json() {
  std::array<char, 64> buf{};
  snprintf(buf.data(), buf.size(), R"({"boot_play":%s,"vol_max":%d,"tz":")",
           g_boot_play.load() ? "true" : "false", g_vol_max.load());
  std::string out = buf.data();
  out += json_escape(tz_copy());
  out += R"(","quiet":")";
  out += quiet_str();
  out += R"("})";
  return out;
}

// ── POST /api/settings?boot_play=1|0&tz=<POSIX>&vol_max=1..100&quiet=… ──
// Any subset, at least one. Every value is checked before ANY is saved, so a
// request with one bad field changes nothing. quiet= is HH:MM-HH:MM or off.
inline esp_err_t h_settings(httpd_req_t *req) {
  if (!key_ok(req)) return reply_locked(req);
  if (esp_err_t sent; !query_ok(req, {"boot_play", "tz", "vol_max", "quiet"}, sent))
    return sent;
  Settings s;
  s.boot_play = query_param(req, "boot_play");
  s.tz = query_param(req, "tz");
  s.vol_max = query_param(req, "vol_max");
  s.quiet = query_param(req, "quiet");
  if (s.boot_play.empty() && s.tz.empty() && s.vol_max.empty() && s.quiet.empty())
    return reply_err(req, "400 Bad Request", "need boot_play=, tz=, vol_max= or quiet=");
  if (!pir_armed_ok(s.boot_play)) return reply_err(req, "400 Bad Request", "bad boot_play");
  if (!s.tz.empty() && !tz_ok(s.tz)) return reply_err(req, "400 Bad Request", "bad tz");
  if (!s.vol_max.empty() && !vol_max_ok(s.vol_max, s.vol_max_pct))
    return reply_err(req, "400 Bad Request", "bad vol_max");
  if (!s.quiet.empty() && !quiet_ok(s.quiet, s.quiet_packed))
    return reply_err(req, "400 Bad Request", "bad quiet");
  if (!prefs_save(s))
    return reply_err(req, "500 Internal Server Error", "settings not saved");
  if (!s.boot_play.empty()) g_boot_play.store(s.boot_play == "1");
  if (!s.tz.empty()) set_tz(s.tz);
  if (!s.vol_max.empty()) g_vol_max.store(s.vol_max_pct);
  if (!s.quiet.empty()) g_quiet.store(s.quiet_packed);
  return reply_json(req, settings_json());
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
// the key, boot_play, the zone, cap and quiet hours (v5.75), the saved Wi-Fi network (the buyer build comes back
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
  owner_reset();
  const esp_err_t sent = reply_json(req, R"({"resetting":true})");
  if (g_on_factory_reset != nullptr) g_on_factory_reset();
  return sent;
}

}  // namespace castle_web
