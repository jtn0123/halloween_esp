# Pending firmware patches

Found by hardware-free validation while the S2 build sat at ~20 bytes of
static RAM headroom (see castle.yaml sdkconfig notes); apply with the next
firmware session, recompile, and update tools/castle_emu_wire.py in step
(tests/test_firmware_contract.py parses sd_web.h and will flag drift).

Pending for the next firmware session (found by judge B, pass 2; no
source under firmware/ was edited for them — both need a recompile/reflash):

- qr_castle — `make generate` so firmware/generated/qr_castle.h encodes
  `http://<castle>/remote` (tools/gen_qr.py's default already says so); the
  flashed eInk QR still lands on the 2.4 MB desk instead of the phone remote.
- sd_web_remote.h `api()` — add `.catch(sync)` so a tap at a dead castle does
  not leave an unhandled "Failed to fetch" in the phone's console; the
  emulator now serves this page byte-for-byte (tools/castle_emu_http.py), so
  web/test/e2e/remote.spec.ts exercises whatever the C says.

Applied in v5.24:

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
