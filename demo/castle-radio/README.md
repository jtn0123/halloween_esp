# Castle Radio concept

Responsive music-player prototype with real importing, voice/background
separation, and a bounded bridge to the porch castle.

## Run

Use the project's Python environment with ffmpeg, yt-dlp, Demucs, and PyTorch.
The existing importer also requires the Rust analyze_track binary (built on
first use when needed). For this worktree, the main checkout's environment is:

```sh
/Users/justin/Documents/Github/halloween_esp/.venv/bin/python \
  demo/castle-radio/server.py
```

Open http://127.0.0.1:8871. Optional first argument changes the port.
The previous static `http.server` command supports playback only; use server.py
for importing. Only one server can bind the same port.

Audio files in `media/` are ignored local copies of the ten numbered rendered
scene MP3 files (01 through 10) from the main checkout's `audio/` directory.

## Working interactions

- Browser audio, seek, volume, pause/resume, stop, previous, next.
- Continuous queue, shuffle, repeat, add/remove/reorder upcoming songs.
- Search and song/atmosphere filters, mobile queue and volume controls.
- File and URL importing through the existing tools/import_track.py.
- Automatic onset analysis through the existing Rust analyzer.
- Optional voice/background separation (default on) through tools/stems.py.
- Separate voice, backing, and original audio previews.
- Automatic browser light cues: vocals drive the door; backing left/right
  drives the respective towers. Without separation, low/mid/high bands drive
  door/left/right. Density tuning reuses tools/import_scene.py.
- Background jobs, errors and retry; imports persist across page/server reloads.
- Locally saved style/run preferences and blackout for all demo audio/lights.
- Physical playback for synced imports, with generated cue frames streamed to
  firmware 5.51 or newer while the castle reads audio from its SD card.
- The original LED-channel and speaker diagnostic bench under Your castle.
- OLED night and castle-green themes, both saved in the browser.

## Isolation and limitations

All imported audio, sources, analysis, generated recipes, and catalog data go in
ignored `.radio-data/`. The device bridge is limited to the configured private
castle address and explicit playback, lighting, test, sync, and cleanup actions.
The local server only binds 127.0.0.1. Upload limit: 100 MB. Jobs run one at a time.
Closing the page does not cancel preparation; quitting the server interrupts it.
In-memory job history/retry disappears on server restart; completed songs persist.

Built-in tracks retain illustrative lighting. New imports use real analyzed cues
with a simplified browser renderer, not production firmware parity. Standard
unsplit scene recipes are saved alongside the split-aware browser cue catalog.
No firmware, SD transfer, production scene edits, or device-owned playback is
implemented. Audio still plays on this computer and stops if the page closes.
Motion controls remain conceptual preferences. Link support follows yt-dlp;
login-protected or unsupported sources may fail with a visible error.

## Shared audio, castle preview, waveforms, and removal

The main player, preview transport, waveform drag, and both scrubbers now share
one audio element. Changing Full song / Voice / Background preserves position
and playing/paused state. The selected layer also controls which castle zones
are shown (voice: door; backing: towers). Stop and blackout stop the same clock.

`visuals.ts` bundles the existing Stage, show/effect engine, and stems_draw
modules directly from web/src. Built-in songs use their generated scene data
in scenes.json, extracted from the existing built previewer. Imported songs
adapt their analyzed cues to the same show engine. This supersedes the earlier
illustrative CSS castle and simplified pulse renderer described above.
The adaptation and user brightness/pause controls remain demo behavior, not a
claim that imported scenes have been deployed or physically verified.

Waveforms use the existing split analysis (peaks and onset markers). Unsplit
and built-in audio is analyzed on demand with the existing tools/stems.py
analysis helpers and cached in .radio-data/waveforms/.

Remove works in Listen and in the imported-song list. Imported demo files move
to .radio-data/trash/ and can be restored with Undo; removal does not reclaim
that disk space. Built-in samples are hidden locally, preserving the bundled
files. The physical castle and original project library are unaffected.

Rebuild the renderer bundle after changing its source modules:

```sh
/path/to/main-checkout/web/node_modules/.bin/esbuild \
  demo/castle-radio/visuals.ts --bundle --minify --format=iife \
  --global-name=CastleVisuals --outfile=demo/castle-radio/visuals.js
```

Removal regressions: run `python -m unittest discover -s demo/castle-radio
-p 'test_*.py'` using the project environment.

## Fixtures, audio routing, and measured preparation progress

Your castle now reuses FIXTURES/loadRig/zoneLayout from the existing rig module.
Pick a Ring 12/16, Jewel 7, Stick 8, matrix, singles, or empty channel. The Stage
renderer now uses actual fixture positions instead of always drawing seven
Jewels. The original PixelInsets view displays every configured LED as well.

For imported tracks, route voice/backing/full-song analysis from left, right,
or their mix to each channel. Individual numbered LEDs can override the channel
source. A Jewel center is LED 1; a ring has no physical center LED. The two-tower
preset routes vocal-left/right to the corresponding tower channels. Fixture and
routing choices persist in browser storage. Built-in scenes retain their original
authored cues; fixture geometry applies to both imported and built-in songs.

Routing evaluates real per-layer/per-channel onset data and waveforms, retaining
relative left/right level differences. When a source is missing (e.g. vocals on
an unsplit track), its assigned pixels stay dark and the preview explains why.
The adaptive mapping is a browser preview and is not yet emitted as matching
production firmware or published to the physical device.

Demo jobs opt into CASTLE_PROGRESS_STREAM in import_fetch.py and stems.py.
Download and Demucs percentages come from the tools' live output. Encoding is
indeterminate; stem analysis counts nine completed layer/channel combinations.
The upload itself has browser-reported progress. Percentages describe the current
stage, not a guessed total-job percentage. Completed elapsed times stop counting.
The non-demo importer/splitter paths keep their existing subprocess behavior.
