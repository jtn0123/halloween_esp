#pragma once
// The show, read from the card: one generic scene runner instead of twelve
// compiled scripts.
//
// WHAT THIS REPLACES. Every scene in scenes.yaml used to become an ESPHome
// script — publish current_scene, one lambda of base-state assignments, a
// volume call, `sfx`, a wait for the speaker, then a delay-and-lambda per cue
// and a final delay to the scene's length, chopped into `cont_<id>_N`
// continuations because ESPHome stops a script by walking its action chain
// recursively. Twelve of those measured 744 LambdaActions, 573 DelayActions
// and 85 ScriptExecuteActions on the S3 build's link map: ~23 KB of static
// internal RAM, and 744 compiled lambdas in a flash budget that is the thing
// actually binding this board (1,311,643 of 1,835,008 bytes at v5.66).
//
// None of it had to be in the image. v5.63 already built the mechanism for
// imported songs — a cue file beside the track, read into PSRAM when it
// starts and freed when it stops, 33 bytes of statics (castle_cues.h). This
// finishes the job for the built-in scenes:
//
//   /sd/scenes/show.man   the manifest: id, audio token, volume, length,
//                         whether it loops  (tools/scene_manifest.py)
//   /sd/scenes/<id>.cue   that scene's base look and every cue
//                         (tools/cue_file.py — the SAME format a raw song's
//                         .cue uses, so there is one reader)
//
// A scene edit is now a publish, not a build and an OTA.
//
// CARD I/O. A scene start already read the card (the audio stream begins
// there), so the manifest lookup and the cue load ride along in that same
// window. Nothing here touches the card once the show is running: the SPI
// traffic starves the RMT refill and two or three pixels flick to a wrong
// colour (docs/ISSUE-ring-flicker.md). One 16-byte header read plus at most
// twelve 96-byte entry reads, then the file is closed.
//
// AND IT IS TWO STEPS, NOT ONE (v5.68, measured on the board). v5.67 did the
// manifest read AND the cue-file load in `begin()`, before `sfx` was called,
// and scene start went from ~650 ms request-to-audible to ~855 ms — the ring
// put scene_start→sound at 604 ms where v5.66 had 400. The audio pipeline
// needs ~400 ms to spin up no matter what, so the card work belongs BESIDE
// that wait, not in front of it. So `begin()` reads only what the play call
// needs (one manifest row: the audio token, the volume, the length) and
// `load_cues()` opens the cue file afterwards, once `sfx` has been asked for.
// Nothing is lost by the delay: the timeline does not start until the speaker
// is heard, and the give-up window is measured from the REQUEST instant
// `begin()` recorded, not from whenever the load happened to finish.
//
// RAM. The statics below are the running scene's id, its audio token, three
// numbers and two flags — 100 bytes, once, for the whole show — plus, since
// v5.77, the evening's list (a vector of up to twelve ids, on the heap) and
// where the playlist is in it. The cues live in PSRAM and are freed on stop.
//
// MAIN LOOP ONLY, like castle_cues: find()/begin()/stop() are called from
// the scene_run script and the mailbox interval, never from the httpd task.
// The one exception is read-only and deliberate: ids_csv() runs once at boot
// (castle_sd_common.yaml) to seed castle_web::set_scene_ids, before start().

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <sys/stat.h>
#include <vector>

#include "castle_cues.h"

namespace castle_scenes {

inline constexpr uint8_t kVersion = 1;
/// SCENE_LIMIT, the device's copy. tools/check_loc.py refuses a thirteenth
/// scene in scenes.yaml and core/src/studio_check.rs refuses it at splice
/// time; this is what refuses it if it reaches the card anyway.
inline constexpr uint8_t kMaxScenes = 12;
inline constexpr size_t kIdMax = 40;
inline constexpr size_t kAudioMax = 48;
/// Where the card keeps the show. The audio has streamed from here since the
/// all-in-flash build was retired; the manifest and the cue files joined it.
inline constexpr const char *kDir = "/sd/scenes";
/// Header flag bit 0 (v5.77): each row's last byte is its place in the
/// evening playlist, 1-based, 0 for "not in the evening". tools/
/// scene_manifest.py MARKS_EVENING. Firmware before v5.77 never read it.
inline constexpr uint16_t kMarksEvening = 0x0001;

#pragma pack(push, 1)
struct Header {
  char magic[4];
  uint8_t version, count;
  uint16_t flags;         // kMarksEvening; written as zero before v5.77
  uint32_t entry_size, reserved;
};
struct Entry {
  char id[kIdMax];        // NUL-terminated
  char audio[kAudioMax];  // the `sfx` track token, no extension
  uint32_t duration_ms;
  uint16_t volume_pct;
  uint8_t loops;
  uint8_t evening;        // place in the evening, 1-based; 0 = not in it
};
#pragma pack(pop)
static_assert(sizeof(Header) == 16 && sizeof(Entry) == 96,
              "the manifest layout is tools/scene_manifest.py's, byte for byte");

// ── the running scene ───────────────────────────────────────────────────
inline char g_id[kIdMax]{};        // "" when no scene is running
inline char g_audio[kAudioMax]{};
inline uint32_t g_len_ms = 0;
inline uint16_t g_volume_pct = 0;
inline bool g_loops = false;
inline bool g_armed = false;       // the manifest had this scene; the show runs
inline long long g_clock_us = 0;   // when the timeline started (speaker heard)
inline long long g_request_us = 0; // when the start was ASKED for (see load_cues)

inline const char *running() { return g_id; }
inline const char *audio() { return g_audio; }
inline bool armed() { return g_armed; }
inline bool loops() { return g_loops; }
inline uint32_t length_ms() { return g_len_ms; }
inline float volume() { return (float) g_volume_pct / 100.0f; }
/// When this start was asked for — what the cue file's sound-wait is counted
/// from, so loading it a few milliseconds later does not lengthen the wait.
inline long long requested_us() { return g_request_us; }

/// The manifest file's path. A separate function so the host harness can
/// point the whole reader at a temporary directory.
inline std::string manifest_path(const char *dir = kDir) {
  return std::string(dir) + "/show.man";
}

/// `id`'s own cue file, beside its audio. Not castle_cues::path_for, which
/// refuses a name with a slash on purpose (a raw song's cue file lives in
/// the card root with the song, and "scenes/09_x.mp3" must not resolve).
inline std::string cue_path(const char *id, const char *dir = kDir) {
  return std::string(dir) + "/" + id + ".cue";
}

/// Open the manifest and read its header. Non-null and `count` filled in
/// only when the four checks castle_cues::load also makes all pass: magic,
/// version, the entry size this build compiled, and an exact file length.
/// The caller closes. `flags`, when asked for, is the header's flag word.
inline FILE *open_manifest(uint8_t &count, const char *dir = kDir,
                           uint16_t *flags = nullptr) {
  count = 0;
  FILE *f = fopen(manifest_path(dir).c_str(), "rb");
  if (f == nullptr) return nullptr;
  Header h{};
  struct stat st{};
  const bool ok =
      fread(&h, 1, sizeof(h), f) == sizeof(h) && memcmp(h.magic, "CSMF", 4) == 0 &&
      h.version == kVersion && h.entry_size == sizeof(Entry) && h.count <= kMaxScenes &&
      stat(manifest_path(dir).c_str(), &st) == 0 &&
      (size_t) st.st_size == sizeof(Header) + (size_t) h.count * sizeof(Entry);
  if (!ok) {
    fclose(f);
    return nullptr;
  }
  count = h.count;
  if (flags != nullptr) *flags = h.flags;
  return f;
}

/// Scene `id`'s entry, or false. One 96-byte stack buffer, no heap, at most
/// twelve reads — and the ids are compared whole, so "vigi" is not "vigil".
inline bool find(const char *id, Entry &out, const char *dir = kDir) {
  uint8_t count = 0;
  FILE *f = open_manifest(count, dir);
  if (f == nullptr) return false;
  Entry e{};
  bool found = false;
  for (uint8_t i = 0; i < count && !found; i++) {
    if (fread(&e, 1, sizeof(e), f) != sizeof(e)) break;
    // A truncated id field is a corrupt file, not a short name: refuse it
    // rather than comparing against something with no terminator.
    if (e.id[kIdMax - 1] != '\0' || e.audio[kAudioMax - 1] != '\0') break;
    if (strcmp(e.id, id) == 0) {
      out = e;
      found = true;
    }
  }
  fclose(f);
  return found;
}

/// The manifest's ids, comma-joined — what /api/status's `scenes` says and
/// what /api/scene checks a request against. Empty when there is no manifest
/// this build can read; the caller then falls back to the ids it was
/// compiled with (generated/fallback_scenes.h).
inline std::string ids_csv(const char *dir = kDir) {
  uint8_t count = 0;
  FILE *f = open_manifest(count, dir);
  if (f == nullptr) return "";
  std::string out;
  Entry e{};
  for (uint8_t i = 0; i < count; i++) {
    if (fread(&e, 1, sizeof(e), f) != sizeof(e)) break;
    if (e.id[kIdMax - 1] != '\0') break;
    if (!out.empty()) out += ',';
    out += e.id;
  }
  fclose(f);
  return out;
}

// ── the evening show (v5.77) ────────────────────────────────────────────
//
// "Start show" plays these, in this order, one per pass of the generated
// `show_playlist` script, wrapping at the end. Until v5.77 that list was
// four compiled actions per scene id — so the image named every scene of
// the yard's show, imported songs included, and a castle sold with a card
// that does not carry them walked into two "missing" scenes every round.
// It is read off the manifest now, beside the ids, by seed_scene_ids.
//
// MAIN LOOP ONLY: set at boot and after a publish by seed_scene_ids, read
// by show_playlist. Statics: one vector and one index.
inline std::vector<std::string> g_evening;
inline size_t g_evening_at = 0;

/// The evening's ids, comma-joined, in play order. A manifest that marks
/// the evening (kMarksEvening) answers its placed rows, by place; one from
/// before v5.77 answers every row except `skip` — the PIR's scene, which
/// is what the compiled list was (every scene but the motion one). Empty
/// when there is no manifest this build can read.
inline std::string evening_csv(const char *skip, const char *dir = kDir) {
  uint8_t count = 0;
  uint16_t flags = 0;
  FILE *f = open_manifest(count, dir, &flags);
  if (f == nullptr) return "";
  const bool marked = (flags & kMarksEvening) != 0;
  std::vector<std::string> placed(kMaxScenes);
  std::string out;
  Entry e{};
  for (uint8_t i = 0; i < count; i++) {
    if (fread(&e, 1, sizeof(e), f) != sizeof(e)) break;
    if (e.id[kIdMax - 1] != '\0') break;
    if (!marked) {
      if (skip != nullptr && strcmp(e.id, skip) == 0) continue;
      if (!out.empty()) out += ',';
      out += e.id;
    } else if (e.evening >= 1 && e.evening <= kMaxScenes && placed[e.evening - 1].empty()) {
      placed[e.evening - 1] = e.id;  // a second claim on one place is ignored
    }
  }
  fclose(f);
  for (const auto &id : placed) {
    if (id.empty()) continue;
    if (!out.empty()) out += ',';
    out += id;
  }
  return out;
}

/// Replace the evening with `csv`'s ids. The next pass of a running show
/// continues from the same place, wrapped to the new length.
inline void set_evening(const std::string &csv) {
  g_evening.clear();
  for (size_t at = 0; at <= csv.size();) {
    const size_t end = std::min(csv.find(',', at), csv.size());
    if (end > at) g_evening.emplace_back(csv, at, end - at);
    at = end + 1;
  }
  if (!g_evening.empty()) g_evening_at %= g_evening.size();
  else g_evening_at = 0;
}

inline size_t evening_count() { return g_evening.size(); }
/// "Start show" starts at the top of the evening, not where it last stopped.
inline void evening_rewind() { g_evening_at = 0; }
/// The scene this pass plays, and move on; "" when the evening is empty.
inline std::string evening_next() {
  if (g_evening.empty()) return "";
  const std::string id = g_evening[g_evening_at % g_evening.size()];
  g_evening_at = (g_evening_at + 1) % g_evening.size();
  return id;
}

/// #29, generic: every file the show will ask for, stat()ed once after mount.
/// Was a generated stat() per scene; the file list is the manifest now, so
/// the list is read rather than compiled. Missing AUDIO is named as the file
/// (`03_seance.mp3`) and a missing cue file as `<id>.cue`, because the two
/// are different faults — one is silence, the other is a scene that plays
/// its sound under a static base look.
inline std::string missing_csv(const char *dir = kDir) {
  uint8_t count = 0;
  FILE *f = open_manifest(count, dir);
  if (f == nullptr) return "show.man";
  std::string out;
  Entry e{};
  struct stat st{};
  for (uint8_t i = 0; i < count; i++) {
    if (fread(&e, 1, sizeof(e), f) != sizeof(e)) break;
    if (e.id[kIdMax - 1] != '\0' || e.audio[kAudioMax - 1] != '\0') break;
    const std::string mp3 = std::string(dir) + "/" + e.audio + ".mp3";
    if (stat(mp3.c_str(), &st) != 0) {
      if (!out.empty()) out += ',';
      out += e.audio;
      out += ".mp3";
    }
    if (stat(cue_path(e.id, dir).c_str(), &st) != 0) {
      if (!out.empty()) out += ',';
      out += e.id;
      out += ".cue";
    }
  }
  fclose(f);
  return out;
}

/// Forget the running scene and give its cues back. `scene_stop` calls this,
/// and so does begin() before it loads the next one.
inline void stop() {
  g_id[0] = g_audio[0] = '\0';
  g_len_ms = 0;
  g_volume_pct = 0;
  g_loops = false;
  g_armed = false;
  g_clock_us = 0;
  g_request_us = 0;
  castle_cues::unload();
}

/// Step one of a start: look scene `id` up and take its numbers. ONE manifest
/// row and nothing else — no cue file, no PSRAM — because the next thing the
/// caller does is ask for the audio, and everything in front of that call is
/// added to the silence the operator hears (see the v5.68 note above).
///
/// False means the card could not tell us what this scene is — no manifest,
/// or no entry with that id — and the caller shows the compiled-in fallback
/// look instead (castle_scenes.yaml).
inline bool begin(const char *id, long long now_us, const char *dir = kDir) {
  // A looping scene re-executes itself with the SAME id every length_ms, and
  // its row cannot have changed underneath it — nothing writes the card while
  // the show is running. So the re-run reuses the row already in hand and the
  // card is not opened at all. This is ONE row, the running scene's own 100
  // bytes; the manifest itself is never resident.
  const bool again = g_armed && strcmp(g_id, id) == 0;
  Entry e{};
  if (!again && !find(id, e, dir)) {
    stop();
    return false;
  }
  castle_cues::unload();   // the previous pass's cues, if there were any
  if (!again) {
    snprintf(g_id, sizeof(g_id), "%s", e.id);
    snprintf(g_audio, sizeof(g_audio), "%s", e.audio);
    g_len_ms = e.duration_ms;
    g_volume_pct = e.volume_pct > 100 ? 100 : e.volume_pct;
    g_loops = e.loops != 0;
  }
  g_armed = true;
  g_clock_us = 0;
  g_request_us = now_us;
  return true;
}

/// Step two: the running scene's cue file, read AFTER `sfx` has been asked
/// for, while the audio pipeline spins up.
///
/// False is the partial failure /api/status names: the scene EXISTS and its
/// audio will play, but its cue file is missing or unreadable, so the
/// timeline is a static base look and nothing after it. Armed from
/// `g_request_us`, not from now, so the ≤1500 ms wait for a speaker that
/// never comes is the same length whenever this ran.
inline bool load_cues(const char *dir = kDir) {
  if (!g_armed) return false;
  return castle_cues::load_at(cue_path(g_id, dir), g_request_us);
}

/// The instant the timeline starts: after the ≤1500 ms wait for the speaker,
/// exactly where a generated script's first `delay:` began.
inline void start_clock(long long now_us) { g_clock_us = now_us; }

/// Has the scene reached its authored length? The `delay:` that used to
/// close a script, as a condition — one `wait_until` in place of 573
/// DelayActions.
inline bool finished(long long now_us) {
  if (!g_armed) return true;
  return (now_us - g_clock_us) / 1000 >= (long long) g_len_ms;
}

}  // namespace castle_scenes
