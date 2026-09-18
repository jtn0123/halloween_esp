#pragma once
// A song's light show, loaded from the card instead of compiled into the image.
//
// Every scene in scenes.yaml used to become an ESPHome script, and a script
// lives in internal RAM and in flash: twelve of them came to ~23 KB of
// statics and 744 compiled lambdas. That is why a long song kept only 200 of
// its hits (PULSE_CAP) — and the limit was never about the show. The card has
// 31 GB free and the PSRAM 2 MB; only the place the cues were KEPT was scarce.
//
// So a track played off the card (`/api/play`) may have a cue file beside
// it, `/sd/<track>.cue` — in the card root with the song, because that is
// where PUT /api/files already writes and DELETE already reaches, so the
// format needed no route of its own. tools/render_cues.py writes it in the
// layout tools/cue_file.py documents. load() reads it into PSRAM in
// kReadChunk pieces — no card I/O while the song runs, which is the traffic
// that starves the RMT refill (docs/ISSUE-ring-flicker.md) — and tick()
// walks it on the speaker's clock, writing the very zone globals a generated
// script's lambdas wrote. Same numbers, same render loop; no script.
//
// Since v5.67 the built-in SCENES come through here too: castle_scenes.h
// reads /sd/scenes/show.man for what a scene IS and hands load_at() the
// scene's own /sd/scenes/<id>.cue. One format, one reader, two kinds of
// caller — which is why the path and the track name are separate doors below.
//
// RAM: the statics below are 33 bytes. Everything else is PSRAM, freed the
// moment the song stops.
//
// Main loop only: load(), unload() and tick() are called from ESPHome's
// loop (castle_sd_common.yaml), never from the httpd task. What /api/status
// says about it is castle_web::g_cues, which that loop stores count() into.

#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace castle_cues {

inline constexpr uint8_t kVersion = 1;
inline constexpr uint8_t kZones = 3;
inline constexpr uint8_t kOpSet = 1;
inline constexpr uint8_t kOpStrike = 2;
inline constexpr uint8_t kLevelKeep = 255;
/// A level is whole percent and 255 means "leave it alone", so every other
/// value is at most 100. tools/cue_file.py clamps on the way in; this is the
/// device refusing to be told otherwise (grade report 2026-09-17 J7): an
/// unclamped 254 became a level of 2.54, which the render loop multiplies
/// straight into the pixels.
inline uint8_t clamp_pct(uint8_t v) { return v > 100 ? 100 : v; }
/// 512 KB of PSRAM: an hour of a busy song. cue_file.MAX_RECORDS agrees.
inline constexpr uint32_t kMaxRecords = 32768;
/// How much of the body one fread takes before the loop task gets a tick
/// back (grade report 2026-09-17 J7). It used to be the whole thing — up to
/// 512 KB off SPI, in one call, on the main loop, at the exact moment a song
/// starts, which is the moment docs/ISSUE-scene-start-audio.md is about. A
/// scene's file is a few KB and pays one yield; only a pathological import
/// pays sixteen.
inline constexpr size_t kReadChunk = 32768;
/// A script holds its first cue this long for the speaker and then runs
/// anyway (gen_show.SOUND_WAIT_MS). So does this.
inline constexpr long long kSoundWaitUs = 1500000;

#pragma pack(push, 1)
struct Header {
  char magic[4];
  uint8_t version, zones;
  uint16_t pad;
  uint32_t count, duration_ms;
};
struct Zone {
  uint8_t effect, level;
  int8_t center;
  uint8_t overlay, palette, pad;
  uint16_t phase;
};
struct Record {
  uint32_t t_ms;
  uint8_t op;      // low nibble: kOpSet / kOpStrike; high nibble: strike mask mode
  uint8_t mask;    // bit per zone
  union {
    struct { uint8_t effect, level; } set;
    struct { uint16_t intensity, decay, attack_ms; uint8_t col[4]; } strike;
    uint8_t raw[10];
  };
};
#pragma pack(pop)
static_assert(sizeof(Header) == 16 && sizeof(Zone) == 8 && sizeof(Record) == 16,
              "the cue file layout is tools/cue_file.py's, byte for byte");

/// The zone globals of castle.yaml, by address: ESPHome ids exist only inside
/// a YAML lambda, so the lambda names them once and hands them over.
struct Pixels {
  int *effect;
  float *flash, *flash_col, *flash_decay, *flash_target, *flash_rise, *level;
  int *center, *overlay, *palette;
  float *phase;
  int *flash_mode, *flash_epoch;
};

inline uint8_t *g_blob = nullptr;        // Zone[kZones] then Record[count], PSRAM
inline uint32_t g_count = 0;
inline uint32_t g_next = 0;
inline long long g_armed_us = 0;         // when load() ran
inline long long g_started_us = 0;       // when the speaker was first heard
inline bool g_running = false;           // the clock has started

inline bool active() { return g_blob != nullptr; }
/// Cues in the loaded show, 0 when there is none.
inline uint32_t count() { return g_count; }

inline void unload() {
  if (g_blob != nullptr) heap_caps_free(g_blob);
  g_blob = nullptr;
  g_count = g_next = 0;
  g_running = false;
}

/// "song.mp3" -> "<dir>/song.cue". A name with a slash is a scene track
/// (scenes/09_x.mp3) or an attack; neither has a cue file.
inline std::string path_for(const std::string &track, const char *dir) {
  if (track.empty() || track.find('/') != std::string::npos ||
      track.find("..") != std::string::npos)
    return "";
  const auto dot = track.rfind('.');
  return std::string(dir) + "/" + track.substr(0, dot) + ".cue";
}

/// Read the cue file AT `path` into PSRAM. False (and nothing loaded) when
/// there is no file, it is not a version this build reads, its length
/// disagrees with its header, or the PSRAM is not there: the song then plays
/// exactly as it did before this file existed.
///
/// Takes a path rather than a track name because there are two kinds of cue
/// file now and only one of them can be derived from a track: a raw song's
/// sits in the card root beside it (load() below), and a SCENE's sits in
/// /sd/scenes/ under the scene's own id (castle_scenes::cue_path). One
/// reader, because it is one format.
inline bool load_at(const std::string &path, long long now_us) {
  unload();
  if (path.empty()) return false;
  FILE *f = fopen(path.c_str(), "rb");
  if (f == nullptr) return false;
  Header h{};
  bool ok = fread(&h, 1, sizeof(h), f) == sizeof(h) &&
            memcmp(h.magic, "CCUE", 4) == 0 && h.version == kVersion &&
            h.zones == kZones && h.count <= kMaxRecords;
  const size_t body = sizeof(Zone) * kZones + sizeof(Record) * (size_t) h.count;
  uint8_t *blob = nullptr;
  if (ok) {
    blob = static_cast<uint8_t *>(heap_caps_malloc(body, MALLOC_CAP_SPIRAM));
    ok = blob != nullptr;
    // J7: kReadChunk at a time, with a tick back to the scheduler between
    // pieces. The bytes are identical; what changes is that the loop task
    // is not gone for the length of a half-megabyte SPI transfer.
    for (size_t at = 0; ok && at < body;) {
      const size_t want = std::min(kReadChunk, body - at);
      ok = fread(blob + at, 1, want, f) == want;
      at += want;
      vTaskDelay(1);
    }
    // One byte past the body must NOT be there: a longer file is a different
    // format wearing this header.
    uint8_t extra;
    ok = ok && fread(&extra, 1, 1, f) == 0;
  }
  fclose(f);
  if (!ok) {
    if (blob != nullptr) heap_caps_free(blob);
    return false;
  }
  g_blob = blob;
  g_count = h.count;
  g_armed_us = now_us;
  return true;
}

/// Read the cue file beside the card track `track` — /api/play's door.
inline bool load(const std::string &track, long long now_us, const char *dir = "/sd") {
  const std::string path = path_for(track, dir);
  if (path.empty()) {
    unload();
    return false;
  }
  return load_at(path, now_us);
}

/// A base look from kZones packed Zone records — the first lambda of what
/// used to be a generated script. Takes the bytes rather than reading g_blob
/// so the compiled-in fallback look can share it: generated/fallback_scenes.h
/// carries the first scene's zone records verbatim, and a castle with no card
/// wears them through this very function (castle_scenes.yaml).
inline void apply_zones(const Pixels &px, const uint8_t *zones) {
  Zone z;
  for (int i = 0; i < kZones; i++) {
    memcpy(&z, zones + sizeof(Zone) * i, sizeof(z));
    px.effect[i] = z.effect;
    px.flash[i] = 0.0f;
    px.flash_target[i] = 0.0f;
    px.flash_decay[i] = 0.9f;
    px.level[i] = clamp_pct(z.level) / 100.0f;
    px.center[i] = z.center;
    px.overlay[i] = z.overlay;
    px.palette[i] = z.palette;
    px.phase[i] = z.phase / 100.0f;
    px.flash_mode[i] = 0;
    for (int k = 0; k < 4; k++) px.flash_col[i * 4 + k] = 1.0f;
  }
}

/// The loaded show's base state.
inline void apply_base(const Pixels &px) {
  if (!active()) return;
  apply_zones(px, g_blob);
}

/// One record, written the way the generated cue lambdas wrote it.
inline void apply(const Pixels &px, const Record &r) {
  for (int i = 0; i < kZones; i++) {
    if (!(r.mask >> i & 1)) continue;
    if ((r.op & 15) == kOpSet) {
      px.effect[i] = r.set.effect;
      if (r.set.level != kLevelKeep) px.level[i] = clamp_pct(r.set.level) / 100.0f;
      continue;
    }
    const float amt = r.strike.intensity / 1000.0f;
    if (r.strike.attack_ms > 0) {
      // Swell to the peak: the render loop climbs by _rise per 16 ms frame.
      px.flash_target[i] = amt;
      px.flash_rise[i] = amt * 16.0f / r.strike.attack_ms;
    } else {
      px.flash[i] = amt;
      px.flash_target[i] = 0.0f;
    }
    px.flash_decay[i] = r.strike.decay / 10000.0f;
    px.flash_mode[i] = r.op >> 4;
    px.flash_epoch[i] = (px.flash_epoch[i] + 1) % 1000;
    for (int k = 0; k < 4; k++) px.flash_col[i * 4 + k] = r.strike.col[k] / 100.0f;
  }
}

/// Once a render frame. The clock starts on the first tick the speaker runs
/// (or kSoundWaitUs after load, as a script's wait_until gives up), and every
/// record whose time has come is applied — all of them, so a loop that
/// stalled for 300 ms catches up instead of drifting for the rest of the song.
/// Returns how many it applied.
inline int tick(const Pixels &px, bool sounding, long long now_us) {
  if (!active()) return 0;
  if (!g_running) {
    if (!sounding && now_us - g_armed_us < kSoundWaitUs) return 0;
    g_running = true;
    g_started_us = now_us;
  }
  const long long at_ms = (now_us - g_started_us) / 1000;
  const uint8_t *records = g_blob + sizeof(Zone) * kZones;
  int applied = 0;
  Record r;
  while (g_next < g_count) {
    memcpy(&r, records + sizeof(Record) * (size_t) g_next, sizeof(r));
    if ((long long) r.t_ms > at_ms) break;
    apply(px, r);
    g_next++;
    applied++;
  }
  return applied;
}

}  // namespace castle_cues
