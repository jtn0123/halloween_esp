// The desk-facing state seam of the SD web layer, split from sd_web.h
// (500-line cap). Two one-way mailboxes and nothing else:
//
//   pending action  — httpd task writes, main loop drains (one slot; the
//                     device queues actions, the desk toasts "queued")
//   mirrored state  — main loop writes, handlers read without touching a
//                     single ESPHome object from the wrong task
//
// No httpd types in here on purpose: this header is the part a unit test or
// a different transport could reuse unchanged.
#pragma once

#include <atomic>
#include <mutex>
#include <string>

namespace castle_web {

// ── pending action, handed from httpd task to the main loop ─────────────
enum ActionType {
  NONE = 0, PLAY = 1, SCENE = 2, STOP = 3, VOLUME = 4, LIGHT = 5,
  PIRCFG = 6, RESTART = 7, SHOW = 8,   // arg "1" starts the playlist, "0" stops
  BLACKOUT = 9,                        // #25: everything off, NOW
};
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

// ── state mirrored FROM the main loop, readable by handlers ─────────────
inline std::atomic<int> g_volume{70};
inline std::atomic<bool> g_pir_armed{true};
inline std::atomic<int> g_pir_cooldown{60};
inline std::atomic<bool> g_show_on{false};   // is the playlist running
inline std::mutex g_state_mu;
inline std::string g_scene;        // current scene id, "" until one runs
inline std::string g_track;        // current audio track, "" when idle
inline std::string g_pir_scene;    // what motion triggers
// #29: scene audio files the boot manifest check could not find on the
// card, comma-separated; empty = all present (the overwhelmingly normal
// case). Set once at boot by the generated manifest_check script.
inline std::string g_missing;

inline void set_missing(const std::string &csv) {
  std::lock_guard<std::mutex> lk(g_state_mu);
  g_missing = csv;
}

inline void mirror_show_state(const std::string &scene, const std::string &track,
                              const std::string &pir_scene) {
  std::lock_guard<std::mutex> lk(g_state_mu);
  g_scene = scene;
  g_track = track;
  g_pir_scene = pir_scene;
}

// /api/light?c= — "RRGGBB" | "white" | "show" | "off", optionally "<zone>:"
// in front to drive ONE strip (the desk's channel test: which data line is
// dead) and "@<1..100>" behind for brightness. Shape only; lights_override
// knows the real zone ids. The emulator mirrors this byte for byte
// (castle_emu_http.light_spec_ok).
inline bool light_spec_ok(const std::string &c) {
  const auto colon = c.find(':');
  const std::string zone = colon == std::string::npos ? "" : c.substr(0, colon);
  std::string spec = colon == std::string::npos ? c : c.substr(colon + 1);
  const auto at = spec.find('@');
  if (at != std::string::npos) {
    const std::string pct = spec.substr(at + 1);
    const bool digits = !pct.empty() && pct.size() <= 3 &&
        pct.find_first_not_of("0123456789") == std::string::npos;
    if (!digits || atoi(pct.c_str()) < 1 || atoi(pct.c_str()) > 100) return false;
    spec.resize(at);
  }
  if (colon != std::string::npos &&
      (zone.empty() || zone.size() > 16 ||
       zone.find_first_not_of("abcdefghijklmnopqrstuvwxyz"
                              "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") != std::string::npos))
    return false;
  const bool hex6 = spec.size() == 6 &&
      spec.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos;
  return hex6 || spec == "white" || spec == "show" || spec == "off";
}

}  // namespace castle_web
