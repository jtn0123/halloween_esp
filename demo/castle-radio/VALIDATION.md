# Import integration validation

Verified locally on 2026-09-14 with the isolated demo server.

- Actual MP3 file import through existing import_track.py and Rust analysis.
- Actual Demucs voice/background separation; 264 generated browser cues for
  the Vigil sample. All three layer previews played in the browser.
- URL import through yt-dlp using a local HTTP audio fixture; generated 92 cues
  for Storm. External provider availability was not tested.
- Browser file-picker submission produced a completed import job.
- Imported audio playback and seeking; 206 response with correct byte length.
- Blackout silences both the player and the separate layer preview.
- Invalid URL returns 400. Corrupt audio produces a visible failed job.
- Retry through the UI succeeds after repairing the isolated test source.
- Completed imported catalog and cues survive page and server reloads.
- Mobile import screen checked at 390 x 844 with no horizontal overflow.
- 36 existing tests pass: tests.test_import_scene, tests.test_import,
  tests.test_stems. Ruff passes for server.py; both JS files pass node --check.

The demo uses detected timing and existing density tuning in a simplified
renderer. No claim of firmware parity or physical-device validation is made.
No production library, production scenes, SD contents, or firmware were changed.

## Shared-preview follow-up

Before fix, the bottom scrubber changed the main audio but left the backing
player at 8 seconds; changing to Voice reset that second player to zero.

After fix, real browser checks confirmed:

- Playing layer switch retains 8-second position and continues playback.
- Main scrubber seeks the backing track to 15.033 seconds (half the fixture).
- Paused switch to Voice retains 15.033 seconds and remains paused.
- Rapid layer changes settle on the last chosen source without resetting time.
- Actual mouse drag on the background waveform seeks to 19.133 seconds;
  the fixed-step light clock follows at 19.120 seconds.
- Rewinding and returning reproduces the light clock at the scrubbed position.
- All three waveform rows use real saved analysis; both pages share the panel.
- Removal clears the selected song from playback and queue, returns 404 for
  its removed audio, and Undo restores original audio and separated layers.
- Three removal regression tests pass, including unrelated-file preservation
  and refusal to overwrite a newer file on restore.
- Layout checked at 390px and 1440px with no horizontal overflow.

Original Stage, show/effect, and waveform renderers now replace the earlier
illustrative renderers. Device publishing remains unconnected.

## Fixture/routing/progress follow-up

- Browser checks: Ring 12 has 12 pixels/no center; Ring 16 updates both picker
  dots and rendered output to 16. Jewel LED 1 overrides only the center pixel.
- Voice-to-both-towers preset selects vocals:left and vocals:right independently.
- Real imported track check: backing-left had 2046 hits, backing-right 1988;
  routing read independent timing arrays and their relative channel levels.
- Live Demucs job observed 0, 17, 33, 50, 67, 100 percent; then encoding;
  then 1/9 through 8/9 channel analyses before Ready. No fake total-job percent.
- 36 existing importer/stem tests and nine demo regressions passed, including
  progress parsing, real child-output streaming, timeout handling, and Undo.
- TypeScript typecheck passes with the updated Stage fixture geometry.
- Fixture controls fit the 390px mobile viewport without horizontal overflow.

## Physical playback bridge and remote preview (2026-09-14)
- Reproduced missing `/device-link.js` and `/radio/device` (404): selector had no device transport implementation and server was stale.
- Added explicit device scene/stop/blackout/volume/installed-playlist bridge. Browser Play for This Is Halloween changed physical `/api/status` scene and track; local audio stayed paused. Stop returned scene to stop.
- Remote status selects the matching built-in song, advances progress/playheads and authored LED simulation from a shared backend estimated clock. Browser observed remote Ballad, progress advanced from 0:09 to 0:44 with local audio paused.
- Firmware 5.50 exposes no playback position: timing is estimated from command or first observation. The latter cannot recover a song already in progress. Preview is not device pixel telemetry. Actual seek/pause, custom automatic queue, and newly imported show deployment remain unsupported and are identified in the interface.
- 15 Python tests pass, including scene allowlist, unsupported commands, volume bounds, elapsed clock, same-scene restart, external scene change, stop. JS syntax and Ruff checks pass.

## Remote inventory and audio sync (2026-09-14)
- Per-song status reads the physical SD root/scenes directories and installed firmware scene IDs. Built-in readiness requires both scene audio and compiled scene; imported audio-only status is explicitly separate.
- Unsynced imported Play opens a sync dialog. Library/import rows also have Sync actions. Your castle lists additional remote audio files outside the demo library, including existing Ghostbusters audio under a different filename.
- Live transfer of `radio_d0183bda099a.mp3` (Storm link import), 158031 bytes, completed with existing uploader byte/CRC verification. Remote inventory changed from missing to audio-only. This added one file and did not start playback or deploy firmware.
- Checked mobile dialog at 390x844. Missing Ghostbusters demo copy opens the appropriate dialog; completed Storm sync disables redundant transfer. Failed sync offers retry.
- 19 Python tests pass; includes missing scene audio, audio-only readiness, rejected path escape, and CRC failure reporting. JS syntax, Ruff, and whitespace checks pass.
- Full generated-light deployment remains a firmware capability gap; audio upload is not labeled a complete show deployment.

## Sync timeout and measured progress (2026-09-14)

- Root cause: the castle serves an upload on its control HTTP task, so concurrent
  inventory/status polls waited behind a large transfer and timed out even when
  the upload ultimately succeeded. Monster Mash was confirmed on the SD card at
  4,463,847 bytes after the reported timeout.
- Device inventory/status polling now pauses during a transfer. A local-only
  sync-status endpoint remains responsive and reports bytes sent, total bytes,
  percentage, upload phase, verification phase, completion, and errors.
- The uploader now streams 32 KB chunks and checks the device-reported byte
  count and CRC before showing completion.
- Physical verification: syncing the 722,276-byte Vigil split preview displayed
  576 KB of 705 KB (82%) during transfer, then displayed `Transfer complete ·
  byte count and CRC verified`. The SD inventory contains the exact 722,276-byte
  file.
- 20 Python tests pass, including chunked measured progress and CRC rejection.
  Ruff, JavaScript syntax, and whitespace checks pass.

## S3 playback formats (2026-09-14)

- Run settings offers MP3 at 96 kbps/44.1 kHz, Ogg Opus at 64 kbps/48 kHz,
  and 16-bit stereo PCM WAV at 44.1 kHz. FLAC is not offered or compiled.
- The chosen format persists across a browser reload and is passed to both link
  and file imports. Existing imports keep their original format.
- Real encoding of the same 6.5-second stereo source produced a 50,839-byte
  Opus file and a 1,152,078-byte WAV file; light-cue analysis completed for
  both outputs.
- Desktop and 390 x 844 mobile layouts were checked in the shared preview. The
  format cards stack into one column on mobile without horizontal overflow.
- The S3 firmware compiles with MP3, Opus, and WAV decoders. The resulting OTA
  image is 1,282,608 bytes. Codec additions are scoped to the Feather S3 target
  so the older S2 build does not pay their flash cost.
- 25 Python tests pass, including format validation, persistence plumbing,
  dynamic library paths, removal/restore, remote inventory, transfer progress,
  and CRC checks. Ruff, JavaScript syntax, and whitespace checks pass.
- The physical castle now runs confirmed firmware 5.51. MP3 playback and live
  imported-light updates are confirmed; Opus and WAV remain compile-validated.

## Imported playback, saved sources, and SD cleanup (2026-09-14)

- Root cause for Monster Mash: imported tracks always resolved to no installed
  scene. The sync dialog then disabled its only action because the audio was
  already present. A raw SD-file playback path now handles synced imports.
- Physical check: selecting Monster Mash with the castle output sent
  `radio_531d0eb77655.mp3` to `/api/play`; firmware status reported that exact
  track and the browser timer/light preview advanced. The castle was stopped
  after the check.
- A transient inventory outage no longer clears last-known per-song status.
  With the server intentionally stopped for one poll cycle, Monster Mash kept
  `On castle · audio only` instead of changing to an unknown state.
- Existing importer manifests restore the original source into the demo
  catalog. Monster Mash now shows its saved YouTube link. Uploaded originals
  remain in the isolated `_src` directory. `Change audio` can re-run codec,
  quality, and voice-separation work without asking for the source again.
- Quality presets are codec-specific: Standard is MP3 96 / Opus 64 kbps, High
  is MP3 160 / Opus 96 kbps, and Maximum is MP3 192 / Opus 128 kbps. WAV stays
  16-bit stereo PCM, so its quality control is disabled.
- Other SD audio now shows measured sizes and a guarded Delete button. A live
  23-byte probe was uploaded, appeared with its size, deleted through the demo
  endpoint, and confirmed absent afterward. Existing user audio was untouched.
- 34 demo regression tests pass, including synced imported-file dispatch,
  missing-file refusal, raw-track timing, retained source links, reprocessing
  options, codec quality, remote sizes, deletion validation, transfer progress,
  and CRC checks. Mobile checks at 390 x 844 show no horizontal overflow.

## Imported physical lights and hardware bench (2026-09-14)

- Root cause: firmware 5.50 stopped the media player for every manual light
  frame, including generated frames sent during raw imported playback.
- Firmware 5.51 preserves raw SD playback while live light frames update the
  three physical zones; authored scene overrides still stop their scene first.
- Monster Mash remained reported as the active SD track while the generated
  light counter advanced from 1 to 49 of 714 bounded frames.
- Your castle now includes per-zone RGBW/off controls, RGB bars, chase, ends,
  custom color and brightness, plus sweep/1 kHz/200 Hz/4 kHz/silence tests.
- At 390 x 844 and 1440 x 900, the bench has no horizontal overflow. OLED night
  resolves the page background to true black and persists through reloads.

## Castle sync overhaul and firmware 5.52 (2026-09-15)

- Root causes found on the live board: (1) firmware never reported whether
  audio was playing or how far in, so the page counted from its own click and
  a raw file "played" forever; (2) a poll fired right after a command still
  named the previous song and the page flipped back to it; (3) `/api/play`
  left `current_scene` on the halted scene, so the first streamed light frame
  ran `scene_stop` and silenced the imported song; (4) three pollers and an
  inventory sweep asked the four-socket castle httpd for status separately.
- Firmware 5.52 mirrors the speaker pipeline state into `/api/status` as
  `playing` and keeps a main-loop clock as `position_ms`; play and scene
  commands restart it; a raw file publishes `scene:"stop"` and clears `track`
  the tick its audio ends. The emulator and the C host harness answer the same
  bytes (66 parity, contract and emulator tests pass).
- Bridge: one cached `/api/status` per 250 ms, a 2.5 s settling window that
  reports the requested song until the castle agrees, and light frames aligned
  to `position_ms` (first frame waits for the mailbox tick and for decoding).
- Page: one shared poll, header chip, castle clock on both scrubbers, stop
  instead of restart, previous/next/shuffle/repeat on the castle, automatic
  queue advance that skips unsynced songs, motion arm/cooldown round-trip,
  castle volume reflected, friendlier outage messages.
- Live board (Feather S3 in v3.3a, OTA to 5.52 verified by `/api/status`):
  Storm (6.5 s) played from the page, the clock matched `position_ms`, the
  castle went idle at the end and Descent started by itself; Stop pressed
  mid-song stayed stopped; the 6.5 s "Storm · link import" played from the SD
  card with 24 of 26 generated frames sent by 5.9 s, cleared at the end, the
  unsynced Ghostbusters import was skipped with a toast and "Vigil · split
  preview" started with its own 98 frames; arming motion from Run settings
  read back `armed:true` from the castle and disarming read back `false`.
  All checks ran at castle volume 0 and the volume was restored to 45.
- 50 demo unit tests pass (status caching, settling, castle clock, frame
  alignment, mailbox tolerance). Ruff and JavaScript syntax checks pass.

## Sonar gate pass (2026-09-15)

- The gate on this PR reports Security E and Reliability E, and the project is
  private, so the only readable output is the 50 GitHub annotations. Sonar's
  own JavaScript rules run locally instead (docs/ISSUE-sonar.md recipe, with
  the demo scripts copied in): 37 "unenclosed multiline block" bugs and 10
  implicit-global bugs, all in the demo scripts.
- Every conditional body is braced now (ESLint `curly` auto-fix) and each
  script declares the cross-script variables it assigns with a `global`
  comment; the two swallowed exceptions carry a reason and the shuffle's
  `Math.random` is annotated. Sonar's JS rules now report maintainability
  findings only.
- Taint: nothing the browser sent is forwarded to the castle or the
  filesystem any more. Scene ids, file names and diagnostic tones are the
  castle's own spelling from its status or listing; light specs are rebuilt
  from known zone and pattern tokens and integers; volume, cooldown and
  armed are integers; served media is matched against the library
  directory's own listing; upload suffixes come from a table; library keys
  resolve through the catalog or the media directory before a path is built.
- Live castle after the rewrite: a bench light spec, Storm, a synced import
  with generated lights (5 frames by 3 s), Stop, a ranged media fetch (206)
  and a traversal attempt (404) all behaved. 51 demo tests pass.

## Phone layout pass (2026-09-15)

- Audited every view at 390 x 844 with a touch context: no horizontal
  overflow on any page, but the header crammed breadcrumb, castle chip and
  output picker into one row, the top bar scrolled away on the 6,400 px Your
  castle page, and a dozen controls were under 32 px (row actions 17 px wide,
  filter chips, preview Stop, LED dots, transport buttons, the output select).
- The top bar is sticky with a bottom-edge selected indicator; the header is
  two rows (breadcrumb and chip, then a full-width output picker); pages carry
  a scroll margin so anchors land below the bar; the player bar and toast
  respect the home-indicator safe area (`viewport-fit=cover`).
- Every button, chip, select and row action is at least 40 px tall on phones;
  the collection row fits sync, add and remove at 44/38/38 px; prepared songs
  show art beside the title with a two-column button grid; LED dots are 36 px;
  the LED bench grid uses 40 px cells; hover styles are off on touch devices.
- Re-audited after the change: the only elements under 32 px are range inputs
  (30 px hit area), checkboxes (22 to 24 px inside 40 px labels) and inline
  links. Screenshots of Listen, the collection, the sync dialog, castle mode
  and prepared songs were checked by eye.
