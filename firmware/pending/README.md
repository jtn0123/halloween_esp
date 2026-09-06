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
512Flash volume and may be stale by then), confirm the version string in
`firmware/castle.yaml` on the web page (it has moved four times since this
was rehearsed — never a number typed here),
watch that first big upload for the watchdog cadence above, connect once so
the image is confirmed, then `make publish`.

Applied in v5.47 (2026-09-06, compiled, NOT yet flashed):

- **DELETE reaches `scenes/` and `site/`** (`sd_web.h` `route_dir`, shared
  with PUT). Until now a file could be put into either directory and never
  removed over the wire, so a renamed scene stranded its old 2 MB track
  until someone pulled the card. `sd_sync rm scenes/<name>` and `castle rm
  scenes/<name>` use it; the emulator mirrors the two routes and the
  contract test counts them. Two more handlers, 25 of the 32 registered.
  (grade report 2026-09-06 J4)
- **The missing-card log line tells the truth** (`sd_audio.h`): "scenes
  will play the chirp, not the show" — it said the flash scenes still
  worked, which has not been so since the flash build went. Comments in
  `flash_mode.h` and `castle_inputs.yaml` that described OTA as off and the
  S2's missing console as this board's are rewritten for both chips.
  (grade report 2026-09-06 J6, H4)
- Host-side, same commit: the OTA-slot guard reads each build's partition
  table instead of the S2's number, the emulator carries both slots
  (`--chip s3`), the stream server's port and `/api/health`'s keys join the
  firmware contract, and the weekly CI compile can fail and be run by hand
  (`gh workflow run ci.yml`). (grade report 2026-09-06 J5, J3, D1)

Applied in v5.46 (2026-09-06, compiled, NOT yet flashed):

- **safe_name refuses every byte >= 0x80** (`sd_web_util.h`). v5.24 closed
  this outage class for quotes, backslashes and control bytes and left the
  high half open: `json_escape` passes a high byte through raw, so one file
  named with a lone 0x80 made `/api/status` and `/api/files` invalid UTF-8
  and every Python client of the castle raised instead of parsing —
  `make publish` and the desk's device panel both. Card names are ASCII
  now; `h_list`'s existing skip-and-count path covers what the Mac wrote
  onto the card directly, and `h_put` answers 400. Mirrored in
  `tools/castle_emu_wire.py`; the contract test re-derives the new bound
  out of the C. Stack-only — zero static RAM. (grade report 2026-09-06 J1)

Applied in v5.45 (2026-09-05, compiled, NOT yet flashed) — and the first
change here that is not about the board in the yard:

- **`firmware/castle_s3.yaml` — the ESP32-S3 carrier build.** Written from
  §13 of `docs/V5-SPEC.md` in the castle-carrier v5 project, with no board
  in hand. It is a SECOND target: `make build-s3` / `upload-s3` / `logs-s3`,
  validated beside castle_sd.yaml by `make validate` and compiled by the
  weekly CI job. The S2 build is untouched and stays the porch's.
  `castle_sd_common.yaml` is the show both of them read; what is left in
  `castle_sd.yaml` is the Feather's own NeoPixel, which the S3 build has no
  hardware for and therefore does not include.

  **Nothing here has been on hardware.** Bring-up, when the board exists:

  1. Flash over the module's own USB Serial/JTAG (`make upload-s3`) — no
     adapter, and no BOOT-button dance. Confirm the version string in
     `firmware/castle.yaml` on the web page.
  2. Watch the boot log on the same USB port. The S2 could never do this;
     it is the first time this firmware has had a console on hardware —
     `tools/qemu_boot.sh` has shown the log up to the SD mount in QEMU
     (2026-09-06, docs/QEMU.md), so what to expect before that point is
     known: health counter, I2C recovery, the INA219 failing cleanly when
     absent, the media player up.
  3. Three strips, not one: check tower L, doorway and tower R each light.
     A dark strip past the first is the RMT block size, and the number to
     look at is `rmt_symbols: 48` in `generated/lights_s3.yaml`.
  4. `i2c: scan: true` should find **0x41**. 0x40 means a Qwiic breakout is
     answering instead of the carrier's INA219 and the reading is not the
     castle's rail.
  5. Read "Castle 5V current" through a whole show. That is V5-SPEC open
     question 1 — how much this thing actually draws — and the answer has
     never been measured, only estimated.
  6. Only then start giving the dram0 diet back, one `sdkconfig_options`
     line at a time, measuring each (V5-SPEC §13.5). mDNS is the one worth
     having: it has never worked on the S2, and `devices.toml` holds a
     hard-coded address because of that.

  Two places this build deliberately departs from the spec, both written
  down rather than argued:

  - §13.8 asks the first S3 image to announce itself as **v6.00**, on the
    grounds that a board this different should not share a major number.
    This is v5.45, because the port did not replace the S2 build and one
    version string still serves both — a v6.00 on the porch board would say
    something untrue. Renumber when the S3 becomes the only castle.
  - §13.1–13.3 are written as edits to `castle.yaml` and `castle_sd.yaml` —
    a port in place, ending with one build. This is a variant beside them
    instead, for as long as the S2 is the castle in the yard.

  One thing the carrier's own checker will still report: `gen/check_firmware_pins.py`
  reads `castle.yaml` and `castle_sd.yaml` (its `ROOTS`), not `castle_s3.yaml`,
  so its platform pass will keep saying "no `variant: esp32s3`" and "no i2c:
  block" while the S2 build exists. That is the checker measuring the porch
  board, correctly. Its eInk precondition — the exit-2 one — is genuinely
  gone: v5.44 deleted `eink_cs`, `sram_cs` and `eink_dc`, and nothing here
  brought them back.

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
