// The card's free-space cache (firmware/sd_space.h), run on this machine.
//
// The point of v5.58 is what does NOT happen: /api/status is polled every
// 15 s by the desk and more by a phone, and it must never walk the FAT. The
// cache is filled once after the mount and then only when a writer says the
// number moved, so the timer that used to expire mid-poll — and stall the
// whole httpd task for seconds on a big card — is gone.
//
// The shim's esp_vfs_fat_info reads CASTLE_SD_FREE_KB every call, so moving
// the environment between calls shows exactly which call touched the card.
// tests/test_sd_space_cxx.py compiles and runs this.
#include <array>
#include <cstdio>
#include <cstdlib>

#include <castle_shim.h>

#include "sd_space.h"

namespace {
int failures = 0;
void check(bool ok, const char *what, int line) {
  if (!ok) { std::printf("FAIL line %d: %s\n", line, what); failures++; }
}
#define CHECK(x) check((x), #x, __LINE__)

void card_says(unsigned total_kb, unsigned free_kb) {
  std::array<char, 24> buf{};
  std::snprintf(buf.data(), buf.size(), "%u", total_kb);
  setenv("CASTLE_SD_TOTAL_KB", buf.data(), 1);
  std::snprintf(buf.data(), buf.size(), "%u", free_kb);
  setenv("CASTLE_SD_FREE_KB", buf.data(), 1);
}
}  // namespace

int main() {
  using castle_web::sd_space_kb;
  unsigned total = 0;
  unsigned free_ = 0;

  // No card: zeroes, and nothing is remembered from before the mount.
  castle_sd::g_mounted = false;
  card_says(4000, 3000);
  sd_space_kb(total, free_);
  CHECK(total == 0 && free_ == 0);

  // The first read after the mount fills the cache.
  castle_sd::g_mounted = true;
  sd_space_kb(total, free_);
  CHECK(total == 4000 && free_ == 3000);

  // A status poll never asks the card again, however the card moves.
  card_says(4000, 1000);
  for (int i = 0; i < 100; i++) sd_space_kb(total, free_);
  CHECK(total == 4000 && free_ == 3000);

  // A writer (write_body's 507 check, h_put and h_delete afterwards) asks.
  sd_space_kb(total, free_, true);
  CHECK(total == 4000 && free_ == 1000);
  card_says(4000, 900);
  sd_space_kb(total, free_);
  CHECK(free_ == 1000);   // and the poll after it is free again

  // An unmount hides the numbers without forgetting them.
  castle_sd::g_mounted = false;
  sd_space_kb(total, free_);
  CHECK(total == 0 && free_ == 0);
  castle_sd::g_mounted = true;
  sd_space_kb(total, free_);
  CHECK(total == 4000 && free_ == 1000);

  if (failures != 0) return 1;
  std::printf("sd space OK\n");
  return 0;
}
