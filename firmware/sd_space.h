// How much room is left on the card — the one number /api/status reports
// that costs real card I/O to learn.
//
// f_getfree walks the whole FAT when FSINFO is stale, which can take
// SECONDS on a big card, and it happens on the httpd task. Until v5.58 the
// cache behind it expired on a 60 s timer, so roughly once a minute a
// status poll (the desk polls every 15 s) paid that walk and the whole web
// server stalled behind it — the browser called it a dropped board.
//
// Nothing else writes this card: the free number moves only when THIS
// firmware's PUT or DELETE handler moves it. So the walk is event-driven —
// once at the first request after the mount, then again from write_body
// (which also wants it fresh for its 507 check) and h_delete. A reader is
// three field copies and never touches the FAT.

#pragma once

#include <esp_vfs_fat.h>
#include <cstdint>

#include "sd_audio.h"

namespace castle_web {

/// Card capacity in KB. `refresh` re-reads it from the card (the writers,
/// and the 507 check that must not refuse a file the last upload made room
/// for); every other caller gets the cached pair. Zero when nothing is
/// mounted.
inline void sd_space_kb(unsigned &total, unsigned &free_, bool refresh = false) {
  static bool known = false;
  static unsigned t = 0;
  static unsigned f = 0;
  if (castle_sd::g_mounted && (refresh || !known)) {
    uint64_t tb = 0;
    uint64_t fb = 0;
    if (esp_vfs_fat_info("/sd", &tb, &fb) == ESP_OK) {
      t = (unsigned) (tb / 1024);
      f = (unsigned) (fb / 1024);
    }
    known = true;   // only ever set while mounted, so a later mount fills it
  }
  total = castle_sd::g_mounted ? t : 0;
  free_ = castle_sd::g_mounted ? f : 0;
}

}  // namespace castle_web
