#pragma once
// The card, on the host: "/sd/..." → $CASTLE_CARD/...
//
// The firmware hardcodes the mount point in every fopen/opendir/stat/
// unlink/rename/mkdir it makes (there is exactly one volume on the device,
// and naming it is clearer than threading a root through ten call sites).
// A host harness cannot write to /sd, so castle_shim.h redirects those six
// calls with function-like macros and they land here.
//
// The redirection is the PLATFORM layer, not a test double of the firmware:
// where the host's libc and ESP-IDF's FATFS disagree, this file models
// FATFS, because FATFS is what the handler will meet on the porch. Three
// cases bite, and all three decide whether h_sd_get answers a file or
// "no such file":
//
//   fopen() on a DIRECTORY — POSIX opens it happily and fails at the first
//   read, FatFs refuses it outright;
//   a TRAILING SEPARATOR — "/sd/a/" is the file `a` to POSIX and
//   FR_NO_PATH to FatFs;
//   a "." SEGMENT — FF_FS_RPATH is 0 in ESP-IDF's ffconf.h, so "." is an
//   ordinary file name that is never found.

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace castle_shim {

/// An environment variable, or a default. The harness is configured this
/// way rather than by argv so one binary can be re-run under many castles.
inline std::string env(const char *name, const std::string &fallback = "") {
  const char *v = getenv(name);
  return (v == nullptr || *v == '\0') ? fallback : std::string(v);
}

inline unsigned long env_ul(const char *name, unsigned long fallback) {
  const char *v = getenv(name);
  if (v == nullptr || *v == '\0') return fallback;
  return strtoul(v, nullptr, 0);
}

/// The directory standing in for the card. Empty means "no CASTLE_CARD was
/// set": paths then pass through untouched, which is wrong but obvious —
/// the harness refuses to start without it.
inline const std::string &card_root() {
  static const std::string root = env("CASTLE_CARD");
  return root;
}

/// "/sd/scenes/x.mp3" → "<card>/scenes/x.mp3"; "/sd" → "<card>".
/// Anything not under /sd is its own path (the harness's own temp files).
inline std::string map_path(const char *p) {
  if (p == nullptr) return std::string();
  const std::string s(p);
  if (card_root().empty()) return s;
  if (s == "/sd") return card_root();
  if (s.compare(0, 4, "/sd/") == 0) return card_root() + s.substr(3);
  return s;
}

inline bool is_dir(const std::string &p) {
  struct stat st {};
  return ::stat(p.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

/// Paths under /sd that POSIX resolves and FatFs does not.
///
/// ESP-IDF builds FatFs with FF_FS_RPATH = 0 (components/fatfs/src/ffconf.h),
/// so "." is not a directory reference — create_name() reads it as an
/// ordinary file name and follow_path() never finds it. And a trailing
/// separator is not decoration: the segment before it must be a directory
/// (FR_NO_PATH otherwise) and the empty segment after it is FR_INVALID_NAME
/// either way. POSIX shrugs at both, so "/sd/a/" would open the file `a`
/// here and fail on the board.
inline bool fat_missing(const std::string &p) {
  if (p.compare(0, 3, "/sd") != 0) return false;
  if (p.back() == '/') return true;
  for (size_t i = 0; i < p.size();) {
    const size_t end = std::min(p.find('/', i), p.size());
    const std::string seg = p.substr(i, end - i);
    if (seg == "." || seg == "..") return true;
    i = end + 1;
  }
  return false;
}

}  // namespace castle_shim

// ── the six redirected calls ────────────────────────────────────────────
// Defined before the macros in castle_shim.h rename their unqualified uses.

inline FILE *castle_shim_fopen(const char *path, const char *mode) {
  if (castle_shim::fat_missing(path)) {
    errno = ENOENT;
    return nullptr;
  }
  const std::string p = castle_shim::map_path(path);
  // FatFs f_open on a directory is FR_INVALID_NAME/FR_NO_FILE, so
  // send_sd_file's fopen returns null and h_sd_get answers 404. POSIX
  // would hand back a FILE* that fails on the first fread — a 200 with an
  // empty body, which no castle has ever sent.
  if (mode != nullptr && mode[0] == 'r' && castle_shim::is_dir(p)) {
    errno = EISDIR;
    return nullptr;
  }
  return ::fopen(p.c_str(), mode);
}

inline DIR *castle_shim_opendir(const char *path) {
  if (castle_shim::fat_missing(path)) {
    errno = ENOENT;
    return nullptr;
  }
  return ::opendir(castle_shim::map_path(path).c_str());
}

inline int castle_shim_stat(const char *path, struct stat *st) {
  if (castle_shim::fat_missing(path)) {
    errno = ENOENT;
    return -1;
  }
  return ::stat(castle_shim::map_path(path).c_str(), st);
}

inline int castle_shim_unlink(const char *path) {
  return ::unlink(castle_shim::map_path(path).c_str());
}

inline int castle_shim_rename(const char *from, const char *to) {
  return ::rename(castle_shim::map_path(from).c_str(),
                  castle_shim::map_path(to).c_str());
}

inline int castle_shim_mkdir(const char *path, mode_t mode) {
  return ::mkdir(castle_shim::map_path(path).c_str(), mode);
}
