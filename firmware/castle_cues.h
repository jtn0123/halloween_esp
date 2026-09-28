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
// Two versions of the file exist, and this build reads both (v5.71). A
// version-1 file means exactly what it meant to a v1 reader, byte for byte:
// set and strike, one flash per zone, masks 0-3. Version 2 adds three things
// the lab's choreography asked for, and tools/cue_file.py writes a 2 ONLY
// when a record uses one of them — so every show that uses none is still a
// v1 file a v5.70 castle can play:
//   - bit 7 of a strike's zone mask: land on layer 1, the ornament
//     (castle_layers.h), which ADDS to layer 0 instead of replacing it;
//   - strike masks 4-7: left, right, top, bottom half, and 8-15: arc0..arc7,
//     one patch of the loop a spin steps round (castle_effects.h);
//   - op 3, a LOOK: per zone, overlay/palette/centre role (each with a keep
//     value) and the overlay clock — a rate in milli-turns per second and a
//     head phase — so a chase can be locked to the tempo mid-song.
// A v5.70 (v1-only) castle REFUSES a version-2 file whole, exactly as it
// refuses any version it does not know: the song plays, dark, and
// /api/status names the file `missing`. Nothing half-applies.
//
// RAM: the statics below are a few dozen bytes, plus castle_layers.h's zone
// state. Everything else is PSRAM, freed the moment the song stops.
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

#include "castle_heard.h"
#include "castle_layers.h"

namespace castle_cues {

/// The newest version this build reads; it reads every version from 1 up.
inline constexpr uint8_t kVersion = 2;
inline constexpr uint8_t kZones = 3;
inline constexpr uint8_t kOpSet = 1;
inline constexpr uint8_t kOpStrike = 2;
inline constexpr uint8_t kOpLook = 3;   // v2
inline constexpr uint8_t kLevelKeep = 255;
/// v2: a strike's zone mask with this bit lands on layer 1, the ornament.
inline constexpr uint8_t kLayerBit = 0x80;
/// v2 look fields' "leave it alone" values.
inline constexpr uint8_t kByteKeep = 255;      // overlay, palette
inline constexpr int8_t kCenterKeep = -128;    // centre role (-1 = none)
inline constexpr uint16_t kWordKeep = 65535;   // rate: keep; head: continue
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
    // v2. rate: milli-turns per second of the chase head / meteor drip, 0 =
    // the legacy clock. head: milli-turns 0..999 to put the head AT, or
    // kWordKeep to continue from wherever it is.
    struct { uint8_t overlay, palette; int8_t center; uint8_t pad;
             uint16_t rate, head, pad2; } look;
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
  /// Not an ESPHome global (castle_layers.h says why), so every caller gets
  /// the one array without naming it.
  castle::ZoneExtra *x = castle::g_zone_x;
};

inline uint8_t *g_blob = nullptr;        // Zone[kZones] then Record[count], PSRAM
inline uint32_t g_count = 0;
inline uint8_t g_version = 0;            // of the loaded file
inline uint32_t g_next = 0;
inline long long g_armed_us = 0;         // when load() ran
inline long long g_started_us = 0;       // when the speaker was first running
inline bool g_running = false;           // the clock has started
/// v5.72: how long tick() waits, once the speaker runs, for the heard clock
/// (castle_heard.h) to report this song's first sample before it gives up
/// and walks the show on the stopwatch the way v5.71 did. A decoder hands
/// over its first samples in well under this; only a speaker whose output
/// callback never fires reaches it.
inline constexpr long long kHeardWaitMs = 1000;
/// v5.72, what the heard clock says about the stopwatch it replaced, for
/// the song loaded last (they stay after it ends, until the next load):
/// `lead` is how far ahead of the sound v5.71's clock was at this song's
/// first heard sample, `drift` the most it wandered from that lead later —
/// a starved decoder shows up here. -1 until a song has been heard.
inline long long g_sync_lead_ms = -1;
inline long long g_sync_drift_ms = -1;

inline bool active() { return g_blob != nullptr; }
/// Cues in the loaded show, 0 when there is none.
inline uint32_t count() { return g_count; }

inline void unload() {
  if (g_blob != nullptr) heap_caps_free(g_blob);
  g_blob = nullptr;
  g_count = g_next = 0;
  g_version = 0;
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

/// Whether every record of a v2 body names an op this build knows. A v1
/// body is not checked: a v1 reader drew any op but a set as a strike, and
/// a v1 file must keep meaning what it meant.
inline bool ops_known(const uint8_t *blob, uint32_t count) {
  const uint8_t *records = blob + sizeof(Zone) * kZones;
  for (uint32_t i = 0; i < count; i++) {
    const uint8_t op = records[sizeof(Record) * (size_t) i + 4] & 15;
    if (op != kOpSet && op != kOpStrike && op != kOpLook) return false;
  }
  return true;
}

/// Read the cue file AT `path` into PSRAM. False (and nothing loaded) when
/// there is no file, it is not a version this build reads, its length
/// disagrees with its header, a v2 record names an unknown op, or the PSRAM
/// is not there: the song then plays exactly as it did before this file
/// existed.
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
            memcmp(h.magic, "CCUE", 4) == 0 && h.version >= 1 && h.version <= kVersion &&
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
    ok = ok && (h.version < 2 || ops_known(blob, h.count));
  }
  fclose(f);
  if (!ok) {
    if (blob != nullptr) heap_caps_free(blob);
    return false;
  }
  g_blob = blob;
  g_count = h.count;
  g_version = h.version;
  g_armed_us = now_us;
  castle_heard::arm();
  g_sync_lead_ms = g_sync_drift_ms = -1;
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
    // A new show owns no ornament, no tempo clock and no strike history.
    px.x[i] = castle::ZoneExtra{};
  }
}

/// The loaded show's base state.
inline void apply_base(const Pixels &px) {
  if (!active()) return;
  apply_zones(px, g_blob);
}

/// A v2 look record on zone `i`, at render-clock `clock_s` (castle_layers.h).
inline void apply_look(const Pixels &px, const Record &r, int i, float clock_s) {
  if (r.look.overlay != kByteKeep) px.overlay[i] = r.look.overlay;
  if (r.look.palette != kByteKeep) px.palette[i] = r.look.palette;
  if (r.look.center != kCenterKeep) px.center[i] = r.look.center;
  const float rate = r.look.rate == kWordKeep ? -1.0f : r.look.rate / 1000.0f;
  const float head = r.look.head == kWordKeep ? -1.0f : (r.look.head % 1000) / 1000.0f;
  castle::set_motion(px.x[i], px.overlay[i], px.phase[i], i, clock_s, rate, head);
}

/// One strike on zone `i`'s layer 0 (the zone globals, written the way the
/// generated cue lambdas wrote them) or layer 1 (the ornament).
inline void apply_strike(const Pixels &px, const Record &r, int i, int mode, bool layer1) {
  castle::ZoneExtra &x = px.x[i];
  // Rate-aware soften: whether this strike is part of a train is decided now,
  // on the song clock, and kept with the layer it lands on.
  const bool train = castle::note_strike(x, r.t_ms);
  float *flash = layer1 ? &x.orn_flash : &px.flash[i];
  float *target = layer1 ? &x.orn_target : &px.flash_target[i];
  float *rise = layer1 ? &x.orn_rise : &px.flash_rise[i];
  const float amt = r.strike.intensity / 1000.0f;
  if (r.strike.attack_ms > 0) {
    // Swell to the peak: the render loop climbs by _rise per 16 ms frame.
    *target = amt;
    *rise = amt * 16.0f / r.strike.attack_ms;
  } else {
    *flash = amt;
    *target = 0.0f;
  }
  const float decay = r.strike.decay / 10000.0f;
  float *col = layer1 ? x.orn_col : &px.flash_col[i * 4];
  for (int k = 0; k < 4; k++) col[k] = r.strike.col[k] / 100.0f;
  if (layer1) {
    x.orn_decay = decay;
    x.orn_mode = mode;
    x.orn_epoch = (x.orn_epoch + 1) % 1000;
    x.train1 = train;
    return;
  }
  px.flash_decay[i] = decay;
  px.flash_mode[i] = mode;
  px.flash_epoch[i] = (px.flash_epoch[i] + 1) % 1000;
  x.train0 = train;
}

/// One record. `clock_s` is the render clock (millis() / 1000, what the
/// strips draw with) at the record's own time — only a look reads it.
inline void apply(const Pixels &px, const Record &r, float clock_s) {
  const bool v2 = g_version >= 2;
  const int op = r.op & 15;
  // A v1 reader drew a mask it did not know (4-15) as "all", and had no
  // layer bit: a v1 file keeps that meaning here.
  int mode = r.op >> 4;
  if (!v2 && mode > 3) mode = 0;
  const bool layer1 = v2 && (r.mask & kLayerBit) != 0;
  for (int i = 0; i < kZones; i++) {
    if (!(r.mask >> i & 1)) continue;
    if (op == kOpSet) {
      px.effect[i] = r.set.effect;
      if (r.set.level != kLevelKeep) px.level[i] = clamp_pct(r.set.level) / 100.0f;
    } else if (v2 && op == kOpLook) {
      apply_look(px, r, i, clock_s);
    } else {
      apply_strike(px, r, i, mode, layer1);
    }
  }
}

/// What the stopwatch said against what was heard, once a tick.
inline void note_sync(long long stopwatch_ms, long long heard_ms) {
  const long long off = stopwatch_ms - heard_ms;
  if (g_sync_lead_ms < 0) {
    g_sync_lead_ms = off < 0 ? 0 : off;
    g_sync_drift_ms = 0;
    return;
  }
  const long long wander = off > g_sync_lead_ms ? off - g_sync_lead_ms : g_sync_lead_ms - off;
  if (wander > g_sync_drift_ms) g_sync_drift_ms = wander;
}

/// Once a render frame. Since v5.72 the song's time is the HEARD clock's
/// (castle_heard.h): nothing fires until the first sample of this song has
/// reached the amplifier, and after that a record fires when the listener
/// reaches its time — not when a stopwatch started beside the speaker says
/// so. The stopwatch still starts on the first tick the speaker runs (or
/// kSoundWaitUs after load, as a script's wait_until gives up); it is what
/// the heard clock is measured against, and the fall-back if the speaker
/// never reports a sample. Every record whose time has come is applied —
/// all of them, so a loop that stalled for 300 ms catches up instead of
/// drifting for the rest of the song. Returns how many it applied.
inline int tick(const Pixels &px, bool sounding, long long now_us) {
  if (!active()) return 0;
  if (!g_running) {
    if (!sounding && now_us - g_armed_us < kSoundWaitUs) return 0;
    g_running = true;
    g_started_us = now_us;
  }
  const long long stopwatch_ms = (now_us - g_started_us) / 1000;
  const long long heard_ms = castle_heard::position_ms(now_us);
  long long at_ms = stopwatch_ms;
  if (heard_ms >= 0) {
    note_sync(stopwatch_ms, heard_ms);
    at_ms = heard_ms;
  } else if (stopwatch_ms < kHeardWaitMs) {
    return 0;  // running, but nothing of this song has been heard yet
  }
  // ESPHome's millis() on the IDF is esp_timer_get_time() / 1000, as a
  // uint32 — the clock the render lambdas pass render_zone.
  const auto now_ms = (uint32_t) (now_us / 1000);
  const uint8_t *records = g_blob + sizeof(Zone) * kZones;
  int applied = 0;
  Record r;
  while (g_next < g_count) {
    memcpy(&r, records + sizeof(Record) * (size_t) g_next, sizeof(r));
    if ((long long) r.t_ms > at_ms) break;
    // The render clock at the record's OWN time, not this tick's: a look
    // anchors its tempo clock where the song put it, stall or no stall.
    const uint32_t rec_ms = now_ms - (uint32_t) (at_ms - (long long) r.t_ms);
    apply(px, r, rec_ms / 1000.0f);
    g_next++;
    applied++;
  }
  return applied;
}

}  // namespace castle_cues
