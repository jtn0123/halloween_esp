# Firmware versions, and which board has run them

One table, because the old shape of this file lied by omission: it opened with
"Pending: **v5.42, written and compiled, NOT yet flashed**" and its newest
entry was v5.50, while the tree was at v5.64 — so the first thing a reader saw
was the oldest unflashed thing, and twelve versions of real work were not here
at all (grade report 2026-09-17 J2).

The column that matters is the last one. `BOARD` means it has run on hardware
and been seen to work; `compiled` means the image links and `esphome config`
passes and nothing more; `host` means only the test harness and the emulator
have seen it. Bump `version:` in `firmware/castle.yaml` with every device
change, so an OTA can be verified from the panel — that string is the only
proof the image you meant to send is the image that is running.

**The board is an Adafruit ESP32-S3 Feather #5477 in castle-carrier v3.3a**,
at 10.27.27.81. It became the castle on 2026-09-17. Before that the porch ran
an ESP32-S2 Feather; `castle_sd.yaml` was deleted the same day
(docs/notes/03-build.md §12.20).

| ver | what changed | run on |
| --- | --- | --- |
| 5.74 | The buyer build and the owner's settings (docs/notes/06-buyer-build.md §12.23). `castle_buyer.yaml`: no Wi-Fi compiled in, softAP `Castle-XXXX` + captive portal + Improv-over-USB, AP back after 3 min without the network, per-unit `castle-xxxxxx.local`, silent power-on boot. Both builds: `sd_web_prefs.h` — an optional castle key (`X-Castle-Key`; empty = off = 5.73) guarding card writes, `/api/ota`, `/api/pir` and the new `/api/settings?boot_play=`, `/api/key`, `/api/factory-reset` (erases NVS: key, settings, Wi-Fi); `boot_play` in NVS (yard on, buyer off); `/api/status` adds `board` (`feather-s3-4m2p`), `fw_variant` (`yard`/`buyer`), `locked`, `boot_play`; the fallback page gets the settings. Compiled 2026-09-30: yard image 1,253,008 B (68.3%, +3,713 B over 5.73), static RAM 120,851 B (35.4%, +64 B); buyer image 1,328,720 B (72.4%), RAM 121,519 B (35.6%). | compiled — not on hardware. The buyer's port-80 handover between the portal and the castle's server has run nowhere but the compiler. |
| 5.73 | The quiet boot publishes `current_scene: stop`, as any castle with nothing running does. v5.72 left it empty, and an empty scene reads as a running one to the checks that compare with `"stop"` — the first live light frame would have run a needless `scene_stop`. Image 1,249,295 B (68.1%), static RAM 120,787 B (35.3%). | **Flashed 2026-09-23** on the S3 Feather (OTA, "up — v5.73"): silent boot again, `scene:"stop"`, `last_reset: software`. The heard clock has still not run on the board — the numbers appear with the first show played. |
| 5.72 | The heard clock (`firmware/castle_heard.h`): a card show's cues fire on the samples the speaker has PLAYED — ESPHome's I2S audio-output callback, summed under a sequence lock — instead of on a stopwatch started when the speaker reported running. That stopwatch ran ahead of the sound by the DMA preload (5 × 10 ms of silence) plus the decoder's first gap, and further after any card stall, because it never stopped. Nothing fires until the song's first sample is heard (the stopwatch returns after 1 s for a speaker that never reports); between reports the clock runs on at most 30 ms. `/api/status` adds `sync_lead_ms` and `sync_drift_ms`, the old clock's error on the last show heard, `-1` until one has been. And the opening scene is NOT started after a software restart (an OTA, `/api/restart`): a flash no longer opens with a song; power-on, crash, brownout and watchdog boots start the show as before. Compiled 2026-09-23: image 1,249,239 B (68.1%, +1,484 B over v5.71's flashed 1,247,755), static RAM 120,755 B (35.3%, +192 B). | **Flashed 2026-09-23** on the S3 Feather (OTA, "up — v5.72"), and the first OTA that played nothing: `last_reset: software`, `playing:false` for the minute watched, `sync_lead_ms`/`sync_drift_ms` present at `-1`. It left `scene:""` rather than `"stop"`, which v5.73 fixes. The heard clock itself has not run on the board yet — nothing has played; `tests/test_heard_clock_cxx.py` and every cxx harness cover it. |
| 5.71 | Card cue format **v2** (`firmware/castle_layers.h`, `castle_cues.h` reads versions 1 and 2): an ornament layer per zone that a strike reaches with bit 7 of its mask and the render ADDS to layer 0 (clamped); strike masks 4-7, the left/right/top/bottom half of a fixture by drawn position (`rig.h` now carries each pixel's x/y), and 8-15, `arc0`..`arc7` — one patch of the loop centred k/8 of the way round clockwise from 12 o'clock (1 within 1/12 turn, linear to 0.1 at 1/4, 0.3 on a Jewel's centre), the building block for a spin round the door ring and the Jewels, on either layer; op 3, a `look` record — overlay, palette, centre role and a tempo-locked chase head / meteor drip (rate in milli-turns/s, head phase; rate 0 is the legacy clock, frame-exact). `tools/cue_file.py` writes a 2 only when a record uses one of those, so every existing show is still the same v1 bytes; a v5.70 castle refuses a v2 file whole (plays dark, `missing`). **Behaviour change for every show, v1 included: the "Soften lightning" switch now softens only a strike that lands < 333 ms after the previous strike on the SAME zone** (either layer; the first strike after load never is) — isolated lightning in the authored scenes now peaks at 0.92 instead of 0.55 and decays at its authored rate with the switch on; flash trains faster than 3/s are softened exactly as before. The soft STROBE effect is unchanged. Compiled 2026-09-23: image 1,247,888 B (68.0%, +2,048 B over v5.70's 1,245,840 in the same tree — of which the arcs are +160 B over the pre-arc v5.71 build's 1,247,728), static RAM 120,563 B (35.3%, +240 B; the arcs add none). Still 5.71: the arcs joined a version that has never been flashed, so no running castle reports a 5.71 without them. | **Flashed 2026-09-23** on the S3 Feather (OTA, "up — v5.71", confirmed by the first status poll; this build: image 1,247,755 B, 68.0%, RAM 35.3%). Only boot is verified: no v2 cue file is on the card yet and no show has run on it — the audio was stopped seconds after boot, because the opening scene starts on every boot. The host C++ harnesses (`render_check`, `layers_check`, `cues_check`), parity dumps and emulator cover the rest. |
| 5.70 | No behaviour change: the web mailbox's dispatch (the PLAY/STOP/VOLUME/LIGHT/SCENE/PIRCFG/SHOW/BLACKOUT/RESTART chain) moved out of the 200 ms interval's lambda into its own package, `castle_web_actions.yaml`, as the `web_action` script the interval executes inline — `castle_sd_common.yaml` was 492 lines against the 490-line pre-commit threshold (grade report 2026-09-17 pm I3). The `esphome config` dump before and after differs only in the script wrapper; +64 B of image. | **Flashed and verified 2026-09-17** on the S3 Feather (OTA, "up — v5.70"): storm runs (cues 8) and ends `stop`/`cues 0`; `POST /api/volume?v=40` applied through `web_action`; `pir.armed` false at boot; health 27 boots / 0 crashes. Image 1,245,824 B (67.9%), RAM 35.2%. |
| 5.69 | J1/J2/J3 (grade report 2026-09-17 pm), and the PIR off. **J2**: a non-looping scene that reaches its length now runs `cues_end` and publishes `current_scene: stop` — it used to leave `g_armed` true, the PSRAM cue blob allocated and `cues` non-zero all night. **J3**: a scene start is refused while `castle_sd::g_quiesce` is set, and `h_ota` queues `run_scene("halt")` before `esp_ota_begin`. **J1**: the scene id list is re-seeded when a PUT lands `/sd/scenes/show.man` (`g_scenes_dirty` → `seed_scene_ids`), `pir_scene` is a `text` validated by `/api/pir` against the card's list instead of a `select` with compiled options, and the evening playlist's holds are `castle_scenes::length_ms()` at run time — so a publish really is the deploy. Plus: the PIR boots DISARMED (`restore_mode: ALWAYS_OFF`) because the AM312 is not wired on the S3 carrier. | **S3 Feather, 2026-09-17**, OTA over Wi-Fi: flash 67.9% (1,245,643 of 1,835,008 B), RAM 35.2% (120,267 of 341,760). `/api/status` → `v5.69`, `pir.armed` **false** straight out of boot, and the boot log's `10 scenes, from /sd/scenes/show.man` sits before `web server up on port 80` — the seeding is a script now and the order held. **J2**: `storm` (6.5 s, does not loop) ran to its length and then `scene: stop` with `cues: 0` while its audio tail was still sounding, and idle PSRAM came back to 1,981 KB free. **J1**: `show.man` re-PUT with its ten entries in reverse byte order (audio tokens untouched) changed `/api/status`'s `scenes` inside one 200 ms tick with no reboot; the repo's own manifest went back the same way and answered `crc32 204d4af3`, so the card is exactly what `make publish` wrote. `/api/pir?scene=nosuch` → 404, `?scene=storm` took and read back. The playlist held `vigil` 31.5 s — 1,500 ms + its 30,000 ms length, read at run time. |
| 5.68 | Scene start does the card work BESIDE the audio wait instead of in front of it: `begin()` reads one manifest row, `sfx` is called, and only then is the cue file opened and the base look applied. A looping scene's re-run reuses the row it already holds rather than reopening show.man. Also: `missing` now HEALS — a republished cue file used to be reported missing until a reboot. | **Flashed and verified 2026-09-17** on the S3 Feather (OTA, "up — v5.68"). Request→audible medians over 6 runs: storm 867 ms, approach 814 ms, citizens 826 ms; ring scene_start→sound 603 ms — i.e. NO measurable change from 5.67. The "~200 ms regression" 5.67 was blamed for rests on a 3-sample v5.66 baseline (one sample 1,007 ms) and on numbers quantised by the 200 ms status tick, so it is unproven; the reorder is kept because it is the right order and free. `missing` heals: deleted cue → `missing: 'storm'`, republish, start → `missing: ''`, `cues` non-zero, no reboot. Health 25 boots / 0 crashes. |
| 5.67 | The twelve generated scene scripts are gone: a scene is `scenes/<id>.cue` + a row in `scenes/show.man` on the card, run by one generic runner (`castle_scenes.h`). Cost, measured against 5.66: RAM 145,115 → 120,043 (42.5% → 35.1%); flash 1,311,643 → 1,246,339 (71.5% → 67.9% of the slot). Also J7 (chunked cue read, `level` clamped), J3 (the event ring to PSRAM) and J5 (`.part`/`.old` uploads refused). | **Flashed and verified 2026-09-17** on the S3 Feather. `/api/status` `scenes` lists what `show.man` says, `missing` empty; every scene's `cues` went non-zero from the panel (citizens 1,258); PSRAM returned on stop; with one `.cue` deleted the scene still played its audio under the fallback look and `missing` named it; an unknown id was refused. |
| 5.66 | mDNS back on — the first line of the S2's diet given back. Cost, measured against 5.65: RAM 143,035 → 145,115 (+2,080 B, 41.9% → 42.5%); flash 1,317,039 → 1,311,643 (71.5% of the slot). | **S3 FEATHER**, OTA 2026-09-17 — `castle-feather-s3.local` resolves to the board and `/api/status` answers by name |
| 5.65 | S2 retired; `castle_feather_s3.yaml` is the base build and `castle.yaml` describes the S3. No behaviour change intended — the whole `sdkconfig` diet, mdns-off and the socket counts are exactly the v5.64 image's. | **S3 FEATHER**, `make ota` 2026-09-17 — RAM and flash byte-identical to 5.64; `storm` plays |
| 5.64 | A card file with `+` or `%` in its name can be played (`castle_web::url_encode`, grade report 2026-09-17 J1); a trailing malformed `%` escape is a refusal, as `url_decode`'s comment always said. | **S3 FEATHER**, OTA over Wi-Fi 2026-09-17 — `a+b.mp3` and `100%.mp3` both play |
| 5.63 | Any song on the card gets the full light show, from a `.cue` file beside it (`castle_cues.h`, `make cues`). | compiled |
| 5.62 | The castle can say what it was doing last night: a 64-event ring mirrored into RTC slow memory, an SNTP wall clock, the previous life's tail dumped to the card. | compiled |
| 5.61 | Survives the things that go wrong underneath it: the card's write plane split out (`sd_web_upload.h`), a read that fails part way is an answer and not a hang. | compiled |
| 5.60 | The web surface tells the truth about what it dropped, saw and confirmed — and the first `/api/status` a boot answers cancels the rollback (`flash_mode.h`), so a web-OTA'd image no longer needs a Home Assistant to survive. | compiled |
| 5.59 | Counts the light frames it drew and the ones it overwrote; `GET /api/events`; status answered from core 0 at priority 6. | compiled |
| 5.57–5.58 | The page and the castle stop disagreeing: stop clears the light override, status never waits on a FAT walk, a reboot mid-song resumes the track, a locked phone keeps the show alive. | compiled |
| 5.56 | Bug-hunt findings across firmware, generator, bridge and page. | compiled |
| 5.55 | A scene's first cue and the position clock start when the SPEAKER runs, not when play was asked for. | compiled |
| 5.54 | The OTA upload loop has one exit and no `malloc`; chunk buffers are `unique_ptr<std::array>`. | compiled |
| 5.53 | 195 Sonar smells across the demo scripts, the Python, the page markup and three firmware headers. | compiled |
| 5.52 | The control room follows the castle's own clock: `/api/status` reports `playing` and `position_ms`. | compiled |
| 5.51 | Castle Radio control room (`demo/castle-radio/`). | compiled |
| 5.50 | Starting a scene hands the strips back to the Show effect (`run_scene`) — found on the S3 Feather bring-up, where every scene ran dark after a channel test ended with `off`. `halt` still leaves the lights alone. | compiled |
| 5.49 | `castle_feather_s3.yaml` — the S3 Feather in the v3.3a carrier, as a THIRD target beside the S2. Became the base build in 5.65. | compiled |
| 5.48 | `h_list` builds its stat path with a `std::string`; the 300-byte buffer is gone and the httpd task's stack is that much lighter. Found by the host harness (`tests/cxx/web_check.cpp`). | host |
| 5.47 | `DELETE` reaches `scenes/` and `site/` (grade report 2026-09-06 J4), so a renamed scene no longer strands its old 2 MB track. Host-side the same day: the OTA-slot guard reads each build's partition table, the emulator carries both slots. | host |
| 5.46 | `safe_name` refuses every byte >= 0x80 (grade report 2026-09-06 J1): one file with a lone 0x80 made `/api/status` invalid UTF-8 and every Python client raised instead of parsing. | host |
| 5.45 | `castle_s3.yaml` — the ESP32-S3-WROOM-1 carrier build, written from `docs/V5-SPEC.md` §13 with no board in hand. | compiled; board does not exist |
| 5.44 | The eInk status panel is gone: its header, fonts, QR generators, two parked chip selects and the OTA quiesce flag that existed only for its task. | compiled |
| 5.42–5.43 | `sd_web_util.h` split out; `/api/status` gains `scenes`; `/api/files?d=`; `write_body` 507 precondition + crc32 in the reply; `/api/site/` capped at 8 MB; `set_csp()` on every page; the `channel_colors` migration. The upload watchdog cadence went to one tick per 32 KB here — see the note below. | compiled |
| 5.34 | `qr_castle` regenerated so the panel's QR landed on `/remote`; `sd_web_remote.h` `api()` catches a dead castle. | **BOARD** (the S2, 2026-08-22) |
| 5.24 | `safe_name` refuses `"`, `\`, DEL and control bytes; the float `sin()` noise hash replaced by one integer mix shared with `web/src/effects.ts`, which is what let the parity check demand frame-exact. | **BOARD** (the S2) |

## Still to watch on the board

- **The upload watchdog cadence** (v5.42): fed every 32 KB, was 8 KB.
  Verified on the emulator only. Watch the first big push on real hardware; if
  an upload reboots the board, revert the cadence in `sd_web.h write_body`.
- **The `sdkconfig` diet** (`castle.yaml`): every line of it was bought with
  the S2's ~20 bytes of dram0 headroom and the S3 measured 35.1% RAM at v5.67,
  so each was a line to give back and measure ONE AT A TIME. That list is now
  empty: mDNS was the one worth having and it came back in v5.66 (+2,080 B of
  dram0, measured), and the four options still off are off on the S3's own
  merits — softAP with no `ap:` fallback, WPS with no button, lwIP's mDNS
  *queries* (resolving someone else's `.local`, which nothing here asks) and
  WPA3-OWE on a PSK network. `CONFIG_LWIP_MAX_SOCKETS: 16` is a ceiling, not a
  cut. Nothing to watch here any more.
- **The scene ceiling** (12): NOT the S2's dram0 any more — a scene cost ~9 KB
  of it when each was a generated script, and since v5.67 a scene is card
  files with no per-scene static cost at all. What holds the number now is
  `show.man`'s fixed record count, in four places that must move together:
  `MAX_SCENES` (`tools/scene_manifest.py`), `kMaxScenes`
  (`firmware/castle_scenes.h`), and `SCENE_LIMIT` in `tools/check_loc.py` and
  `core/src/vocab.rs`. Raising it is deliberate work on all four with a
  measured number — docs/notes/03-build.md §12.21.
- **The bare-Feather decode numbers** (`make bench-audio`): the S2's were
  measured (PROJECT_NOTES §12.13, 96 kbps off the card, zero underruns); this
  chip's have not been.

## The bring-up list, for the next board out of the box

1. **Check the Feather is #5477** (4 MB flash, 2 MB PSRAM). #5323 has no
   PSRAM; the boot log's PSRAM line is where that shows.
2. First flash the BARE Feather, before it goes into the carrier: USB-C to the
   Mac, hold BOOT, tap RESET, release BOOT, then `make upload`. A factory
   Feather runs Adafruit's own USB stack, not the S3's ROM port. Once this
   image is on it, uploads and `make logs` need no buttons. In the carrier it
   is brick first, USB second, never USB alone: USB-only power drives 3.3 V
   logic into an unpowered 74AHCT125 and both amps (castle-carrier-v3
   docs/AUDIT-2026-09-03.md E-F4).
3. Watch the boot log: PSRAM found, card mounted, media player up. Confirm the
   version string from `firmware/castle.yaml` on the web page.
4. Barrel jack in: tower L, doorway and tower R each light, and the status
   pixel shows blue at boot. A dark strip is an RMT block — `rmt_symbols: 48`,
   three strips plus the pixel is 192 of 192 (`tools/gen_rig.py`).
5. Then `make publish`, or the card has no show and every scene chirps.

The WROOM carrier build (`castle_s3.yaml`) has its own extra steps when that
board exists: `i2c: scan: true` must find **0x41** (0x40 is a Qwiic breakout
answering instead of the carrier's INA219, and its reading is not the
castle's rail), and "Castle 5V current" read through a whole show is the
answer to V5-SPEC open question 1 — how much this thing actually draws, which
has never been measured, only estimated.

Keep `tools/castle_emu_wire.py` in step with any change to `sd_web.h`:
`tests/test_firmware_contract.py` parses the C and flags drift, and
`tests/cxx/web_check.cpp` compiles the real headers and sends every request to
both castles. Change both in one commit.
