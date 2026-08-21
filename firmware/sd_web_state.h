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

}  // namespace castle_web
