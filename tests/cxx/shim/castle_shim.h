#pragma once
// Include this FIRST, before any firmware header. It pulls in every system
// header the firmware will ask for, and only then renames the six calls
// that would otherwise reach for a card this machine does not have.
//
// WHY THE ORDER MATTERS. The redirection is a set of function-like macros —
// `stat(p, s)` and not `stat`, so `struct stat` keeps its meaning. A macro
// like that is still poison to the DECLARATIONS in <sys/stat.h>, so every
// header that declares one is included here, above the #defines. The
// firmware's own `#include <cstdio>` further down is then a no-op against
// its include guard, and the calls it makes below expand to ours.
//
// Nothing in here is a stand-in for firmware behaviour. It is the platform
// underneath the firmware, and the whole point of the harness is that the
// layer above it is the real thing.

// Every system header the firmware headers include, plus the ones the
// standard library might pull a redirected symbol through later.
#include <algorithm>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>
#include <vector>

#include <castle_shim_fs.h>
#include <esp_http_server.h>

#define fopen(p, m) castle_shim_fopen((p), (m))
#define opendir(p) castle_shim_opendir((p))
#define stat(p, s) castle_shim_stat((p), (s))
#define unlink(p) castle_shim_unlink((p))
#define rename(a, b) castle_shim_rename((a), (b))
#define mkdir(p, m) castle_shim_mkdir((p), (m))
