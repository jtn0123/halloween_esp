#pragma once
// The heap, with capabilities it does not have. /api/status prints two of
// these numbers and boot_log.h asks SPIRAM first on purpose, so both must
// answer — but neither number means anything on a host, and the test that
// compares the C against the emulator skips them for that reason.

#include <cstdlib>
#include <cstdint>

#define MALLOC_CAP_EXEC (1 << 0)
#define MALLOC_CAP_32BIT (1 << 1)
#define MALLOC_CAP_8BIT (1 << 2)
#define MALLOC_CAP_DMA (1 << 3)
#define MALLOC_CAP_SPIRAM (1 << 10)
#define MALLOC_CAP_INTERNAL (1 << 11)
#define MALLOC_CAP_DEFAULT (1 << 12)

/// Fixed, so /api/status is deterministic across runs: 1800 KB of PSRAM and
/// 96 KB of internal heap, the numbers a healthy S2 Feather reports.
inline size_t heap_caps_get_free_size(uint32_t caps) {
  if (caps & MALLOC_CAP_SPIRAM) return 1800u * 1024u;
  return 96u * 1024u;
}

inline void *heap_caps_malloc(size_t size, uint32_t caps) {
  (void) caps;
  return malloc(size);
}

inline void *heap_caps_calloc(size_t n, size_t size, uint32_t caps) {
  (void) caps;
  return calloc(n, size);
}

inline void heap_caps_free(void *p) { free(p); }
