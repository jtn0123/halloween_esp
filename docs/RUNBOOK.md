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
   (scene tracks + the lean page to the card). Watch the rebuild log —
   it says what was pushed and what it could not do.
3. **Firmware, if the log says so.** A brand-new scene is a *compile-time*
   object: the running board does not know it until you
   `make ota` (builds `castle_sd.yaml`, stops audio, flashes over HTTP).
   The desk shows the same fact two ways: the scene's tile is dimmed, and
   the 🏰 panel's health row says "N scene(s) newer than the firmware".
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
runs the generators and the importer as child processes, and the Rust studio
bin — what `make studio` starts since 2026-09-01 — has no `sys.executable`
to fall back on — without `CASTLE_PY` it finds a
bare `python3`, and every rebuild dies on `import yaml` instead of on
anything to do with the show. (CLAUDE.md, "Sandboxing", lists it beside
`CASTLE_TRACKS` / `CASTLE_SCENES` / `CASTLE_HOST`.)

## When a scene will not play

Work down this list — it is ordered by how often each one was the answer:

- **`unknown scene` toast** → the firmware predates the scene. `make ota`.
- **Scene runs, no audio, chirp instead** → the track is missing from
  `/sd/scenes/`. `make publish` (or check the 🏰 panel: "scenes/ is
  missing …"). Note `missing` in `/api/status` only covers scenes the
  *running build* knows — a nine-scene build reports `missing:""` while
  the tenth scene's track is absent (`docs/API.md`).
- **Nothing answers at all** → `tools/sd_sync.py status`. No reply: check
  power, then the router's DHCP table for the board's MAC
  (`84:f7:03:d7:99:3c`). The desk chip says which host it is probing.
- **Audio starts then breaks up** → look at heap in the 🏰 panel; under
  ~20 KB playing is the documented failure floor. Also
  `docs/ISSUE-scene-start-audio.md` for the open scene-start issue.

## Show night

- `make publish` in the afternoon, while you can still fix things.
- The evening playlist: 🏰 panel → "▶ start the show", or the phone
  remote (`http://<castle>/remote`) — hand that URL to whoever is at the
  door.
- Motion: the PIR row in the panel — armed, which scene, cooldown.
- **Stop audio before any OTA.** `make ota` does this itself; if you flash
  another way, press ■ first.

## After changing firmware

- Bump `version:` in `firmware/castle.yaml` (the panel is how you PROVE the
  OTA took — an upload that "succeeded" with the old version on screen did
  not).
- `make ota`, then confirm the image (connect once with `tools/device.py`
  or HA) — an unconfirmed image rolls back on its next reboot.
- First big upload after a firmware change: watch it. v5.42 feeds the
  watchdog every 32 KB instead of every 8 KB during uploads (4× faster
  pushes); it behaved on the emulator but the real watchdog only exists on
  the board — if an upload reboots the castle, that cadence is the suspect
  (`firmware/sd_web.h write_body`).
