// firmware/castle_tz.h on the host: one "<epoch> <tz>" per line in, one
// verdict per line out — "bad", or the local time castle_tz::local gives:
// "Y M D h m s wday dst". tests/test_castle_tz_cxx.py builds the same
// answers with tools/castle_emu_tz.py and with Python's zoneinfo.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "castle_tz.h"

int main() {
  char line[256];
  while (fgets(line, sizeof(line), stdin) != nullptr) {
    char *nl = strchr(line, '\n');
    if (nl != nullptr) *nl = '\0';
    char *sp = strchr(line, ' ');
    if (sp == nullptr) return 2;
    *sp = '\0';
    const long long utc = strtoll(line, nullptr, 10);
    castle_tz::Zone z;
    if (!castle_tz::parse(sp + 1, z)) {
      printf("bad\n");
      continue;
    }
    const castle_tz::Local t = castle_tz::local(utc, z);
    printf("%d %d %d %d %d %d %d %d\n", t.year, t.month, t.day, t.hour, t.minute,
           t.second, t.wday, t.dst ? 1 : 0);
  }
  return 0;
}
