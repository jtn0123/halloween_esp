#pragma once
// The host operating system under the shim — the four things a harness
// does that POSIX and Windows spell differently. Everything else the shim
// needs from the host is in the C library both have.
//
//   set_env  — the harness configures the firmware's fake card through the
//              environment, and Windows has no setenv;
//   make_dir — mkdir(path, mode) is POSIX; Windows' mkdir takes no mode;
//   localtime_r — castle_health stamps its log with it; the Windows runtime
//              spells the same call localtime_s, arguments swapped;
//   binary pipes — the web and mailbox harnesses speak raw HTTP and JSON
//              over stdin/stdout, byte for byte, and the Windows C runtime
//              opens both in TEXT mode: every "\n" written would arrive as
//              "\r\n", and a 0x1A read would end the input. The device
//              has no text mode either.
//
// The compiler there is MinGW-w64's g++ (tests/cxx_compiler.py says why).

#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>

#ifdef _WIN32
#include <direct.h>
#include <fcntl.h>
#include <io.h>
#endif

#if defined(_WIN32) && !defined(_POSIX_THREAD_SAFE_FUNCTIONS)
inline struct tm *localtime_r(const time_t *t, struct tm *out) {
  return localtime_s(out, t) == 0 ? out : nullptr;
}
#endif

namespace castle_shim {

/// setenv(name, value, 1), on either system.
inline int set_env(const char *name, const char *value) {
#ifdef _WIN32
  // _putenv keeps its own copy in the C runtime's environment block, which
  // is the one getenv reads.
  return _putenv((std::string(name) + "=" + value).c_str());
#else
  return ::setenv(name, value, 1);
#endif
}

/// mkdir(path, mode). FatFs has no permission bits, so Windows dropping
/// the mode loses nothing the card would have kept.
inline int make_dir(const char *path, unsigned mode) {
#ifdef _WIN32
  (void) mode;
  return ::_mkdir(path);
#else
  return ::mkdir(path, static_cast<mode_t>(mode));
#endif
}

#ifdef _WIN32
/// Both pipes in binary mode before main() reads or writes a byte.
inline const bool binary_stdio = [] {
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
  return true;
}();
#endif

}  // namespace castle_shim
