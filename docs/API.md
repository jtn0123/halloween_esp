# The cue desk's HTTP contract

Three parties: the **desk** (the page, `web/src/`), the **studio**
(castle-core's `studio` bin, `core/src/bin/studio.rs` plus
`core/src/studio*.rs` — the local server behind the desk, and what
`make studio` runs), and the **castle** (`firmware/sd_web.h`, or
`tools/castle_emu.py` standing in for it). There was a second studio, in
Python, answering the same two tables; docs/RETIREMENT.md retired it, and
what holds this surface steady now is `tests/golden/` (the recorded
answers, replayed by `tests/test_studio_golden.py`), the black-box suites
`tests/test_studio_*_rs.py`, and the browser suite. The prefix says who
owns a route:

- `/studio/…` — the studio's own authoring routes (`web/src/api.ts`).
- `/api/…` — the castle's. The studio relays these untouched
  (`tools/castle_link.py`); no castle in reach → 502. `device.ts` owns them.
- `/api/status` is the one shared path: the castle's status when one answers,
  else the studio's own `{"studio": true, "castle": "<host>"}` — the desk's
  mode probe; `castle` (v5.42) names the configured host so the desk can say
  WHO is not answering, and is absent when none is configured.

Every body is JSON unless noted. Failures carry `error` (and `reason`, one
line, on tool failures). An unknown `/studio/*` path is a 404; an unknown
`/api/*` path is refused by the relay's allowlist (404 with the known routes
in the body, `castle_link.KNOWN_API`) — a client typo no longer reads as a
castle outage.

## Studio-owned (`/studio/…`)

| Method | Path | Does |
|---|---|---|
| GET | `/`, `/index.html` | the built previewer page, **lean**: the inlined scene audio is rewritten to `/studio/scene-audio/<id>` links at serve time (always — loopback included; `previewer/castle-cue-desk.html` on disk stays the portable inlined build) |
| GET | `/studio/scene-audio/<id>` | a scene's rendered mp3, from the audio/ the served page was built from (Range honoured) |
| GET | `/studio/tracks` | `{tracks: [...], scenes: [ids]}` — the library |
| DELETE | `/studio/tracks/<id>[?scene=1]` | remove a track (`?scene=1`: its scene too, then rebuild) |
| POST | `/studio/import` | `{url}` JSON, or multipart file + `X-Import-Opts` — blocking import |
| POST | `/studio/import/async` | `{url, …opts}` → a job |
| GET | `/studio/job/<id>` | job progress; the track list rides on the last poll |
| POST | `/studio/refresh` | `{id, …opts}` — re-import from the remembered source |
| GET | `/studio/track/<id>[.ext]` | stream a track (Range honoured) |
| GET | `/studio/waveform/<id>[?sensitivity=…]` | peaks + onsets for the clip editor |
| POST | `/studio/stems` | `{id, force?}` — Demucs split as a job |
| GET | `/studio/stems/<id>` | the cached stems analysis (404 = not split) |
| GET | `/studio/stem/<id>/<layer>` | a stem mp3 (`vocals` / `backing`) |
| POST | `/studio/compare` | `{id, …encode opts}` — codec A/B renders |
| GET | `/studio/compare/<token>/<codec>` | one A/B render |
| POST | `/studio/probe` | `{url}` — yt-dlp title/duration (400 on a bad link) |
| POST | `/studio/scene` | `{id, yaml}` — splice a scene into scenes.yaml, then rebuild; `scene_schema` rejects a malformed block with 400 `{errors: [...]}` |
| POST | `/studio/rebuild` | render audio → gen_esphome → gen_previewer → **publish** (when a castle answers: the same push as `/studio/publish`, its result in the log) |
| POST | `/studio/publish` | push the show + the lean page to the card (`sd_sync scenes` — audio, `<id>.cue`, `show.man` — then `site`); answers `{needs_reboot: [ids], note}` for scenes the running castle has not read yet — since v5.67 those need a reboot, not an OTA |
| POST | `/studio/server/stop`, `/studio/server/restart` | answer, then do it |
| GET | `/studio/card/<name>` | pull a file off the castle's card (relays `/sd/<name>`, name-stripped) |

**Aliases, one release only (v5.24):** each of these also answers at its old
`/api/…` spelling (`/api/tracks`, `/api/import`, … `/api/card/<name>`); the
studio logs `DEPRECATED` once per route. The one exception is `/api/scene`:
with `?s=<id>` it is the castle's fire-a-scene and relays; with a JSON body
it is the editor above. The table is `STUDIO_ROUTES` in
`core/src/studio_alias.rs`, which is the whole shim and goes with the
aliases when they do.

## Relayed to the castle (`/api/…`, `/remote`)

| Method | Path | Does (firmware `sd_web.h`) |
|---|---|---|
| GET | `/api/status`, `/api/health`, `/api/bootlog` | status (studio answers when no castle), health, boot log. v5.42 status adds `scenes`: the comma-joined ids the castle answers for. **v5.67 changes where they come from** — the CARD's own manifest, `/sd/scenes/show.man`, read ONCE at boot (nothing may touch the card while a song plays, docs/ISSUE-ring-flicker.md), falling back to the ids the image was built with when there is no manifest it believes. So adding a scene is a publish plus a reboot, not an OTA; diff `scenes` against scenes.yaml to see whether the running castle has read the latest publish (the desk does). **`missing` only covers those ids**, and since v5.67 it also GROWS at run time: a scene the manifest cannot answer for adds its id, and a scene whose `<id>.cue` is unreadable adds `<id>.cue`, bounded at 16 names. v5.52 adds `playing` (the audio pipeline is running) and `position_ms` (the main loop's clock since it started; 0 when idle), and a raw `/api/play` file now reports `scene:"stop"` and clears `track` the tick its audio ends. v5.55 makes the clock sound-true: `position_ms` stays 0 (with `playing:true`) until the speaker's own task is running, about half a second after a command, so a client that aligns lights to it waits for a moving clock; every generated scene holds its first cue the same way. v5.60 makes the whole reply ONE snapshot — `track`, `playing` and `position_ms` are taken at a single instant under one lock, so a poll on the tick a track ends can no longer carry a track name beside `playing:false` — counts EVERY light frame the one-slot mailbox lost in `light_evicted` (a frame dropped behind a stop used to go unreported, so `applied + evicted` never reconciled against what a page had sent), and confirms a web-OTA'd image on the first status it answers (the rollback net no longer needs a Home Assistant client). v5.61 adds `sd_read_errors` to **health**: transfers off the card that FAILED part way and were torn down (see `/sd/<path>` below) rather than framed as a short success — this boot only, 0 on a healthy castle, and the one number that explains a track that stops at the same place every time it plays. **v5.62** adds to **status** `epoch` (unix seconds, `0` until SNTP answers — the ring stamps uptime and always will, so this is the base a page converts with) and `rssi` (dBm, `0` when not associated); and to **health** `heap_min_kb` (the LOW-WATER mark of internal heap since boot — `heap_free_kb` in status is what is free *now*, after the allocation that failed was handed back, so it reads healthy on a castle that came within a hundred bytes of the wall an hour ago) and `sd_last_error` (`"<path>@<offset>"` of the last torn transfer, `""` when there has been none: a count alone could not tell one bad file from a dying card). **v5.63** adds to **status** `cues`: how many cues the castle loaded from `/sd/<track>.cue` for the raw file it is playing (`firmware/castle_cues.h`), `0` when the track has no cue file or nothing is playing — and since **v5.67** a SCENE reports its own number too, from `/sd/scenes/<id>.cue`, because a scene's timeline is the same kind of file read by the same reader — a page with light frames of its own to stream reads it and keeps quiet, and must not post `off` over a show it does not own |
| GET | `/api/events` | the main loop's own record, oldest first: `[{"t":<uptime_ms>,"e":"<kind>","a":"<arg>"}]`, 64 entries deep and never growing. A page polling `/api/status` once a second cannot see a scene that started and stopped in between; this is the record of the ticks it missed. A track name is kept to 47 bytes and **v5.62** puts `"trunc":true` beside one that was cut — present only when it happened, so an ordinary line is unchanged; without it /api/events named a song that does not exist. Kinds through v5.61: `play` `scene` `stop` `volume` `show` `blackout` `restart` (the web mailbox, arg = the command's own), `light_evicted` (arg = frames dropped since the last such line, at most one a second) and `sound` / `silent` (the audio clock). **v5.62** gives `sound` the track name and `silent` the milliseconds that were audible (both carried an empty arg before, which made the pair useless the next morning), and adds the sources the ring never saw: `scene_start` (arg = the scene id, recorded at the top of `run_scene` whoever asked — the PIR, a button and the evening playlist were recorded by nothing at all), `pir` (arg = the scene it fired), `pir_cooldown` (arg = seconds of cooldown left), `pir_off` (motion while disarmed), `button` (arg = the button), `wifi_up` (arg = rssi) / `wifi_down`, and `http_err` (arg = the status code, at most one line per two seconds so a client in a retry loop cannot flush the ring). Since v5.62 a compact copy of this ring — 64 x 16 B in RTC slow memory, 10 characters of arg — survives a panic or a watchdog reset, and the tail of the previous life's copy is written under the boot line in `/sd/logs/castle.log` |
| GET | `/api/files[?d=<subdir>]` | list the card root, or (v5.42) a subdirectory — `?d=scenes` is how the desk finally sees the show's own tracks. v5.62: a DIRECTORY reports `"size":0` rather than whatever the filesystem keeps its own bookkeeping in; `dir` is the field to branch on, and the number now agrees across the board, the emulator and the host-compiled harness |
| PUT | `/api/files/<name>`, `/api/site/<name>`, `/api/scenes/<name>` | write a file to the card (body = bytes). v5.42: refuses `507 not enough room on the card` before the first byte, `413 site file too large` for an implausible page, and answers `{bytes, crc32}` — sd_sync compares the CRC so a bad SD sector fails loudly. v5.61: an upload no longer holds the castle's one HTTP task, so `/api/status`, `/api/stop` and everything else keep answering for the whole of a `make publish` (the bytes are read and written by a worker task; the reply is unchanged and still comes when the file is on the card) — and a `500 rename failed` at the end now leaves the PREVIOUS copy of that name intact and playable, where it used to take it down with the failed upload |
| DELETE | `/api/files/<name>` | remove a card file |
| POST | `/api/play?f=` (v5.60: a long name with spaces no longer truncates in the query buffer and draws a spurious `400 need ?f=<file>`), `/api/stop`, `/api/scene?s=`, `/api/volume?v=` (0..100, clamped to scenes.yaml `hardware.audio.max_volume`) | transport |
| POST | `/api/show/start`, `/api/show/stop`, `/api/blackout`, `/api/light?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]`, `/api/pir?…` | show / lights / PIR. v5.60: `/api/pir?scene=` is checked against the same id list `/api/scene` uses (`404 unknown scene`, `503 scene list not ready`) instead of being passed to the select unread, and a `|` in any of `armed`/`cooldown`/`scene` is `400 bad separator` — the three ride to the main loop packed `a|c|s` and the two decoders disagreed about a value containing one. **v5.69** makes that check the ONLY one: `pir_scene` is a `text` now, not a `select` with compiled options, so the legal ids are the card's and a scene published while the castle is running can be named here immediately. The PIR also boots DISARMED (the AM312 is not wired on the S3 carrier), so `?armed=1` is a deliberate act and lasts until the next reboot |
| PUT | `/api/ota` | firmware image |
| GET | `/remote` | the castle's phone remote page |
| GET | `/sd/<path>` | stream any file on the card — also the URL the media pipeline plays scene audio through, on port **8080** (a second server, so a three-minute song cannot park the control plane). v5.61: a read error part way through no longer ends the response cleanly — the chunked body is ABANDONED without its terminator, which every HTTP client reports as a truncated transfer, or a `500 card read failed` when nothing has gone out yet; either way it counts in `/api/health`'s `sd_read_errors`. While a firmware image is being written (`PUT /api/ota`), the 8080 server answers `503 updating — card reads are paused` instead of competing with the flash write for the SPI bus and the CPU; port 80 is unaffected and the desk can watch the update |
