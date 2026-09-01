# Retiring the Python studio — the off-season plan

Written 2026-09-01, the day after the Rust studio became what `make studio`
starts and what `make e2e` tests. The Python server is now a fallback and a
measuring stick; this is the plan for removing it without losing either the
safety or the tests, staged so nothing risky happens before Halloween runs.

The one-line rule: **the server retires, the toolchain does not.** The Rust
studio spawns Python children for every rebuild and import — that is the
design, not a leftover — so "retire Python" means exactly one thing here:
delete the second HTTP server and the live-twin tests that need it, after
freezing what those tests know into fixtures the Rust studio can be held to
alone.

## What stays forever (the Rust studio's children)

Spawned by `core/src/studio_scenes.rs`, `studio_import.rs`, `studio_probe.rs`
— see `studio_proc.rs` `py()` for how the interpreter is found:

- `tools/render_audio.py` (which itself spawns the Rust `scene_render` for
  the DSP; the Python is the ffmpeg/encode orchestration around it)
- `tools/gen_esphome.py`, `tools/gen_previewer.py` — the generators
- `tools/import_track.py` (+ `import_convert.py`, and the library half of
  `studio_tracks.py` they import — see the split in phase 3)
- `tools/sd_sync.py` — the publish push
- `tools/scene_check.py` — until phase 2 replaces it, then it goes too
- yt-dlp, ffmpeg — external, unchanged

## What retires (the second server)

`tools/studio.py`, `studio_http.py`, `studio_routes.py`, `studio_media.py`,
`studio_jobs.py`, `studio_publish.py`, `studio_scenes.py` (last, after
phase 2), and the fallback arms of `tools/studio_launch.sh`. With them: the
live-twin harness `tests/studio_rust_case.py` and the Python-server halves
of `tests/studio_case.py`'s consumers (the map is in phase 3).

## Phase 0 — pre-season prep (done or in flight, 2026-09-01)

- ✓ `make studio` starts the Rust server (`tools/studio_launch.sh`).
- ✓ `make e2e` tests the Rust server by default, freshly built.
- ✓ Golden fixtures: `tests/golden/` freezes the Python studio's canonical
  responses — read routes, error bodies, and above all the scene-splice
  validation strings (`tools/scene_check.py`'s 400 bodies, the desk's UX
  contract) — captured while the Python server is still the trusted
  reference. `tests/test_studio_golden.py` holds the RUST studio to them
  and never launches the Python one, so it survives the deletion.

## Phase 1 — the season is the soak test (Sep–Oct 2026)

Run the show on the Rust studio. The exit gate for everything below: the
season ends without `CASTLE_STUDIO=python` having been needed. If it WAS
needed, whatever forced it becomes a bug with a parity gate still alive to
bisect against — fix first, retire later. Nothing else in this phase.

## Phase 2 — native scene validation (Nov 2026)

The one real port. The Rust studio's splice route shells to
`tools/scene_check.py` because validation strings must come from one
implementation while two servers answer the desk. Post-season there is one
server, so the check moves into castle-core: a YAML-subset parser for a
scene block (the crate stays zero-dependency — the subset a scene uses, not
the spec), `scene_schema`'s rules, `SCENE_LIMIT`. Two things to accept
deliberately:

- The "scene is not valid YAML: …" prose is PyYAML's today. A native parser
  writes its own; the goldens for those bodies are REGENERATED then, on
  purpose, in the same commit — that is the moment byte-parity for this
  route ends, and the golden diff is the record of it.
- The 12-scene ceiling lives in three places (desk splice, `check_loc.py`,
  the firmware reality). After the port it is Rust + `check_loc.py`; keep
  the numbers sourced from one constant each side and cross-checked by a
  test, as today.

## Phase 3 — the deletion (Nov–Dec 2026)

- Split `studio_tracks.py`: the importer keeps the library/manifest half it
  imports today; the server-only half goes.
- Delete the server modules listed above. `studio_launch.sh` loses its
  fallback arms (or is deleted and `make studio` execs the binary; keep the
  cargo-builds-first behaviour either way).
- The test map — every file that imports the server today, and its fate:
  - `tests/studio_rust_case.py`, `test_studio_rust.py`,
    `test_studio_scenes_rust.py`, `test_studio_media_rust.py`,
    `test_studio_import_rust.py`, `test_studio_relay_rust.py` — the
    live-twin comparisons die; anything they assert that the golden suite
    and the Rust `#[test]`s do not already cover moves there FIRST, then
    the files go. The rule from CLAUDE.md holds: tests are ported, never
    just deleted for green.
  - `tests/test_studio_api.py`, `test_studio_http.py`, `test_studio_unit.py`
    (studio_http), `test_studio_jobs.py`, `test_studio_publish.py`,
    `test_media_failures.py`, `test_studio_cache.py`, `test_analysis.py`,
    `test_studio_media.py` (studio_media) — cover deleted code; each case
    either has a Rust twin already (`core/src` `#[test]`s — audit before
    deleting), moves to the golden/e2e layer, or names a behaviour the Rust
    server genuinely lacks, which is a port task, not a deletion.
  - `tests/test_studio_tracks_api.py`, `tests/studio_case.py` — shrink to
    the importer/library half that survives.
- CI: the web matrix collapses to one axis (`CASTLE_STUDIO_CMD` stays as
  the escape hatch), the python job drops the server suites, total runtime
  falls.

## Phase 4 — the docs sweep (with phase 3, same PRs)

`CLAUDE.md` (layout, sandbox knobs: `CASTLE_STUDIO` dies, `CASTLE_PY`
stays), `docs/API.md` (one server), `docs/PARITY.md` (the studio row
becomes the golden suite), `docs/RUNBOOK.md`, `web/playwright.config.ts`
comments, this file (marked done, kept as the record).

## What would cancel this plan

A second operator, a port off the home LAN, or the S2 being replaced by a
board that changes the toolchain story — any of those reopens the question
of what the reference implementation is. Absent that: the plan above, in
order, none of it before the season ends.
