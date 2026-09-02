# Halloween Castle — Decision log and roadmap (§13–§14)

Part of the design record; the index is [`PROJECT_NOTES.md`](../../PROJECT_NOTES.md). Section numbers are global across the parts, so `§12.9` means the same thing in every file.

---

## 13. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-09 | RAK18060 ruled out | No solderless I2S path; no ESPHome TAS2560 driver; S2 poor audio host (§2) |
| 2026-08-09 | Audio via DFPlayer Mini | Native ESPHome support, uses the 32 GB SD, zero DSP load on a single-core S2 |
| 2026-08-09 | ESPHome for lights *and* sound on one board | WLED can't play audio; cross-device sync too laggy for lightning/thunder |
| 2026-08-09 | RAK4630 repurposed as remote trigger | Plays to nRF52840 strengths (µA sleep, LoRa range) instead of its no-WiFi weakness |
| 2026-08-09 | Buy genuine DFR0299, wire BUSY pin anyway | Clone chip lottery is the top practical risk (§9) |
| 2026-08-09 | Prototype in a browser previewer first | Tune timing without reflashing; one scene file feeds preview *and* ESPHome |
| 2026-08-09 | Second palette: Haunted Mansion violet/green | Justin's reference. Coexists with candlelit warm — they're different scenes, not a global mode |
| 2026-08-09 | Music written original, not sourced | Haunted-parlour idiom (A minor, 3/4, raised 7th). Avoids putting copyrighted material on a public-facing display |
| 2026-08-09 | Parts sourcing parked to the end | Justin's call — settle the show first, then buy to fit it (§11) |
| 2026-08-10 | Light cues derive from audio markers, not hand-typed times | Synths report their own event times; a jittered heartbeat can't drift from its light (§12.6) |
| 2026-08-10 | Base effects get a per-zone `level`; strikes stay unscaled | Contrast is the whole mechanism — a standing effect at full brightness buries the audio-driven pulses (§12.6) |
| 2026-08-10 | Each strike carries its own colour and decay | One global white snap can't express both a lightning bolt and a heartbeat; colour is how a viewer hears with their eyes (§12.6) |
| 2026-08-10 | ~~Previewer arms audio on the first gesture anywhere~~ **Reverted** | Made any stray click blast a scene at full volume, and made the overlay's own text a lie (§12.7) |
| 2026-08-10 | Previewer is muted by default; audio starts only from Play | A preview tool must never make noise you didn't ask for. Unmuting is one click; being ambushed is not recoverable (§12.7) |
| 2026-08-10 | Mute uses `.muted`, never volume 0 | Volume is also written by the fade-in and master slider, so a volume-based mute is one stray write from being undone (§12.7) |
| 2026-08-10 | Seeking replays all `set` cues from zero | Scrubbing otherwise keeps whatever effects were on stage when you grabbed the bar (§12.7) |
| 2026-08-10 | Imported tracks get onset-detected light, not hand-typed cues | Outside audio can't report its own beats; detecting them keeps the "light follows sound" guarantee that generated scenes get for free (§12.8) |
| 2026-08-10 | Onsets detected per frequency band, not once overall | A single onset track makes all three zones blink together; banding gives the bass the door and the melody the towers (§12.8) |
| 2026-08-10 | `tracks/` git-ignored, scene definitions tracked | The audio is Justin's and large; the show should still be reproducible from the repo (§12.8) |
| 2026-08-10 | SD audio built as a separate variant, not folded into castle.yaml | It is unproven on S2; the flash build must keep working with no card in the slot (§12.9) |
| 2026-08-10 | SD reads whole files into PSRAM rather than streaming | `AudioFile` is {ptr,len,type} and `play_file()` is public, so the decoder needs no changes. Streaming would mean writing a MediaSource nobody has written (§12.9) |
| 2026-08-10 | NeoPixel → A0, PIR → A1, I2S DOUT → A3 | The eInk FeatherWing hard-wires D5/D6/D9/D10; NeoPixel data on the SD chip select would have been an intermittent-mount nightmare (§12.10) |
| 2026-08-10 | eInk and SRAM chip selects parked HIGH at boot | They share the card's SPI bus and we use neither; a floating CS is a device that may answer mid-transaction (§12.10) |
| 2026-08-10 | Tracks remember their source in tracks.json | An imported MP3 is otherwise a dead end — no way to rebuild it at different settings without hunting for the link again |
| 2026-08-10 | Build cache moved to external storage, repo stays put | Source is 2.8 MB, cache is 1.3 GB. Moving the repo breaks `.venv` paths to solve nothing (§12.12) |
| 2026-08-10 | Built a decode benchmark before choosing a codec | Nobody has published an ESP32-S2 MP3 measurement; picking a codec on folk wisdom is how you find out in October (§12.13) |
| 2026-08-10 | MP3 stays the codec | Measured on the board: zero underruns, zero decode errors, 32.7 ms average loop. The S2-can't-decode folklore is wrong at 96 kbps mono (§12.13) |
| 2026-08-10 | Never set CONFIG_ESP_CONSOLE_USB_CDC on this board | It doesn't just fail — it panics. Interrupt watchdog timeout inside `esp_usb_console_flush_internal`, captured from the crash log (§12.14) |
| 2026-08-10 | Find the device by IP, not mDNS | mDNS does not resolve on this network; the API on port 6053 does. A subnet scan found it immediately (§12.14) |
| 2026-08-22 | Two bitrates: 32 kbps embedded, 96 kbps on the card | The flash build is bounded by its 2.9 MB partition; the card never was. 32 kbps brickwalls at ~6 kHz, measured; 96 is what the show was tuned at (`scenes.yaml` `card_bitrate`, `audio/card/`) |
| 2026-08-22 | Scene audio stays mono | Both amps are pinned LEFT (WIRING §5) and the S2 puts a mono stream in the left slot at full level — stereo doubles the bytes and changes nothing (HARDWARE_FINDINGS §8) |
| 2026-08-22 | One volume ceiling, `hardware.audio.max_volume` | Generated into every scene's level and rig.h, clamped by /api/volume and the emulator. 0.8 for the bare 4 Ω drivers that crackled above it; 1.0 for the shrouded pair that replaced them |
| 2026-08-22 | A stop stops the scripts; a play halts them | `scene_stop` only cleared output until v5.35, so a looping scene walked back on; `/api/play` runs `run_scene("halt")` first (v5.37) so a song or a test tone keeps the speakers |
| 2026-08-27 | The show's arithmetic moves into a Rust crate, `core/` | The same maths lived in C++ (firmware), TypeScript (desk) and Python (generators), and a render's last digits depended on whichever numpy/scipy wheel the machine had — the desk stopped predicting the porch by drifting, silently. One implementation, spawned as a subprocess by `tools/render_audio.py` and `tools/import_track.py`, makes a render byte-identical on every machine; `Modes::CANONICAL` pins the reference wheel's arithmetic rather than the local one. The copies that remain are held bit-exact by [`docs/PARITY.md`](../PARITY.md) |
| 2026-08-27 | castle-core takes zero dependencies | It must compile to a small WASM module the cue desk inlines (every KB of crate is ~1.4 KB of page), and it must stay auditable line-for-line against the C++ and TS copies it exists to replace. A crate graph would put both out of reach — so the HTTP server, CRC32, TOML subset and WAV writer are all in-crate |
| 2026-08-27 | The Rust studio is a twin, not a replacement | Track B of [`.claude/typesafe-migration-plan.md`](../../.claude/typesafe-migration-plan.md): a rewrite in place would have swapped the desk's server mid-season on a decoration that has one operator and one October. So `core/src/bin/studio.rs` was built beside `tools/studio.py` and held answer-for-answer to it (`tests/studio_rust_case.py`, and the browser suite via `CASTLE_STUDIO_CMD`); the flip of `make studio` is off-season work, when a regression costs nothing |
| 2026-09-01 | The flip: `make studio` starts the Rust one, Python is the fallback | The e2e matrix runs both servers on every push, so the twin is gated continuously rather than hoped about — the condition the 2026-08-27 row was waiting for. `tools/studio_launch.sh` (also `.claude/launch.json`) builds and execs `core/target/release/studio`, and falls back to `tools/studio.py` with a printed reason when there is no cargo; `CASTLE_STUDIO=rust\|python` names one outright. Nothing Python retires: it is still the reference every `tests/test_studio*_rust.py` suite measures against |

| 2026-09-01 | `make e2e` tests the Rust server by default | The browser suite was still defaulting to `tools/studio.py` the day after production stopped running it. `web/playwright.config.ts` now mirrors the launcher — the built binary when it exists, Python otherwise — and `make e2e` rebuilds the binary first, refusing to test a stale one. `CASTLE_STUDIO_CMD` pins either; CI still runs both axes |
| 2026-09-01 | The Python studio retires after the season, not before — and its answers are frozen now | [`docs/RETIREMENT.md`](../RETIREMENT.md): the server goes, the toolchain it spawns (generators, importer, sd_sync) stays. Retiring mid-season would delete the only measuring stick while the Rust server has run for one day; so the season is the soak test. What CAN be done early was: 39 golden fixtures (`tests/golden/`, read routes and every scene-splice refusal string) captured while Python is still the trusted reference, held against the Rust server by a test that never launches the Python one |
| 2026-09-01 | One firmware build — the SD one | The all-in-flash `castle_flash.yaml` could not hold the show once scenes 9–10 were real songs (3.2 MB of audio into a 1.75 MB OTA slot), had not compiled in a week, and had not been on the porch since 2026-08-22. CI only proof-read it, never cooked it. Deleted rather than nursed, on the operator's word that the card is not optional (§12.15). v5.43's binary is unchanged by the cleanup |

---

## 14. Roadmap and where things stand

### The 2026-08-10 roadmap, closed out

1. **TypeScript migration — done 2026-08-10** (`web/MIGRATION.md`, kept as
   the record). Every desk module is typed and under the cap.
2. **~~SD card streaming~~ — not possible**, and the whole-file path
   (`firmware/sd_audio.h`, whole file into PSRAM) shipped instead; since
   2026-09-01 it is the only build (§12.15).
3. **A cut-down cue desk served off the device — done.** `sd_sync site`
   pushes the lean page (90 KB gzipped) and per-scene mp3s to the card; the
   studio serves the same lean form. The full inlined page stays the
   portable artifact, governed by `tools/previewer_budget.py`.

### Where the project stands — 2026-09-01

Software, all committed and CI-green at `8e55f4e`:

- **The Rust studio is production** (`make studio`, `make e2e`); the Python
  one is the fallback and the parity reference until the season is over —
  [`docs/RETIREMENT.md`](../RETIREMENT.md), Phase 0 complete.
- **One firmware build**, `castle_sd.yaml`, v5.43, compiled at 88 % dram0
  (the alarm is 92 %). `make publish` and `make ota` rehearsed end to end
  against the emulator, so flash day is the board and nothing else.
- **The gates**: 994 Python tests, 76 Rust, 148 browser, 13 cross-language
  parity suites, 39 golden fixtures, the 500-line and dated-citation
  guards. The grade report 2026-09-01 (`.claude/grade-report.md`) is A−
  with every item closed.
- **The show**: 10 of the 12 scene slots this board can hold.

Waiting on hardware — the castle is off the network:

- OTA to **5.43**, confirm it on the panel, watch the first big upload for
  the 32 KB watchdog cadence, connect once so the image is confirmed, then
  `make publish` — the checklist is `firmware/pending/README.md`.
- The door-ring flicker bench tests, in the order
  [`docs/ISSUE-ring-flicker.md`](../ISSUE-ring-flicker.md) gives them; the
  second-RMT-block lever waits until those come back clean.
- The `bench*.yaml` variants lost their onboard-pixel cue display when they
  were rebased on the SD build (§12.15). Restore it only if a bare-board
  dry run is wanted again.

Standing work, reviewed 2026-09-01:

- `tools/synth.py` is 447 lines: split into voices and pieces when the next
  scene pushes it over, not before.
- ~~Verify the remapped pins with a meter~~ — the rig has run on them since
  2026-08-22; struck.
- Re-run the decode benchmark with pixels and amp drawing current (§12.13
  caveat) — still open, needs the board.
- Off-season: retirement phases 1–4; scene slots 11 and 12 when there are
  songs for them.
