#pragma once
// <dirent.h>, with the field the firmware reads that Windows' does not have.
//
// sd_audio.h tells a folder from a song by `d_type == DT_DIR` — newlib and
// every POSIX libc fill it in. MinGW-w64's dirent has no d_type at all, so
// on Windows this header IS the directory API: opendir/readdir/closedir
// over the C runtime's _findfirst/_findnext, which report the directory
// bit with every name. Everywhere else it hands straight on to the
// system's own header.
//
// Like FatFs (and unlike POSIX), "." and ".." never come back from readdir:
// f_readdir filters them out, and the firmware is what is under test.

#ifndef _WIN32
#include_next <dirent.h>
#else

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <io.h>
#include <string>

#define DT_UNKNOWN 0
#define DT_DIR 4
#define DT_REG 8

struct dirent {
  unsigned char d_type;
  char d_name[260];
};

struct DIR {
  intptr_t handle;
  bool fresh;  // `found` holds a name readdir has not handed out yet
  struct _finddata_t found;
  struct dirent entry;
};

inline DIR *opendir(const char *path) {
  if (path == nullptr || *path == '\0') {
    errno = ENOENT;
    return nullptr;
  }
  std::string pattern(path);
  const char last = pattern.back();
  if (last != '/' && last != '\\') pattern += '/';
  pattern += '*';
  DIR *d = new DIR{};
  d->handle = _findfirst(pattern.c_str(), &d->found);
  if (d->handle == -1) {
    delete d;
    errno = ENOENT;
    return nullptr;
  }
  d->fresh = true;
  return d;
}

inline struct dirent *readdir(DIR *d) {
  for (;;) {
    if (!d->fresh && _findnext(d->handle, &d->found) != 0) return nullptr;
    d->fresh = false;
    const char *name = d->found.name;
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) continue;
    d->entry.d_type = (d->found.attrib & _A_SUBDIR) ? DT_DIR : DT_REG;
    strncpy(d->entry.d_name, name, sizeof d->entry.d_name - 1);
    d->entry.d_name[sizeof d->entry.d_name - 1] = '\0';
    return &d->entry;
  }
}

inline int closedir(DIR *d) {
  if (d == nullptr) return -1;
  _findclose(d->handle);
  delete d;
  return 0;
}

#endif
