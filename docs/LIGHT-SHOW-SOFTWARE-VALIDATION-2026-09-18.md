# Rich imported shows: local implementation and simulation

Follow-up to [the investigation](LIGHT-SHOW-BLANDNESS-INVESTIGATION-2026-09-18.md).
No physical castle requests, playback, uploads, settings changes, reboot, or
firmware deployment were performed during this implementation. All transfer and
playback rehearsals used a loopback emulator. Browser testing used muted audio;
the silent simulator left the audio element paused.

## Subsequent preview correction

The [option 1 experiment](LIGHT-SHOW-OPTION-1-EXPERIMENT-2026-09-18.md)
found that matching decoded cue data alone did not ensure matching flash
application: the desk accumulated hits while the card player replaced them.
Prepared preview and offline simulation now use card-specific playback.
Earlier frame-count and cue-execution checks remain valid as software checks,
but their old visual output should not be treated as exact card playback.

## What changed

- `demo/castle-radio/rich_show.py` reuses the original desk's scene recipe,
  section detector, pulse expansion, and binary cue encoder. It generates
  `<song>.cue` and `<song>.show.json` beside the local audio. Audio is neither
  changed nor re-encoded by show preparation.
- Unsplit songs use the desk's band choreography. Split songs retain vocals on
  the doorway and left/right backing on their respective towers; each band's
  color ramps, pixel masks, attack, and tempo-sensitive decay remain available.
  Cross-zone accent spill is disabled for those pinned stem routes so vocals
  cannot unexpectedly migrate onto the backing fixtures.
- Section changes use a fresh whole-song loudness analysis of the playback file.
  Separation is reused from the saved analysis, not rerun.
- New imports prepare these files automatically. Sync can prepare older imports
  locally if needed, then copies audio, preview, and cue files, verifying each
  transfer. The cue file is transferred last. A failed companion transfer does
  not report the job as complete.
- The webpage loads its **Prepared castle show** directly from a decoded copy
  of the binary cue file. Integer/scaled field rounding therefore matches the
  exported data. A cue CRC identifies the preview and prevents a mismatched
  preview from being accepted.
- **Simulate lights silently** advances the show without playing audio. It
  supports scrubbing, pause, and stop. The castle drawing and individual LED
  view are visible beside the controls on desktop and stacked on mobile.
- **Routing / style experiment** remains explicitly separate. Those existing
  browser-only controls do not overwrite the prepared show's rendering or
  silently modify its exported choreography.
- Sync readiness now recognizes companion cues for imported songs, rather than
  treating every import as audio-only. Legacy reduced streaming remains the
  fallback for songs without an installed native cue file.
- Removing/restoring a local import includes its cue and preview companions.

## Real-song measurements

These are the locally available split imports from the investigation. The
original catalog and audio were preserved; their new companions remain in the
ignored `.radio-data/tracks/` library.

| Measurement | Monster Mash | Day-o |
| --- | ---: | ---: |
| Previous reduced solid-color updates | 715 | 352 |
| Prepared cue records | 4,459 | 1,563 |
| Strikes | 4,447 | 1,530 |
| Section effect/level changes | 12 | 33 |
| Encoded strike colors | 436 | 330 |
| Cue-file bytes | 71,384 | 25,048 |
| Complete 16 ms browser frames simulated | 11,621 | 7,352 |
| Distinct sampled pixel patterns, every 160 ms | 1,162 | 736 |

Counts are not a perceptual quality score. Rich generation intentionally gates
some hits during quiet sections and excludes the final 100 ms, so its strike
count need not equal the raw analysis count. Simultaneous records within a
single zone can also supersede one another in a render tick; separate-zone
events are no longer globally reduced to one quarter-second command.

## Software evidence

1. **Actual firmware C++ reader:** compiled `tests/cxx/cues_check.cpp` and ran
   both complete generated cue files through `firmware/castle_cues.h`. Every
   emitted state line matched `test_cue_file_cxx.trace` from the decoded file.
   This verifies record loading, timing, target masks, colors, levels, effects,
   attack, decay, and end-of-file handling, without an ESP32.
2. **Whole-song browser engine:** the actual shipped `visuals.js` renderer
   simulated 18,973 frames total. Every cue executed, all RGB pixel values were
   finite and between zero and one, and the sampled pixel patterns varied
   throughout both songs. This is reproducible with the command below.
3. **Real uploader to local emulator:** both songs' audio, `.cue`, and preview
   JSON matched the local files byte-for-byte after upload. Inventory reported
   ready; simulated playback reported 4,459 and 1,563 loaded cues respectively.
4. **Webpage integration:** Monster Mash loaded all 4,459 prepared cues. Forward
   seek to 45 seconds and backward seek to 5 seconds rebuilt the timeline
   correctly; Stop ended silent simulation. The audio element stayed paused.
5. **Responsive visual check:** desktop castle and LED views displayed the
   prepared show. At 390 × 844, the canvas remained visible and the page had no
   horizontal overflow.
6. **Regression tests:** `test_rich_show.py` covers dense simultaneous hits,
   color/pixel variation, preserved stem routing, exact decoded previews,
   unchanged audio, invalidation after audio replacement, missing stem analysis,
   failed companion upload, and a real upload/playback through the local emulator.

Reproduce the silent whole-song renderer check:

```sh
node demo/castle-radio/simulate_show.mjs \
  demo/castle-radio/.radio-data/tracks/radio_a1f0fc4d6545.show.json \
  demo/castle-radio/.radio-data/tracks/radio_4965453bf420.show.json
```

Run an isolated preview server that cannot address the physical castle:

```sh
CASTLE_RADIO_HOST=127.0.0.1:9 .venv/bin/python demo/castle-radio/server.py 8878
```

Open `http://127.0.0.1:8878`, choose Monster Mash or Day-o, keep output set to
**This computer**, and choose **Prepared castle show → Simulate lights silently**.
The offline castle indicator is intentional. No sync is needed for local preview.
To select a song without starting audio, use **Import music → Preview show**.

Final checks: `make check` passed (1,193 main Python tests, Radio tests, lint,
type checks, image/size guards, and the existing browser/C++ rendering parity
and simulation suites). The final focused Radio run passed 103 Python and 83
JavaScript tests; `make coverage-radio` passed at 72%, above its 67% floor.
The first broad run's two failures came from a localhost-only environment
override conflicting with mocked launcher/hermetic-environment expectations;
both passed in isolation and the full rerun passed. No test was disabled.
The unrelated original desk's full Playwright suite was not run; this change's
Radio page was exercised directly in a muted browser and its controller now
has automated tests in `test_rich_preview.test.mjs`.

## What still requires the user's physical test

Physical brightness, fixture geometry matching the browser configuration,
speaker-to-light latency on the actual board, and subjective musical quality
remain unverified. The software checks do not simulate electrical output,
SPI/RMT scheduling, real SD-card latency, or the room's appearance.

The existing style/palette/per-pixel routing experiments are still preview-only;
exporting arbitrary changes from those controls is separate work. The prepared
show uses the rich desk recipe and the saved split routing described above.
Native playback requires firmware with companion-cue support (v5.63+); the
previous investigation observed v5.70, but no new device check was made here.

The updated webpage and song companions have **not** been published to the
physical device. That is deliberately left for the later hardware test.
