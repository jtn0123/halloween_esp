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
