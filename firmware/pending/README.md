# Pending firmware patches

Found by hardware-free validation while the S2 build sat at ~20 bytes of
static RAM headroom (see castle.yaml sdkconfig notes); apply with the next
firmware session, recompile, and update tools/castle_emu_wire.py in step
(tests/test_firmware_contract.py parses sd_web.h and will flag drift).

Nothing pending. Applied in v5.24:

- json_names — safe_name (sd_web.h) now refuses `"`, `\`, DEL and control
  bytes (< 0x20), because h_list/h_status snprintf names raw into JSON and
  one such name broke /api/files and /api/status for every client. Mirrored
  byte-for-byte in tools/castle_emu_wire.py safe_name; the contract test
  re-derives the byte rule from the C. Stack-only — zero static RAM.
- hashf noise parity — the float32 `frac(sin(n*127.1)*43758.5)` hash is
  gone. castle_effects.h and web/src/effects.ts now share one integer mix
  (lowbias32) over integer inputs: vnoise hashes its lattice cell, sparkle
  hashes (cell, pixel, zone), scatter hashes (pixel, zone, epoch), and the
  result is a 24-bit fraction that is bit-identical in float32 and double.
  web/test/firmware_parity.mjs judges every effect frame-exact now.
