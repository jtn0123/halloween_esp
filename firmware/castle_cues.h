#pragma once
// A song's light show, loaded from the card instead of compiled into the image.
//
// Every scene in scenes.yaml becomes an ESPHome script, and a script lives
// in internal RAM: about 9 KB a scene on a chip that once had 20 bytes to
// spare. That is why the show stops at twelve scenes and why a long song
// keeps 200 of its hits (PULSE_CAP) — and neither limit was ever about the
// show. The card has 31 GB free and the PSRAM 2 MB; only the place the cues
// were KEPT was scarce.
//
// So a track played off the card (`/api/play`) may have a cue file beside
// it, `/sd/<track>.cue` — in the card root with the song, because that is
// where PUT /api/files already writes and DELETE already reaches, so the
// format needed no route of its own. tools/render_cues.py writes it in the
// layout tools/cue_file.py documents. load() reads it into PSRAM in one go — no
// card I/O while the song runs, which is the traffic that starves the RMT
// refill (docs/ISSUE-ring-flicker.md) — and tick() walks it on the speaker's
// clock, writing the very zone globals a generated script's lambdas write.
// Same numbers, same render loop; no script, no ceiling.
//
// RAM: the statics below are 33 bytes. Everything else is PSRAM, freed the
// moment the song stops.
//
// Main loop only: load(), unload() and tick() are called from ESPHome's
// loop (castle_sd_common.yaml), never from the httpd task. What /api/status
// says about it is castle_web::g_cues, which that loop stores count() into.

#include <esp_heap_caps.h>

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
/// 512 KB of PSRAM: an hour of a busy song. cue_file.MAX_RECORDS agrees.
inline constexpr uint32_t kMaxRecords = 32768;
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

/// Read `track`'s cue file into PSRAM. False (and nothing loaded) when there
/// is no file, it is not a version this build reads, its length disagrees
/// with its header, or the PSRAM is not there: the song then plays exactly
/// as it did before this file existed.
inline bool load(const std::string &track, long long now_us, const char *dir = "/sd") {
  unload();
  const std::string path = path_for(track, dir);
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
    // One byte past the body must NOT be there: a longer file is a different
    // format wearing this header.
    uint8_t extra;
    ok = blob != nullptr && fread(blob, 1, body, f) == body && fread(&extra, 1, 1, f) == 0;
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

/// The scene's base state — the first lambda of a generated script.
inline void apply_base(const Pixels &px) {
  if (!active()) return;
  Zone z;
  for (int i = 0; i < kZones; i++) {
    memcpy(&z, g_blob + sizeof(Zone) * i, sizeof(z));
    px.effect[i] = z.effect;
    px.flash[i] = 0.0f;
    px.flash_target[i] = 0.0f;
    px.flash_decay[i] = 0.9f;
    px.level[i] = z.level / 100.0f;
    px.center[i] = z.center;
    px.overlay[i] = z.overlay;
    px.palette[i] = z.palette;
    px.phase[i] = z.phase / 100.0f;
    px.flash_mode[i] = 0;
    for (int k = 0; k < 4; k++) px.flash_col[i * 4 + k] = 1.0f;
  }
}

/// One record, written the way gen_esphome.py's cue lambdas write it.
inline void apply(const Pixels &px, const Record &r) {
  for (int i = 0; i < kZones; i++) {
    if (!(r.mask >> i & 1)) continue;
    if ((r.op & 15) == kOpSet) {
      px.effect[i] = r.set.effect;
      if (r.set.level != kLevelKeep) px.level[i] = r.set.level / 100.0f;
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
