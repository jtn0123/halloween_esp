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
#include <map>
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

/// A CARD THAT STOPS ANSWERING, on demand (A8, v5.61).
///
/// CASTLE_SD_FAIL_AFTER=<bytes> makes every read of a file under /sd fail
/// once that many bytes have come out of it — fread returns short and
/// ferror() is true afterwards, which is exactly what ESP-IDF's FATFS does
/// when the SPI card NAKs a sector mid-transfer. It is the one failure in
/// this file that is not a platform difference but a fault: there is no
/// other way to reach the torn-transfer path from a host test, and the path
/// exists because the alternative was framing a dying card as a short song.
inline std::map<FILE *, size_t> &read_budget() {
  static std::map<FILE *, size_t> m;
  return m;
}

inline std::map<FILE *, bool> &read_failed() {
  static std::map<FILE *, bool> m;
  return m;
}

}  // namespace castle_shim

// ── the redirected calls ────────────────────────────────────────────────
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
  FILE *f = ::fopen(p.c_str(), mode);
  // SET, not non-zero: "CASTLE_SD_FAIL_AFTER=0" is the card that refuses
  // the very first sector, which is the leg where nothing has gone out yet
  // and a real 500 is still possible.
  if (f != nullptr && mode != nullptr && mode[0] == 'r' &&
      std::string(path).compare(0, 3, "/sd") == 0 &&
      !castle_shim::env("CASTLE_SD_FAIL_AFTER").empty())
    castle_shim::read_budget()[f] = castle_shim::env_ul("CASTLE_SD_FAIL_AFTER", 0);
  return f;
}

/// fread, with the injected fault above. Short + ferror, never a silent
/// zero: a zero alone is end-of-file, which is the confusion A8 was.
inline size_t castle_shim_fread(void *dst, size_t size, size_t n, FILE *f) {
  auto it = castle_shim::read_budget().find(f);
  if (it == castle_shim::read_budget().end()) return ::fread(dst, size, n, f);
  if (it->second == 0) {
    castle_shim::read_failed()[f] = true;
    return 0;
  }
  const size_t want = size * n;
  const size_t allow = want < it->second ? want : it->second;
  const size_t got = ::fread(dst, 1, allow, f);
  it->second -= got;
  return size == 0 ? 0 : got / size;
}

inline int castle_shim_ferror(FILE *f) {
  const auto it = castle_shim::read_failed().find(f);
  if (it != castle_shim::read_failed().end() && it->second) return 1;
  return ::ferror(f);
}

inline int castle_shim_fclose(FILE *f) {
  castle_shim::read_budget().erase(f);
  castle_shim::read_failed().erase(f);
  return ::fclose(f);
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

/// rename, with one injected fault (A11, v5.61).
///
/// CASTLE_RENAME_PART_FAILS=1 makes the rename of a `<name>.part` sidecar
/// into place fail, the way a full FAT root directory or a card that has
/// gone read-only makes it fail. That single call is the moment the upload
/// either replaces the previous copy or must leave it alone, and a host
/// filesystem will never refuse it on its own.
inline int castle_shim_rename(const char *from, const char *to) {
  const std::string src(from == nullptr ? "" : from);
  if (src.size() >= 5 && src.compare(src.size() - 5, 5, ".part") == 0 &&
      castle_shim::env_ul("CASTLE_RENAME_PART_FAILS", 0) != 0) {
    errno = ENOSPC;
    return -1;
  }
  return ::rename(castle_shim::map_path(from).c_str(),
                  castle_shim::map_path(to).c_str());
}

inline int castle_shim_mkdir(const char *path, mode_t mode) {
  return ::mkdir(castle_shim::map_path(path).c_str(), mode);
}
