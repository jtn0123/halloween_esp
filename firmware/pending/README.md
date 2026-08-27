# Pending firmware patches

Found by hardware-free validation while the S2 build sat at ~20 bytes of
static RAM headroom (see castle.yaml sdkconfig notes); apply with the next
firmware session, recompile, and update tools/castle_emu_wire.py in step
(tests/test_firmware_contract.py parses sd_web.h and will flag drift).

Pending: **v5.42, written and compiled, NOT yet flashed** (2026-08-23 —
the castle was off the network when the work landed). One OTA covers it:
`make ota`, then confirm on the panel and re-push the page (`make publish`).

- sd_web_util.h split out of sd_web.h (helper layer; contract test reads it).
- /api/status gains `scenes` (the build's ids) — the desk's stale-firmware
  warning reads it.
- /api/files?d=<subdir> lists inside the card.
- write_body: 507 free-space precondition, crc32 in the reply (sd_sync
  compares), watchdog fed every 4th chunk (one tick / 32 KB — **verify the
  first big upload on the bench**; revert the cadence if a push reboots it).
- /api/site/ uploads capped at 8 MB (413).
- set_csp() on every served page (root, /site/*, /remote).

After the OTA, `make publish` puts the LEAN desk page + per-scene audio on
the card — first paint drops from 3.3 MB to ~150 KB gzipped.

Applied in v5.34 (flashed to the new porch board, 2026-08-22):

- qr_castle — regenerated (tools/gen_qr.py) so the eInk QR lands on
  `http://<castle>/remote`, the phone remote, not the 2.4 MB desk.
- sd_web_remote.h `api()` — `.catch(sync)` so a tap at a dead castle does
  not leave an unhandled "Failed to fetch" in the phone's console; the
  emulator serves this page byte-for-byte (tools/castle_emu_http.py), so
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
  web/test/firmware_parity.ts judges every effect frame-exact now.
