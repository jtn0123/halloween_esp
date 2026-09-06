#pragma once
// esp_rom_crc32_le — the ROM's CRC-32, which is the ordinary reflected
// CRC-32 (polynomial 0xEDB88320, the one zlib and PNG use) with the
// initial and final inversions left to the caller.
//
// That last part is why this is not "any CRC will do": write_body seeds
// crc=0, feeds every chunk, and prints the result as the upload's crc32,
// and sd_sync then compares it against Python's zlib.crc32 of the same
// bytes. The two agree only because the ROM routine inverts on the way in
// AND on the way out, exactly as zlib does. Getting that wrong here would
// hide a real mismatch on the porch behind a green test.

#include <cstddef>
#include <cstdint>

namespace castle_shim {

inline const uint32_t *crc32_table() {
  static uint32_t tbl[256];
  static bool built = false;
  if (!built) {
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t c = i;
      for (int k = 0; k < 8; k++) c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
      tbl[i] = c;
    }
    built = true;
  }
  return tbl;
}

}  // namespace castle_shim

inline uint32_t esp_rom_crc32_le(uint32_t crc, const uint8_t *buf, uint32_t len) {
  const uint32_t *tbl = castle_shim::crc32_table();
  crc = ~crc;
  for (uint32_t i = 0; i < len; i++)
    crc = tbl[(crc ^ buf[i]) & 0xffu] ^ (crc >> 8);
  return ~crc;
}
