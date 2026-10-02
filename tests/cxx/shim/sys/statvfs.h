#pragma once
// <sys/statvfs.h> — the host's free space, which esp_vfs_fat_info reports
// when the harness has not named the card's numbers itself. Windows has no
// statvfs; the C runtime's _getdiskfree answers the same three questions
// about a drive, so on Windows this header is that, and everywhere else
// it is the system's own.

#ifndef _WIN32
#include_next <sys/statvfs.h>
#else

#include <cctype>
#include <direct.h>

struct statvfs {
  unsigned long f_frsize;
  unsigned long f_blocks;
  unsigned long f_bavail;
};

/// The drive `path` is on: its letter when it has one ("C:\..." — every
/// temp directory the harness makes), else the current drive (0).
inline int statvfs(const char *path, struct statvfs *st) {
  unsigned drive = 0;
  if (path != nullptr && isalpha(static_cast<unsigned char>(path[0])) &&
      path[1] == ':')
    drive = static_cast<unsigned>(toupper(static_cast<unsigned char>(path[0])) - 'A' + 1);
  struct _diskfree_t df {};
  if (_getdiskfree(drive, &df) != 0) return -1;
  st->f_frsize = df.sectors_per_cluster * df.bytes_per_sector;
  st->f_blocks = df.total_clusters;
  st->f_bavail = df.avail_clusters;
  return 0;
}

#endif
