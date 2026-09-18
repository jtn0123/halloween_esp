# Runbook — the workflows the operator actually performs

The rest of `docs/` describes the system; this page is the night-to-night
view: what to run, in what order, and what to check when it doesn't take.
(Grade report 2026-08-23 H1 — the missing last-mile documentation is how the Ballad
of the Witches' Road sat rendered on the Mac while the castle answered
`unknown scene` all evening.)

## Adding a song, end to end

1. **Import** — in the desk's Library (`make studio`), or
   `make track SRC=<file|url> ID=<name>`.
2. **Make the scene** — the desk's "Make scene" button writes it into
   `scenes/scenes.yaml` and the studio rebuilds: audio → firmware cues →
   previewer, **then publishes automatically when the castle answers**
   (the show — audio, `<id>.cue`, `show.man` — plus the lean page, to the
   card). Watch the rebuild log — it says what was pushed and what it could
   not do.
3. **Reboot, if the log says so.** Since v5.67 a new scene is card data, not
   a compile-time object: no OTA. But the castle reads `show.man` ONCE at
   boot — nothing may touch the card while a song is playing
   (docs/ISSUE-ring-flicker.md) — so a brand-new id is unknown until it
   restarts. The publish answers `needs_reboot: [ids]` and the desk shows the
   same fact two ways: the scene's tile is dimmed, and the 🏰 panel's health
   row names the count. `tools/sd_sync.py` → 🏰 panel "restart", or pull power.
   *Editing* an existing scene needs neither a reboot nor a flash: the runner
   reads its cue file at every start.
4. **Verify** — the panel shows the new version; press the scene; the chip's
   ▶ line names the right track. `tools/sd_sync.py status` from a terminal
   says the same.

Terminal spelling of step 2's push, any time: `make publish`.

The render itself is Rust: `make audio` (and the studio's rebuild) spawns
castle-core's `scene_render`, rebuilding it with cargo on first use. A
machine without the Rust toolchain stops with a sentence saying so — there
is no Python fall-back, on purpose: the crate's fixed float profile is what
makes a scene the same bytes on every machine.

Running the desk from a worktree, or from anywhere the project `.venv` is not
one directory up? Export `CASTLE_PY=/path/to/.venv/bin/python`. The studio
runs the generators and the importer as child processes, and the studio bin
— what `make studio` starts — has no `sys.executable` to fall back on:
without `CASTLE_PY` it finds a bare `python3`, and every rebuild dies on
`import yaml` instead of on anything to do with the show. (CLAUDE.md, "Sandboxing", lists it beside
`CASTLE_TRACKS` / `CASTLE_SCENES` / `CASTLE_HOST`.)

## When a scene will not play

Work down this list — it is ordered by how often each one was the answer:

- **`unknown scene` toast** → the castle has not read the manifest with that
  id in it. `make publish`, then reboot the board (v5.67; before that it was
  `make ota`). `/api/status`'s `scenes` is exactly what `show.man` said at
  boot, so compare it with `scenes/scenes.yaml`.
- **Scene runs, lights but no cues (`cues: 0` in status)** → its `<id>.cue`
  is not on the card, or is not one the reader believes (magic, version,
  length). `make publish`; `/api/status`'s `missing` names it. Since v5.68
  starting the scene again after the publish CLEARS the name — no reboot — so
  `missing` staying put is a publish that did not land, not a stale reading.
- **Scene runs, no audio, chirp instead** → the track is missing from
  `/sd/scenes/`. `make publish` (or check the 🏰 panel: "scenes/ is
  missing …"). Note `missing` in `/api/status` only covers scenes the
  *running build* knows — a nine-scene build reports `missing:""` while
  the tenth scene's track is absent (`docs/API.md`).
- **Nothing answers at all** → `tools/sd_sync.py status`. No reply: check
  power, then the router's DHCP table for the board's MAC
  (`84:f7:03:d7:99:3c`). The desk chip says which host it is probing.
- **Audio starts then breaks up** → the Castle Radio page, "Recent castle
  events": the row above the log carries `heap now … · lowest …`. The
  *lowest* number is the one that matters — `heap_min_kb` in `/api/health`,
  the low-water mark since boot; the free figure recovers the moment the
  allocation that failed is handed back, which is why "look at heap" used to
  come back clean an hour after the fault. Under ~20 KB while playing is the
  documented failure floor. The same row shows card read errors and the last
  path one happened on. Also `docs/ISSUE-scene-start-audio.md`.
- **It fell over and you want to know what it was doing** →
  `tools/sd_sync.py logs` (or `castle logs`). Since v5.62 each boot line in
  `/sd/logs/castle.log` is followed by the last 64 things the *previous*
  life did, read out of RTC memory, which a panic does not clear — plus the
  reset reason, the card errors that life saw and which OTA slot it ran
  from. Live, the same ring is "Recent castle events" on the page.

## Show night

- `make publish` in the afternoon, while you can still fix things.
- The evening playlist: 🏰 panel → "▶ start the show", or the phone
  remote (`http://<castle>/remote`) — hand that URL to whoever is at the
  door.
- Motion: the PIR row in the panel — armed, which scene, cooldown. Since
  v5.69 the castle boots **disarmed**: the AM312 is not wired on the S3
  carrier, so arming it is a deliberate `POST /api/pir?armed=1` (or the panel
  toggle) and lasts only until the next reboot.
- **Stop audio before any OTA.** `make ota` does this itself; if you flash
  another way, press ■ first.
- **The card is not optional.** Since the all-in-flash build was retired
  (§12.15) there is no embedded copy of the show to fall back on: a castle
  with an empty slot, or a card that was never published to, plays a
  one-second chirp per scene and nothing else — and since v5.67 the SHOW
  itself is on the card too, so a cardless castle has one compiled-in
  fallback look (`firmware/generated/fallback_scenes.h`), says why in the log
  and the event ring, and reports it in `/api/status`'s `missing`. A fresh
  board is `make publish` **then** `make ota` — that order, because v5.66 and
  earlier simply ignore the new card files, while a v5.67 image with nothing
  to read falls back to the one look.

## After changing firmware

- Bump `version:` in `firmware/castle.yaml` (the panel is how you PROVE the
  OTA took — an upload that "succeeded" with the old version on screen did
  not).
- `make ota`. Since v5.60 the image confirms itself on the first
  `/api/status` it answers — the poll `make ota` already does — so there is
  no manual step. (A connect from `tools/device.py` or HA still confirms it
  too; an image that answers neither rolls back on its next reboot.)
- First big upload after a firmware change: watch it. v5.42 feeds the
  watchdog every 32 KB instead of every 8 KB during uploads (4× faster
  pushes); it behaved on the emulator but the real watchdog only exists on
  the board — if an upload reboots the castle, that cadence is the suspect
  (`firmware/sd_web.h write_body`).
