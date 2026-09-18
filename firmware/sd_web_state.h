// The desk-facing state seam of the SD web layer, split from sd_web.h
// (500-line cap). Two one-way mailboxes and nothing else:
//
//   pending action  — httpd task writes, main loop drains (one slot; the
//                     device queues actions, the desk toasts "queued")
//   mirrored state  — main loop writes, handlers read without touching a
//                     single ESPHome object from the wrong task
//
// The event ring that used to be a third section here came out on the same
// cap in v5.67: it is a HISTORY rather than a mailbox, and it is sd_web_ring.h
// now, included below because set_pending counts an evicted light frame into
// one of its counters.
//
// No httpd types in here on purpose: this header is the part a unit test or
// a different transport could reuse unchanged.
#pragma once

#include <algorithm>
#include <atomic>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

#include "sd_web_ring.h"

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
// A flashed image MUST reboot. The one slot can be replaced by whatever the
// next request queues, so RESTART keeps a latch of its own and is drained
// ahead of the slot — a web OTA that reported {"flashed":true} can no longer
// be talked out of rebooting by a status poll or a light frame.
inline std::atomic g_restart_pending{false};

inline void set_pending(ActionType type, std::string arg) {
  if (type == ActionType::RESTART) {
    g_restart_pending.store(true);
    return;
  }
  std::scoped_lock lk(g_mu);
  // A synced import streams light frames at ~4 Hz, faster than the 200 ms
  // drain: a LIGHT must never evict the stop, volume or play a hand pressed
  // in the same tick. LIGHT over LIGHT still wins (a colour-picker drag
  // lands its last colour); everything else takes the slot as before.
  //
  // Either way a frame is lost: LIGHT over LIGHT drops the one underneath,
  // and LIGHT behind another kind drops ITSELF. Both are evictions and both
  // are counted — until v5.60 only the first was, so a page could never
  // make applied + evicted add up to what it had sent (A6/C8).
  if (type == ActionType::LIGHT && g_pending.type != ActionType::NONE) {
    g_light_evicted.fetch_add(1);
    if (g_pending.type != ActionType::LIGHT) return;
  }
  g_pending = {type, std::move(arg)};
}
/// Called by the YAML interval on the main loop. Returns NONE most of the time.
inline Action take_pending() {
  if (g_restart_pending.exchange(false)) return {ActionType::RESTART, ""};
  std::scoped_lock lk(g_mu);
  Action a = g_pending;
  g_pending = {ActionType::NONE, ""};
  return a;
}

/// Main loop, on the tick an action is executed: the ring line for it, and
/// the applied counter for a LIGHT (which is far too frequent to record).
inline void record_action(ActionType type, const std::string &arg, long long now_us) {
  switch (type) {
    case ActionType::PLAY: record_event(EventKind::PLAY, arg, now_us); break;
    case ActionType::SCENE: record_event(EventKind::SCENE, arg, now_us); break;
    case ActionType::STOP: record_event(EventKind::STOP, arg, now_us); break;
    case ActionType::VOLUME: record_event(EventKind::VOLUME, arg, now_us); break;
    case ActionType::SHOW: record_event(EventKind::SHOW, arg, now_us); break;
    case ActionType::BLACKOUT: record_event(EventKind::BLACKOUT, arg, now_us); break;
    case ActionType::RESTART: record_event(EventKind::RESTART, arg, now_us); break;
    case ActionType::LIGHT: g_light_applied.fetch_add(1); break;
    default: break;   // NONE and PIRCFG are not show events
  }
}

// ── state mirrored FROM the main loop, readable by handlers ─────────────
inline std::atomic g_volume{70};
// FALSE at boot since v5.69: the AM312 is not wired on the S3 carrier, so
// the switch is ALWAYS_OFF (tools/gen_esphome.py) and this is the same
// answer /api/status gives before the first 200 ms mirror has run.
inline std::atomic g_pir_armed{false};
inline std::atomic g_pir_cooldown{60};
inline std::atomic g_show_on{false};   // is the playlist running
inline std::mutex g_state_mu;

// ── /api/status as ONE instant (v5.60, A7) ──────────────────────────────
// h_status used to copy the strings under g_state_mu, drop the lock, and
// only then load show_on / playing / position_ms / volume as independent
// atomics. The 200 ms mirror in castle_sd_common.yaml lands in between often
// enough to matter: a poll on the tick a track ends carried the track's name
// beside playing:false, or playing:true beside an empty track, and a browser
// following the castle skipped or stuck. Everything the reply prints now
// lives in ONE struct, written by the main loop under one lock at one
// instant and copied out by the handler the same way.
struct Status {
  std::string scene;       // current scene id, "" until one runs
  std::string track;       // current audio track, "" when idle
  std::string pir_scene;   // what motion triggers
  // #29: what the show asked the card for and did not get, comma-separated;
  // empty = all present (the overwhelmingly normal case). Seeded at boot by
  // manifest_check, which walks the card's own scene manifest, and added to
  // at run time by the scene runner when a scene or a cue file is not there
  // (v5.67, note_missing below) — and REMOVED from again when that same start
  // succeeds later, so a republished file heals the list without a reboot
  // (v5.68, heal_missing).
  std::string missing;
  // B1: the scene ids the castle can actually start, comma-joined. The
  // card's manifest since v5.67 — so adding a scene is a publish, not an
  // OTA — falling back to the ids this build was compiled with when there
  // is no manifest to read (generated/fallback_scenes.h).
  std::string scenes;
  int volume{70};
  bool show_on{false};
  bool playing{false};
  long long position_ms{0};
  unsigned light_applied{0};
  unsigned light_evicted{0};
  unsigned cues{0};
  bool pir_armed{false};   // v5.69: the PIR boots disarmed (not wired)
  int pir_cooldown{60};
  // L6 (v5.62): the radio, which nothing reported at all. A castle that
  // answers slowly at the end of the garden and a castle with a failing
  // power supply look identical from the desk without this number.
  int rssi{0};   // dBm, 0 = not associated / not known yet
};
inline Status g_status{};   // guarded by g_state_mu

inline Status status_snapshot() {
  std::scoped_lock lk(g_state_mu);
  return g_status;
}

inline void set_missing(std::string_view csv) {
  std::scoped_lock lk(g_state_mu);
  g_status.missing = csv;
}

/// v5.67: ADD one name, if it is not already listed. The boot check speaks
/// for the card as it was at mount; a scene that could not be started an hour
/// later is a second fact about the same card, and overwriting the first with
/// it would lose the list a publish is meant to fix. Bounded: at most
/// kMissingMax names, because /api/status is polled and this string is in it.
inline constexpr size_t kMissingMax = 16;

inline void note_missing(const std::string &name) {
  std::scoped_lock lk(g_state_mu);
  if (name.empty()) return;
  const std::string &have = g_status.missing;
  // Whole-element match: "x.cue" must not be found inside "yx.cue".
  for (size_t at = 0; at <= have.size();) {
    const size_t end = std::min(have.find(',', at), have.size());
    if (have.compare(at, end - at, name) == 0) return;
    at = end + 1;
  }
  if (std::count(have.begin(), have.end(), ',') + 1 >= (long) kMissingMax) return;
  if (!g_status.missing.empty()) g_status.missing += ',';
  g_status.missing += name;
}

/// v5.68: REMOVE one name, because the list has to heal. A cue file that was
/// missing and has since been published is not missing any more, and until
/// this existed the only way to say so was a reboot — so the operator who
/// fixed the card still read `missing:"storm.cue"` from the castle that was
/// by then playing storm's full show. Called on the success path of a start,
/// which is the one moment the castle has first-hand evidence about a name.
///
/// Whole-element removal, and the separator goes with the element rather than
/// being left behind: a stray ',' would make an empty name that nothing
/// matches and that /api/status would print.
inline void heal_missing(const std::string &name) {
  std::scoped_lock lk(g_state_mu);
  if (name.empty() || g_status.missing.empty()) return;
  std::string out;
  const std::string &have = g_status.missing;
  for (size_t at = 0; at <= have.size();) {
    const size_t end = std::min(have.find(',', at), have.size());
    if (end > at && have.compare(at, end - at, name) != 0) {
      if (!out.empty()) out += ',';
      out.append(have, at, end - at);
    }
    at = end + 1;
  }
  g_status.missing = out;
}

// Scene ids the firmware actually has, read off the card's own manifest —
// /sd/scenes/show.man, by `seed_scene_ids` (firmware/castle_scenes.yaml).
// It came from pir_scene's compiled select options until v5.67 and from the
// card ONCE AT BOOT until v5.69; a publish re-seeds it now (J1, the bell
// below). Empty means "not seeded yet": /api/scene used to
// accept ANYTHING then, so a client retrying from boot could be told its
// typo was queued. An empty list now answers "not ready" instead.
//
// v5.60 (A5): guarded by g_state_mu. The httpd task reads this list on
// every /api/scene and /api/status while the boot lambda writes it, and a
// bare std::vector read across two tasks is a data race TSAN names — the
// boot order was also wrong (start() before set_scene_ids), so the very
// first polls raced a vector that was being assigned underneath them.
inline std::vector<std::string> g_scene_ids;   // guarded by g_state_mu

/// J1 (grade report 2026-09-17 pm): "a publish landed a new show.man".
///
/// The upload worker (sd_web_upload.h) runs on a task of its own and must
/// not re-read the manifest itself: that is card I/O, and the list it would
/// assign is read by the httpd task on every /api/scene. So it rings this
/// bell and the 200 ms main-loop interval (castle_sd_common.yaml) does both
/// — exchange(false), so one publish is one re-seed. Before v5.69 the list
/// was seeded once at boot and `sd_sync scenes` printed "reboot to re-read
/// it", which made a new scene 404 on the castle that already had it.
inline std::atomic g_scenes_dirty{false};

inline void set_scene_ids(std::vector<std::string> ids) {
  std::scoped_lock lk(g_state_mu);
  g_scene_ids = std::move(ids);
  g_status.scenes.clear();
  for (const auto &id : g_scene_ids) {
    if (!g_status.scenes.empty()) g_status.scenes += ",";
    g_status.scenes += id;
  }
}

/// The show's FIRST scene — the one the castle opens with at boot. Read
/// under the same lock and from the same list, so "which scene is first" has
/// one answer and it is the card's (v5.67: the list is seeded from
/// /sd/scenes/show.man, castle_sd_common.yaml). Empty before the list is
/// seeded, which is the boot order refusing to start a scene that has not
/// been established rather than guessing a name.
inline std::string first_scene_id() {
  std::scoped_lock lk(g_state_mu);
  return g_scene_ids.empty() ? std::string() : g_scene_ids.front();
}

/// What /api/scene and /api/pir must know about an id, read under the lock:
/// 0 = the list is not seeded yet, 1 = known, -1 = no such scene.
inline int scene_id_state(const std::string &s) {
  std::scoped_lock lk(g_state_mu);
  if (g_scene_ids.empty()) return 0;
  return std::find(g_scene_ids.begin(), g_scene_ids.end(), s) == g_scene_ids.end()
             ? -1
             : 1;
}

// A2: the first /api/status this boot has answered. The bootloader holds a
// freshly-OTA'd image in PENDING_VERIFY, and the only confirmation used to
// be `api: on_client_connected` — with no Home Assistant on the network a
// web-OTA'd image was never confirmed and rolled back on the next power
// cycle. The main loop watches this flag and confirms once (flash_mode.h).
inline std::atomic g_status_served{false};

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
// How long an ARMED clock is allowed to report "starting" before a silent
// pipeline counts as a track that ended. The same grace the scene scripts
// give the speaker (gen_esphome's SOUND_WAIT_MS): a decoder that takes its
// time must not read as "finished" on the very next 200 ms tick, because a
// browser following the castle would clear the track and skip the song.
inline constexpr long long kSoundWaitUs = 1500000;

/// One call per mirror tick: `playing` is the pipeline's state, `sounding`
/// the speaker's, `track` what the loop believes is on the speaker. Returns
/// true on the tick playback ENDED on its own (playing -> idle), so the
/// caller can clear a raw track the way scene_stop clears an authored one.
///
/// L10 (v5.62): the two lines this writes used to carry an empty arg, so a
/// ring read the next morning said the speaker started and stopped without
/// ever saying WHAT — useless for the one question worth asking of it ("did
/// the track that killed it always kill it"). SOUND now carries the track
/// name and SILENT the milliseconds that actually played.
inline bool mirror_audio(bool playing, bool sounding, long long now_us,
                         std::string_view track = {}) {
  if (playing && !g_audio_was_playing) {
    g_audio_started_us = now_us;
    g_clock_armed = true;
  }
  if (g_clock_armed && playing && sounding) {
    g_audio_started_us = now_us;
    g_clock_armed = false;
    record_event(EventKind::SOUND, track, now_us);   // the amplifier has it
  }
  // Armed but the pipeline is not up yet: hold "starting" for the grace
  // rather than reporting an end the sound never had.
  if (!playing && g_clock_armed && g_audio_was_playing &&
      now_us - g_audio_started_us < kSoundWaitUs) {
    g_playing.store(true);
    g_position_ms.store(0);
    return false;
  }
  const bool ended = !playing && g_audio_was_playing;
  if (ended) {
    // How much sound there was. An armed clock never started, so the honest
    // answer for a track that died before the amplifier heard it is 0.
    const long long played = g_clock_armed ? 0 : (now_us - g_audio_started_us) / 1000;
    record_event(EventKind::SILENT, std::to_string(played), now_us);
  }
  if (!playing) g_clock_armed = false;
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

/// The ONE publish point: called last in the 200 ms mirror tick, after the
/// atomics above have been stored, it copies the whole of /api/status into
/// g_status under one lock. Lives down here rather than beside the other
/// mirrored state because it has to see the audio clock's atomics — the
/// audio fields and the track name MUST move together or a poll lands
/// between them (A7).
inline void mirror_show_state(std::string_view scene, std::string_view track,
                              std::string_view pir_scene) {
  std::scoped_lock lk(g_state_mu);
  g_status.scene = scene;
  g_status.track = track;
  g_status.pir_scene = pir_scene;
  g_status.volume = g_volume.load();
  g_status.show_on = g_show_on.load();
  g_status.playing = g_playing.load();
  g_status.position_ms = g_position_ms.load();
  g_status.light_applied = g_light_applied.load();
  g_status.light_evicted = g_light_evicted.load();
  g_status.cues = g_cues.load();
  g_status.pir_armed = g_pir_armed.load();
  g_status.pir_cooldown = g_pir_cooldown.load();
  g_status.rssi = g_rssi.load();
}

// /api/light?c= — "RRGGBB" | "white" | "bars" | "chase" | "ends" | "show" |
// "off" (the three named patterns are the bench effects in gen_rig), "<zone>:"
// in front to drive ONE strip (the desk's channel test: which data line is
// dead) and "@<1..100>" behind for brightness. Shape only; lights_override
// knows the real zone ids. The emulator mirrors this byte for byte
// (castle_emu_http.light_spec_ok).
/// True only for the texture hand-back command itself — "show", "<zone>:show"
/// or "show@50". A substring test read a zone named "show" or "shower"
/// ("show:off", "shower:off") as the hand-back and left the scene script
/// firing underneath a bench colour.
inline bool light_spec_is_show(const std::string &c) {
  const auto colon = c.find(':');
  std::string spec = colon == std::string::npos ? c : c.substr(colon + 1);
  if (const auto at = spec.find('@'); at != std::string::npos) spec.resize(at);
  return spec == "show";
}

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
