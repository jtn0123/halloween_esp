# The cue desk's HTTP contract

Three parties: the **desk** (the page, `web/src/`), the **studio**
(`tools/studio.py`, the local server behind it) and the **castle**
(`firmware/sd_web.h`, or `tools/castle_emu.py` standing in for it). The
prefix says who owns a route:

- `/studio/…` — the studio's own authoring routes (`web/src/api.ts`).
- `/api/…` — the castle's. The studio relays these untouched
  (`tools/castle_link.py`); no castle in reach → 502. `device.ts` owns them.
- `/api/status` is the one shared path: the castle's status when one answers,
  else the studio's own `{"studio": true}` — the desk's mode probe.

Every body is JSON unless noted. Failures carry `error` (and `reason`, one
line, on tool failures). An unknown `/studio/*` path is a 404; an unknown
`/api/*` path relays, so it is whatever the castle says (502 with none).

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
| POST | `/studio/rebuild` | render audio → gen_esphome → gen_previewer |
| POST | `/studio/server/stop`, `/studio/server/restart` | answer, then do it |
| GET | `/studio/card/<name>` | pull a file off the castle's card (relays `/sd/<name>`, name-stripped) |

**Aliases, one release only (v5.24):** each of these also answers at its old
`/api/…` spelling (`/api/tracks`, `/api/import`, … `/api/card/<name>`); the
studio logs `DEPRECATED` once per route. The one exception is `/api/scene`:
with `?s=<id>` it is the castle's fire-a-scene and relays; with a JSON body
it is the editor above. The table is `STUDIO_ROUTES` in `tools/studio.py`.

## Relayed to the castle (`/api/…`, `/remote`)

| Method | Path | Does (firmware `sd_web.h`) |
|---|---|---|
| GET | `/api/status`, `/api/health`, `/api/bootlog` | status (studio answers when no castle), health, boot log |
| GET | `/api/files` | list the card |
| PUT | `/api/files/<name>`, `/api/site/<name>`, `/api/scenes/<name>` | write a file to the card (body = bytes) |
| DELETE | `/api/files/<name>` | remove a card file |
| POST | `/api/play?f=`, `/api/stop`, `/api/scene?s=`, `/api/volume?v=` | transport |
| POST | `/api/show/start`, `/api/show/stop`, `/api/blackout`, `/api/light?c=`, `/api/pir?…` | show / lights / PIR |
| PUT | `/api/ota` | firmware image |
| GET | `/remote` | the castle's phone remote page |
