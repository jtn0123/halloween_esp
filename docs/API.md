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
| POST | `/studio/publish` | push scene tracks + the lean page to the card (`sd_sync scenes` + `site`); answers `{needs_firmware: [ids], note}` for scenes the RUNNING build lacks — those need `make ota` |
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
| GET | `/api/status`, `/api/health`, `/api/bootlog` | status (studio answers when no castle), health, boot log. v5.42 status adds `scenes`: the comma-joined ids the RUNNING BUILD was compiled with. **`missing` only covers those ids** — a nine-scene build reports `missing:""` while the tenth scene's track is absent; diff `scenes` against scenes.yaml for that (the desk does). v5.52 adds `playing` (the audio pipeline is running) and `position_ms` (the main loop's clock since it started; 0 when idle), and a raw `/api/play` file now reports `scene:"stop"` and clears `track` the tick its audio ends. v5.55 makes the clock sound-true: `position_ms` stays 0 (with `playing:true`) until the speaker's own task is running, about half a second after a command, so a client that aligns lights to it waits for a moving clock; every generated scene holds its first cue the same way. v5.60 makes the whole reply ONE snapshot — `track`, `playing` and `position_ms` are taken at a single instant under one lock, so a poll on the tick a track ends can no longer carry a track name beside `playing:false` — counts EVERY light frame the one-slot mailbox lost in `light_evicted` (a frame dropped behind a stop used to go unreported, so `applied + evicted` never reconciled against what a page had sent), and confirms a web-OTA'd image on the first status it answers (the rollback net no longer needs a Home Assistant client) |
| GET | `/api/files[?d=<subdir>]` | list the card root, or (v5.42) a subdirectory — `?d=scenes` is how the desk finally sees the show's own tracks |
| PUT | `/api/files/<name>`, `/api/site/<name>`, `/api/scenes/<name>` | write a file to the card (body = bytes). v5.42: refuses `507 not enough room on the card` before the first byte, `413 site file too large` for an implausible page, and answers `{bytes, crc32}` — sd_sync compares the CRC so a bad SD sector fails loudly |
| DELETE | `/api/files/<name>` | remove a card file |
| POST | `/api/play?f=` (v5.60: a long name with spaces no longer truncates in the query buffer and draws a spurious `400 need ?f=<file>`), `/api/stop`, `/api/scene?s=`, `/api/volume?v=` (0..100, clamped to scenes.yaml `hardware.audio.max_volume`) | transport |
| POST | `/api/show/start`, `/api/show/stop`, `/api/blackout`, `/api/light?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]`, `/api/pir?…` | show / lights / PIR. v5.60: `/api/pir?scene=` is checked against the same id list `/api/scene` uses (`404 unknown scene`, `503 scene list not ready`) instead of being passed to the select unread, and a `|` in any of `armed`/`cooldown`/`scene` is `400 bad separator` — the three ride to the main loop packed `a|c|s` and the two decoders disagreed about a value containing one |
| PUT | `/api/ota` | firmware image |
| GET | `/remote` | the castle's phone remote page |
