# The cue desk's HTTP contract

Four parties: the **desk** (the page, `web/src/`), the **Rust studio**
(castle-core's `studio` bin, `core/src/bin/studio.rs` plus
`core/src/studio*.rs` — the local server behind the desk, and what
`make studio` runs since 2026-09-01), the **Python studio**
(`tools/studio.py` and its `studio_*.py`: the same surface again, both tables
below, the launcher's fallback when there is no cargo, and the reference the
parity gates measure the Rust against — `tests/studio_rust_case.py` and the
five `tests/test_studio*_rust.py` suites hold the two answer-for-answer), and
the **castle** (`firmware/sd_web.h`, or
`tools/castle_emu.py` standing in for it). A route added to one studio is
added to both, or the parity suites go red (`docs/PARITY.md`). The prefix
says who owns a route:

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
`tools/studio_http.py` (`tools/studio.py` only re-exports the name).

## Relayed to the castle (`/api/…`, `/remote`)

| Method | Path | Does (firmware `sd_web.h`) |
|---|---|---|
| GET | `/api/status`, `/api/health`, `/api/bootlog` | status (studio answers when no castle), health, boot log. v5.42 status adds `scenes`: the comma-joined ids the RUNNING BUILD was compiled with. **`missing` only covers those ids** — a nine-scene build reports `missing:""` while the tenth scene's track is absent; diff `scenes` against scenes.yaml for that (the desk does) |
| GET | `/api/files[?d=<subdir>]` | list the card root, or (v5.42) a subdirectory — `?d=scenes` is how the desk finally sees the show's own tracks |
| PUT | `/api/files/<name>`, `/api/site/<name>`, `/api/scenes/<name>` | write a file to the card (body = bytes). v5.42: refuses `507 not enough room on the card` before the first byte, `413 site file too large` for an implausible page, and answers `{bytes, crc32}` — sd_sync compares the CRC so a bad SD sector fails loudly |
| DELETE | `/api/files/<name>` | remove a card file |
| POST | `/api/play?f=`, `/api/stop`, `/api/scene?s=`, `/api/volume?v=` (0..100, clamped to scenes.yaml `hardware.audio.max_volume`) | transport |
| POST | `/api/show/start`, `/api/show/stop`, `/api/blackout`, `/api/light?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]`, `/api/pir?…` | show / lights / PIR |
| PUT | `/api/ota` | firmware image |
| GET | `/remote` | the castle's phone remote page |
