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
#include <string_view>

namespace castle_web {

// ── pending action, handed from httpd task to the main loop ─────────────
enum class ActionType {
  NONE = 0, PLAY = 1, SCENE = 2, STOP = 3, VOLUME = 4, LIGHT = 5,
  PIRCFG = 6, RESTART = 7, SHOW = 8,   // arg "1" starts the playlist, "0" stops
  BLACKOUT = 9,                        // #25: everything off, NOW
};
struct Action {
  ActionType type{ActionType::NONE};
  std::string arg;
};
inline std::mutex g_mu;
inline Action g_pending{};

inline void set_pending(ActionType type, std::string arg) {
  std::scoped_lock lk(g_mu);
  g_pending = {type, std::move(arg)};
}
/// Called by the YAML interval on the main loop. Returns NONE most of the time.
inline Action take_pending() {
  std::scoped_lock lk(g_mu);
  Action a = g_pending;
  g_pending = {ActionType::NONE, ""};
  return a;
}

// ── state mirrored FROM the main loop, readable by handlers ─────────────
inline std::atomic g_volume{70};
inline std::atomic g_pir_armed{true};
inline std::atomic g_pir_cooldown{60};
inline std::atomic g_show_on{false};   // is the playlist running
inline std::mutex g_state_mu;
inline std::string g_scene;        // current scene id, "" until one runs
inline std::string g_track;        // current audio track, "" when idle
inline std::string g_pir_scene;    // what motion triggers
// #29: scene audio files the boot manifest check could not find on the
// card, comma-separated; empty = all present (the overwhelmingly normal
// case). Set once at boot by the generated manifest_check script.
inline std::string g_missing;

inline void set_missing(std::string_view csv) {
  std::scoped_lock lk(g_state_mu);
  g_missing = csv;
}

inline void mirror_show_state(std::string_view scene, std::string_view track,
                              std::string_view pir_scene) {
  std::scoped_lock lk(g_state_mu);
  g_scene = scene;
  g_track = track;
  g_pir_scene = pir_scene;
}

// ── the audio clock (v5.52, sound-true since v5.55) ─────────────────────
// The speaker media player knows whether its pipeline is running but not
// how far in it is, so the main loop keeps a clock of its own. The pipeline
// reports PLAYING about half a second before the first sample reaches the
// amplifier (a second of file is buffered, then decoded), and a clock started
// there led the sound by that much — every light frame aligned to it fired
// early. So a play or scene command ARMS the clock, and it starts on the
// first tick the speaker itself is running (its I2S task is draining PCM:
// audible within one DMA buffer). Only if that never comes does the clock
// fall back to counting from the pipeline's own start, as 5.52 did.
// /api/status reports playing and position_ms; a browser follows them
// instead of guessing from the moment it pressed a button.
inline std::atomic g_playing{false};
inline std::atomic g_position_ms{0LL};
inline long long g_audio_started_us = 0;   // main loop only
inline bool g_audio_was_playing = false;   // main loop only
inline bool g_clock_armed = false;         // main loop only: sound not yet heard

/// One call per mirror tick: `playing` is the pipeline's state, `sounding`
/// the speaker's. Returns true on the tick playback ENDED on its own
/// (playing -> idle), so the caller can clear a raw track the way scene_stop
/// clears an authored one.
inline bool mirror_audio(bool playing, bool sounding, long long now_us) {
  if (playing && !g_audio_was_playing) {
    g_audio_started_us = now_us;
    g_clock_armed = true;
  }
  if (g_clock_armed && playing && sounding) {
    g_audio_started_us = now_us;
    g_clock_armed = false;
  }
  const bool ended = !playing && g_audio_was_playing;
  g_audio_was_playing = playing;
  g_playing.store(playing);
  g_position_ms.store(playing && !g_clock_armed ? (now_us - g_audio_started_us) / 1000 : 0);
  return ended;
}

/// A play or scene command: the clock is re-armed even when the pipeline
/// never went idle between the old track and the new one, and reads 0
/// until the speaker is heard from again.
inline void restart_audio_clock(long long now_us) {
  g_audio_started_us = now_us;
  g_audio_was_playing = true;
  g_clock_armed = true;
  g_playing.store(true);
  g_position_ms.store(0);
}

// /api/light?c= — "RRGGBB" | "white" | "bars" | "chase" | "ends" | "show" |
// "off" (the three named patterns are the bench effects in gen_rig), "<zone>:"
// in front to drive ONE strip (the desk's channel test: which data line is
// dead) and "@<1..100>" behind for brightness. Shape only; lights_override
// knows the real zone ids. The emulator mirrors this byte for byte
// (castle_emu_http.light_spec_ok).
inline bool light_spec_ok(const std::string &c) {
  const auto colon = c.find(':');
  const std::string zone = colon == std::string::npos ? "" : c.substr(0, colon);
  std::string spec = colon == std::string::npos ? c : c.substr(colon + 1);
  if (const auto at = spec.find('@'); at != std::string::npos) {
    const std::string pct = spec.substr(at + 1);
    if (const bool digits = !pct.empty() && pct.size() <= 3 &&
            pct.find_first_not_of("0123456789") == std::string::npos;
        !digits || atoi(pct.c_str()) < 1 || atoi(pct.c_str()) > 100)
      return false;
    spec.resize(at);
  }
  if (colon != std::string::npos &&
      (zone.empty() || zone.size() > 16 ||
       zone.find_first_not_of("abcdefghijklmnopqrstuvwxyz"
                              "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") != std::string::npos))
    return false;
  const bool hex6 = spec.size() == 6 &&
      spec.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos;
  return hex6 || spec == "white" || spec == "show" || spec == "off" ||
         spec == "bars" || spec == "chase" || spec == "ends";
}

}  // namespace castle_web
