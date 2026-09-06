# Pending firmware patches

Found by hardware-free validation while the S2 build sat at ~20 bytes of
static RAM headroom (see castle.yaml sdkconfig notes); apply with the next
firmware session, recompile, and update tools/castle_emu_wire.py in step
(tests/test_firmware_contract.py parses sd_web.h and will flag drift).

Pending: **v5.42, written and compiled, NOT yet flashed** (2026-08-23 —
the castle was off the network when the work landed). One OTA covers it:
`make ota`, then confirm the version on the web page and re-push the page (`make publish`).

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

**Rehearsed 2026-09-01, castle still offline** — the whole flow ran against
the emulator so flash day is only the board itself: v5.43 (which folds this
v5.42 list in, plus the channel_colors migration) compiled clean, then
`sd_sync scenes` (10 tracks, largest 2.3 MB, CRC-checked), `sd_sync site`
(lean page, 90 KB gzipped) and `sd_sync ota` (the 1.1 MB image over HTTP,
come-back polling, the confirm reminder) all passed. Flash day is therefore:
`make ota` (rebuilds the image — the rehearsal binary lives on the
512Flash volume and may be stale by then), confirm **5.44** on the web page,
watch that first big upload for the watchdog cadence above, connect once so
the image is confirmed, then `make publish`.

Applied in v5.44 (2026-09-04, compiled, NOT yet flashed):

- The eInk status panel is gone: castle_eink.h, its font and QR headers
  and their generators, the two parked chip selects, the "Refresh eInk"
  button and the OTA quiesce flag that existed only for the panel's task.
  The carrier board has no wing; the page shows what the panel showed;
  D6 and D10 are free for the carrier's 5 V sense and wired button.
  Verify with /api/status (`version` 5.44) — there is no panel to read.

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
