# Castle Radio concept

Responsive music-player prototype with real importing, voice/background
separation, and a bounded bridge to the porch castle.

## Desktop setup and everyday startup

On an Apple Silicon Mac, install [Homebrew](https://brew.sh) if it is not already available,
then run this once from the project root:

```sh
./tools/install_castle_tools.sh
```

The installer prepares a dedicated `.venv-desktop`, system audio tools, the
Rust audio analyzer, and the Demucs model. Run it again explicitly to repair
missing dependencies. It does not flash the castle or sync the music library.

For everyday use, click **Start Mac tools** on the castle website and allow the
browser to open Castle Tools. Then choose **Connect Mac tools**. The existing
script runs in the background without a Terminal window. Keep the small
connection window open. Imports, separation, and previews stay on the website.

Existing installations can enable this once by double-clicking **Enable Website
Startup.command** in the project folder. The full installer also registers it.
Registration compiles a small native URL handler into `~/Applications/Castle
Tools.app` using Apple's Command Line Tools. It accepts only the fixed
`castle-tools://start` action; no URL-supplied shell command, path, or host is
executed. It remembers this checkout's location, so register again after moving
it. It does not install audio dependencies or add a login item.

The **♜** menu-bar icon offers Start, Open startup log, and Quit. Logs are at
`~/Library/Logs/Castle Tools/startup.log`. Quitting stops a helper started by
this app; a helper already running elsewhere is reused and left alone.
**Open Castle Studio.command** remains a manual fallback.

The connection uses a narrowly scoped popup message bridge because the castle's
HTTP page deliberately blocks external fetches and media. Audio previews arrive
as local blob URLs; the existing firmware security policy is unchanged. Both
ends verify the other window and its exact origin, and the helper verifies that
its configured castle matches the page. Physical playback and lighting commands
stay on the castle's own API. Sync uses the Mac's existing verified SD uploader.

If the helper or popup closes, imports are disabled and the page offers
**Connect Mac tools** again. Installed castle playback still works. A phone
without a local helper retains device controls. This does not yet connect a
phone to another computer's helper over the LAN.

The launcher never installs or upgrades packages or downloads the voice model.
A missing optional splitter still allows ordinary imports and playback. Run
the installer explicitly for repairs, then restart the launcher. The manual fallback launcher
can be stopped with Ctrl-C. A second launch reuses the running helper.
Set `CASTLE_RADIO_HOST` before launching to use another castle; the same address
must be used in the browser. The helper remains bound to loopback only.

The direct desktop site is still available at http://127.0.0.1:8871 for standalone
use. **Castle Cue Desk.command** opens the older desk on port 8765.

For development, the server can still be run directly:

```sh
.venv/bin/python demo/castle-radio/server.py
```

An optional first argument changes its port. A static `http.server` only
supports playback. The launcher copies missing numbered sample MP3s from the
project's existing `audio/` outputs into ignored `media/`; it preserves existing
copies. Sample audio is local and is not distributed with the repository.

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
- One shared castle link (device-link.js): a single status poll a second feeds
  the header chip, the player, the bench and the motion settings; it slows to
  every 4 s in a background tab and the server answers all of them from one
  cached `/api/status` per quarter second.
- Firmware 5.52 reports `playing` and `position_ms` (sound-true since 5.55:
  0 until the speaker runs), so the scrubber follows the
  castle's own clock, a raw file clears `track` when it ends, and the queue
  moves on by itself (songs that are not synced are skipped with a toast).
- Play on the castle is a stop button while something plays; a command that has
  not landed yet is shown as "starting" instead of flipping back to the old song.
- Firmware 5.59 counts the LIGHT frames it actually drew (`light_applied`) and
  the ones overwritten before the drain (`light_evicted`). A running generated
  show is reported as "N landed of M sent" against the counts the castle held
  when the show started, and names what was overwritten; older firmware reports
  neither number, which is unknown rather than zero, and keeps the old wording.
- Your castle carries a link-health line — last round trip, the worst of the
  last ten, missed polls, castle uptime and firmware — and the bench can ask
  the castle for its own record of what it did (`/api/events`, 5.59 or newer).
- The link survives a rough minute: one dropped poll keeps the last good state
  on screen and says "reconnecting", and only three in a row read as offline.
  An `uptime_s` that goes backwards is an OTA or a brownout, not the end of a
  song — the follower resets, says the castle is starting up while its scene
  table is empty, and starts the interrupted track again. A `/api/files`
  listing that has not answered yet makes the queue wait, never skip.
- Generated lights respect the firmware's one-command-per-200 ms drain: frames
  are never sent faster than that, a frame the next one has already overtaken
  is dropped rather than burst, a castle that never reports `position_ms` has
  its frame clock re-based on now, and every stop waits for its light-off to be
  drained before STOP can evict it. The show holds a screen wake lock and
  re-aligns when a throttled tab comes back.
- Motion arming and cooldown are sent to the castle and read back from it.
- Phone layout: sticky top bar, two-row header, 40 px controls, safe-area
  aware player bar; audited at 390 x 844 with no horizontal overflow.
- The original LED-channel and speaker diagnostic bench under Your castle.
- OLED night and castle-green themes, both saved in the browser.

## On the castle itself

`make publish` (or `tools/sd_sync.py site`) pushes this control room to the
castle's SD card as ONE self-contained page, and http://10.27.27.81/ serves
it: 68 KB gzipped, about 1.5 s to first paint over the porch Wi-Fi.
`device_site.py` builds it; `castle-direct.js`, inlined first, answers every
`/radio/*` route from the firmware's own `/api` (status settling, the command
builders, the SD inventory and the generated-light streamer are ports of
`device_bridge.py` with its light-show runner
`light_show.py`, and `remote_library.py`). Scene audio streams from
`/sd/scenes/`; synced imports from the card root, with their lights reduced
to mailbox-rate frames at build time and streamed by the phone's browser on
the castle's own clock. Importing, separation, waveforms and syncing run on the computer.
**Connect Mac tools** makes them available from this same device page through
the local companion window; without that connection the page remains a device
player. The castle
keeps the previous cue desk build as `site/index.old.html(.gz)`.
Firmware serves `index.html.gz` in preference to the plain file, so a card
copy that only updates `index.html` leaves the previous gzipped page in
place — push the `.gz` (as `sd_sync site` does) or delete the stale gzip.

Tests: `python -m unittest test_device_site` in this directory, and
`tests/test_sd_sync.py` for the push.

## What the server requires of a request

The server binds loopback, but loopback is not a wall a browser respects: any
page open in the same browser can post to `127.0.0.1:8871`. So every
state-changing route is shaped so a browser has to ask permission first, and
this server answers no `OPTIONS` at all. JSON routes (`/radio/device/command`,
`/radio/device/sync`, `/radio/retry`, `/radio/cancel`, `/radio/rename`,
`/radio/reprocess`, link imports) require
`Content-Type: application/json` — `; charset=…` is fine — and answer 415
otherwise; `application/json` is not a content type a cross-origin form can
send without a preflight. The raw upload (`/radio/import` with audio bytes) and
the bodiless `/radio/restore/<key>` require `X-Castle: 1` and answer 403
without it: any custom header forces the same preflight, and `X-Filename` could
not do the job because the route falls back to a default name. The page's own
scripts and the companion bridge send both (`companion.js` allow-lists the
header). Preparation queues at most eight unfinished jobs; the ninth is refused
with 429 rather than parked on disk. This is not authentication and is not
meant as any: it only means a foreign origin cannot reach these routes with a
"simple" request (grade report 2026-09-17 E2).

## Isolation and limitations

All imported audio, sources, analysis, generated recipes, and catalog data go in
ignored `.radio-data/`. The device bridge is limited to the configured private
castle address and explicit playback, lighting, test, sync, and cleanup actions.
Set `CASTLE_RADIO_HOST` to point the bridge at another castle (default 10.27.27.81).
The local server only binds 127.0.0.1. Upload limit: 100 MB. Jobs run one at a time.
Closing the page does not cancel preparation; quitting the server interrupts it.
In-memory job history/retry disappears on server restart; completed songs persist.

Built-in tracks retain illustrative lighting. New imports use real analyzed cues
with a simplified browser renderer, not production firmware parity. Standard
unsplit scene recipes are saved alongside the split-aware browser cue catalog.
Production scene edits are not implemented. Computer playback stops if the
page closes; castle playback continues, and the queue only advances while this
page is open. Pause and seek are not supported by the castle firmware. "While
music is playing" remains a preview preference. Link support follows yt-dlp;
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
