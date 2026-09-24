# Halloween Castle — project notes for Claude

A store-bought decorative castle lit by an **Adafruit ESP32-S3 Feather
#5477** (4 MB flash, 2 MB PSRAM) in the castle-carrier v3.3a, running ESPHome:
2× Jewel7 RGBW (towers) + Ring12 RGB (door), a MAX98357A amp, a PIR, and a
browser "cue desk" for tuning scenes. It was an ESP32-S2 Feather until
2026-09-17; that board is retired and its build deleted (see the history
paragraph under "Hardware and firmware facts that bite"). The global
`~/.claude/CLAUDE.md` describes a different repo — its espflash and
Arduino-toolchain parts do not apply here, and neither do its S3 board and
flash-size specifics: this is a 4 MB Feather, flashed with ESPHome. Its Rust half is a different matter: this repo has
a Rust crate of its own at `core/`, and it is the production DSP path. This
file is the one that governs.

## Layout and what generates what

- `scenes/scenes.yaml` — THE source of truth: every scene's light cues, audio
  score, length and level. Everything else is generated from it.
- `tools/render_audio.py` → `audio/NN_<id>.mp3` (gitignored; the desk's
  inlined copy) and `audio/card/` (the 96 kbps files `sd_sync scenes` pushes).
- `tools/gen_esphome.py` → `firmware/generated/` (`sfx`, `rig.h`, lights) and
  — since v5.67 — `audio/card/scenes/`, which is THE SHOW: `show.man` (the
  scene manifest, `tools/scene_manifest.py`: id, audio token, volume, length,
  loop flag) and one `<id>.cue` per scene in the same format a raw song's cue
  file uses. `tools/gen_scene_cards.py` is the emitter. There are no generated
  per-scene ESPHome scripts any more — one runner reads those files
  (`firmware/castle_scenes.h` + `castle_scenes.yaml`), so **a scene edit is a
  publish, not a firmware flash**: `make publish` is the whole deploy.
- `tools/render_cues.py` → `audio/card/cues/<track>.cue` (`make cues`): a
  song's light show as a FILE the firmware loads from the card when the
  track is played (`firmware/castle_cues.h`, v5.63) — any track in `tracks/`,
  every pulse (no PULSE_CAP), no scene slot, no OTA. The scene block is the
  one in scenes.yaml when the song is a scene, else the desk's own
  `sceneYaml` run headless (`web/src/scene_cli.ts`, bundled with esbuild).
  `tools/cue_file.py` is the format; `tests/test_cue_file_cxx.py` runs the
  real header against it. `sd_sync cues` (and `make publish`) puts each file
  beside its song in the card root.
- `tools/gen_previewer.py` → `previewer/castle-cue-desk.html` (the whole desk,
  `web/src/*.ts` bundled + minified, scene audio inlined). Generated, NOT
  tracked — `make preview` rebuilds it.
- `core/` — castle-core, the repo's Rust crate (zero dependencies, one
  `Cargo.lock` with nothing in it). Nine bins under `core/src/bin/`:
  `scene_render` (the production renderer — `tools/render_audio.py` spawns
  it), `analyze_track` (the importer's onsets/beats), `studio` (THE server behind
  the desk — the Python one retired 2026-09-06, docs/RETIREMENT.md),
  `castle`, and the five parity dumps
  `parity_dump` / `synth_dump` /
  `pulse_dump` / `netguard_dump` (the SSRF guard's corpus face) /
  `scene_dump` (the scene validator's).
  `src/wasm.rs` is the face the desk page inlines — built
  `cargo build --release --no-default-features --target wasm32-unknown-unknown`,
  because the crate's default `native` feature is the whole server/ffmpeg/
  flock half and none of it belongs in a module that never listens.
  `tools/core_bins.py` is the only door: subprocess, built on demand with
  cargo, a hard stop rather than a silent Python fall-back. The
  cross-language gates are `tests/test_*_rust.py` and
  `tests/studio_rs_case.py`; the copies they hold are listed in
  `docs/PARITY.md`.
- `tools/studio_launch.sh` — what `make studio` and `.claude/launch.json`
  start: it builds `core/target/release/studio` when cargo is present and
  execs it, and refuses with a printed reason when there is neither cargo
  nor a build. The server itself is castle-core's `studio` bin on
  127.0.0.1:8765 — it imports tracks, serves waveforms, edits scenes.yaml
  under `/studio/*` and relays `/api/*` to the castle, spawning the Python
  toolchain (`import_track.py`, the generators, `sd_sync.py`) for the work
  that stayed Python. Route table: `docs/API.md`. There was a second,
  Python server (`tools/studio.py` and its `studio_*.py`) until 2026-09-06;
  `docs/RETIREMENT.md` is the plan that removed it and the tag
  `python-studio-final` is the last tree that carries it.
- `firmware/` — ESPHome YAML + C++ headers. **Two** buildable targets, one
  show: `castle_feather_s3.yaml` is the ESP32-S3 Feather #5477 in carrier
  v3.3a, THE build and the castle in the yard (`make build` / `upload` /
  `logs` / `ota`; `build-fs3` etc. are aliases for muscle memory), and
  `castle_s3.yaml` (2026-09-05) is the bare-ESP32-S3-WROOM-1 carrier v5 —
  `make build-s3` / `upload-s3` / `logs-s3` / `validate-s3`. Both are
  compiled by the weekly CI job; the carrier has never been on hardware and
  its board does not exist yet. The show itself — the card, the loopback
  stream, the web API (`sd_web.h`) the desk talks to — is
  `castle_sd_common.yaml`, which both include; `castle.yaml` is the shared
  core (and, since the S2 went, the file that describes the CHIP), not a
  buildable target. What is left in `castle_feather_s3.yaml` is the Feather's
  own NeoPixel and the playback codecs — the WROOM has no such LED, and
  ESPHome packages APPEND lists, so a build cannot subtract a light its base
  declared. Two builds have been deleted rather than nursed:
  `castle_flash.yaml` on 2026-09-01 (every scene embedded; the show outgrew a
  1.75 MB OTA slot — docs/notes/03-build.md §12.15) and `castle_sd.yaml`, the
  ESP32-S2 Feather, on 2026-09-17 (§12.20), taking `castle_sd_jewels.yaml`,
  `hello.yaml` and `generated/lights_s3.yaml` with it. `bench*.yaml` are
  bare-board variants OF the production build; `castle_s3_qemu.yaml` is the
  carrier with UART0 and Wi-Fi off, for `tools/qemu_boot.sh` (docs/QEMU.md: a
  hand-run bring-up tool, deliberately not a CI gate).
  `firmware/pending/README.md` is the version table: what changed, and which
  board it has run on.
- `tracks/` — the user's imported audio (gitignored except `tracks.json`, the
  provenance manifest) — never a test fixture directory.
- `previewer/castle-cue-desk.html` is generated and **gitignored**
  (`.gitignore:40`): 2 MB a revision was the whole repo's growth, so the blob
  is rebuilt, never committed. Anything that needs it builds it first — CI
  runs `gen_previewer.py` as a step, and the e2e global setup refuses to run
  without it. The inlined build is still the portable artifact (open from
  disk, copy it to someone), so its weight is governed by
  `tools/previewer_budget.py` (`PAGE_BUDGET_KB`, 4 MB), and going over it
  does not fail first — it **un-inlines**: scenes give up their data URI
  from the BACK of the show, one at a time, for a
  `/studio/scene-audio/<id>` link, until the page fits. The build prints
  which scenes went that way; the desk falls back to the live synth for
  exactly those when the link cannot be fetched (a page opened from disk).
  The build **FAILS** — writing nothing, leaving the last good page in the
  tree — only when the page is still over budget with NOTHING inlined,
  which is markup and bundle and no scene's fault. Both halves are
  `tests/test_previewer_budget.py`.
  The DEVICE never serves it — `sd_sync site` pushes the lean rewrite +
  per-scene mp3s, and the studio rewrites to the same lean form at serve time.

## Make targets (see `make help`)

`setup` (python3.13 venv) · `audio` · `generate` · `preview` · `validate` ·
`build` / `upload` / `logs` (the S3 Feather in the yard; `build-fs3` /
`upload-fs3` / `logs-fs3` are aliases) · `build-s3` / `upload-s3` / `logs-s3`
/ `validate-s3` (the WROOM carrier) · `studio` · `track SRC=… ID=…`
· `test` · `test-radio` · `lint`
· `check` · `e2e` · `check-all` · `coverage` / `audit` (non-gating)
· `lock` · `rust` / `rust-test` / `rust-lint` / `rust-coverage` (castle-core;
`rust-coverage` is a non-gating `cargo llvm-cov` summary; `lint` depends on
`rust-lint`, and `tests/test_castle_core.py` shells out to those three, so the
gate has one definition) · `bench*` (bare-board dry runs) · `sd-build` /
`sd-upload` (old names for `build` / `upload`) · `publish` (scene tracks + lean page → the castle) · `ota`
(build, stop audio, flash the image that build just wrote —
`tools/check_image.py --path` names it, so no glob can hand the castle a
stale binary). `studio` runs `tools/studio_launch.sh`, which builds the
binary before it execs it. The studio's rebuild publishes on its own when a
castle answers; `docs/RUNBOOK.md` is the operator's end-to-end view.

Castle Radio (`demo/castle-radio/`) is a SECOND Python server, on port 8871 —
the music player and its Mac-tools bridge, started by the launcher the
installer registers (`demo/castle-radio/README.md`). It has its own gates,
`make test-radio` (Python + `node --test`) and `make coverage-radio` (its own
floor, `COVERAGE_RADIO_MIN`), and it is in `make lint`'s scope; 8871 joins the
"may be in use by the user's own sessions" list below.

Run Python through `.venv/bin/python` (the Makefile falls back to `python3`
only when `.venv` is absent). `make e2e` is `cd web && npx playwright test`;
set `CASTLE_E2E_PORT=8821` to run beside another suite (default 8799).

## Rules that are enforced

- **500 lines per file**, every text file the repo tracks, docs included —
  `tools/check_loc.py` runs in `make check` and the pre-commit hook. Split on
  a real seam rather than trimming comments. Generated files are exempt
  (`EXEMPT_PATHS`, each with its generator named); `scenes/scenes.yaml` is
  exempt as *data* (`DATA_EXEMPT`) and pays for it with the budget that
  actually binds it — **at most 12 scenes**, counted and failed by the same
  check (`SCENE_LIMIT`), which since v5.67 is the card manifest's fixed record
  count rather than a RAM measurement. The desk refuses the thirteenth too, at splice time
  and before the file is touched (`core/src/studio_check.rs`, whose count
  and refusal are the studio's own since the phase-2 port) — the ceiling
  should not be
  discovered by a red pre-commit hook after the show is already edited.
  Nothing hand-written is exempt.
- **Every grade-report citation names its audit**: `grade report 2026-08-31
  B1`, never a bare `B1` — item IDs are renumbered by each audit, and nine
  reports now exist (`/bin/ls .claude/grade-report*.md` — eight dated plus the
  live `grade-report.md`, and older ones only in git history). `tools/check_citations.py` runs in `make check`, the hook and CI,
  and refuses an undated one. Date it by `git blame`, then confirm the ITEM
  matches the topic; if nothing matches, describe the problem in words rather
  than guess an ID.
- ruff + mypy clean (`pyproject.toml`); tsc `--noEmit` clean for `web/`.
- `make check` green before handing work back. Never skip or disable a test
  to get there — fix it or list it as follow-up work. `check` is exactly CI's
  blocking Python/TypeScript steps: `audio`, `test`, `test-radio` (added
  2026-09-17, grade report 2026-09-17 pm D2), `lint` (ruff format + ruff +
  mypy + `rust-lint`), the two `check_image.py` runs, `check_loc.py`,
  `check_citations.py`, `tsc --noEmit` and `web`'s node suites. It is NOT all
  of CI: the coverage floors (`coverage-gate`, `coverage-radio`) re-run those
  two suites under coverage.py and stay out of the inner loop, the esphome
  validate/compile steps are `make validate` / `check-all`, the browser suite
  is `make e2e`, and the wasm-face build and `make audit` are CI-only (audit
  non-gating). Run the coverage floors yourself when you touched `tools/` or
  `demo/castle-radio`.
- The e2e suite (`cd web && npx playwright test --list` for the count) needs
  a built page (`make preview`) and `cd web && npx playwright install chromium`.

## Sandboxing — never touch the real library or show from tests/tools

- `CASTLE_TRACKS=<dir>` redirects the track library and the manifest.
- `CASTLE_SCENES=<file>` redirects scene writes.
- `CASTLE_HOST=<host[,fallback…]>` names the castle; set-but-EMPTY (`""`)
  means "explicitly no castle" — castle_link returns None, no sockets.
- `CASTLE_BUILD=<dir>` redirects everything the generators WRITE — `audio/`,
  `firmware/generated/` and the previewer page (`tools/build_paths.py`
  `build_root()`, `core/src/studio.rs`). Without it a sandboxed
  `CASTLE_SCENES` still builds beside itself, in `<scenes-dir>/_build/`;
  unset both and the target is the repo. It is the fourth name in
  `tests/helpers.SANDBOX_ENV`, cleared before any tools module reads it, so
  an emulator shell that exported these knobs cannot redden `make test`.
- `CASTLE_STUDIO_CMD=<command>` swaps the SERVER the e2e suite runs
  against — the escape hatch for bisecting against an older build. Unset,
  `web/playwright.config.ts` runs `core/target/release/studio`, which
  `make e2e` rebuilds first when cargo is present, so the default local run
  tests what production runs. The port and `--localhost` are appended by the
  config, whose fall-back is `??`, so an EMPTY value is not "absent" — it is
  a server command of `""` and the suite fails to start. CI names it
  explicitly for that reason. (`CASTLE_STUDIO=rust|python` chose between the
  two servers until 2026-09-06; there is one, so it is gone.)
- `CASTLE_PY=<interpreter>` names the python the studio's children run
  under. The studio bin has no `sys.executable` to fall back on and asks
  `CASTLE_PY` first, then `.venv/bin/python`, then bare `python3`
  (`core/src/studio_proc.rs` `py()`/`check_py()`). Set it from a worktree
  or a CI checkout that shares
  another tree's venv — otherwise the rebuild finds a system python with no
  yaml and every child fails confusingly. `web/playwright.config.ts` honours
  it for the same reason.
- `tests/studio_rs_case.py` and `web/playwright.config.ts` set them.
- Hardware-free castle: `.venv/bin/python tools/castle_emu.py 8093`, then
  `CASTLE_HOST=127.0.0.1:8093 tools/studio_launch.sh 8766 --localhost`
  gives the full desk→studio→castle chain. The emulator is a byte-level port
  of `sd_web.h` (`tools/castle_emu_wire.py`); `tests/test_firmware_contract.py`
  parses the C and fails if the two drift — change both in one commit. And
  `tests/test_firmware_web_cxx.py` + `_card` + `_storm` RUN the drift check:
  `tests/cxx/web_check.cpp` compiles the real headers against a fake ESP-IDF
  (`tests/cxx/shim/`) and every request goes to both castles.
- Ports 8765/8766/8093/8871 may be in use by the user's own sessions; tests bind
  port 0, e2e uses `CASTLE_E2E_PORT`.

## Hardware and firmware facts that bite

- **Flash is the tight resource**, not RAM. The Feather has 4 MB, so the
  dual-OTA layout gives two 1,835,008-byte app slots: v5.69 measured **67.9%
  of flash and 35.2% of RAM** (120,267 of 341,760) on the board. Prefer PSRAM
  for buffers and stack over statics anyway — it costs nothing and the habit
  is cheap — but the thing to watch before a feature lands is the image size,
  and `tools/check_image.py` is the gate (warns at 90% of the slot, fails at
  97%). Over budget, the fallback is physical access, which is what OTA
  existed to avoid.
- RMT: 4 TX channels × 48 symbols, **192 in total**, no DMA — a budget
  `tools/gen_rig.py` spends per zone and refuses to overspend. Three strips
  plus the Feather's status pixel is all four blocks, 192 of 192, nothing
  spare; the WROOM carrier has no status pixel and keeps one block. A strip
  that asks for something that is not a whole block gets no channel and stays
  dark without a word, which is why 64 — the S2's block size, and the number
  every note used to say — is now a hard build failure.
- **ESP32-S2 HISTORY** (retired 2026-09-17, docs/notes/03-build.md §12.20).
  The porch ran an S2 Feather from 2026-08-22, and a lot of this repo's
  caution was bought there: no USB serial console at all (its TinyUSB CDC
  console faulted the chip inside `sinf()`), mDNS that never worked, one core,
  and a **static-RAM cliff** — IDF 5.5 left ~20 bytes of dram0 headroom, so
  every change had to be RAM-neutral and a whole `sdkconfig` diet was bought
  to make v5.23 link at all. Its RMT was 4 × 64 = 256. None of those limits
  binds now, but the diet is still IN the build, deliberately: the retirement
  changed the build's shape and not its behaviour. Each line comes back one at
  a time, measured on hardware. mDNS was the first and is DONE: v5.66 turned
  it on for +2,080 B of flash and `castle-feather-s3.local` resolved on the
  board on 2026-09-17 (`devices.toml` still leads with the router lease
  because a reservation beats a multicast query — the name is its fallback).
  Still on the diet, in no fixed order: softAP + captive portal, WPS,
  `LWIP_MAX_SOCKETS` back above 16, LWIP's own mDNS queries (the board asking,
  not answering), and WPA3 OWE. One at a time, each with a measured flash/RAM
  delta.
- The door ring corrupts a frame now and then: `docs/ISSUE-ring-flicker.md`
  has what is already ruled out (with evidence) and the next tests.
- The desk's effects (`web/src/effects.ts`) and `firmware/castle_effects.h`
  share an integer hash and are checked frame-exact (`web/test/firmware_parity.ts`,
  `tests/cxx/`). Change both or neither. The whole parity contract — every
  copy, every check, what to do when one fails — is `docs/PARITY.md`.
- Stop audio before an OTA (`make ota` and `sd_sync ota` do it themselves).
  The ring is RGB, not RGBW (`rgbw: false`).
- Scene ceiling: **12 scenes max**, still — but for a different reason since
  v5.67. It was ~9 KB of the S2's dram0 a scene, because each scene was a
  generated script; scenes are card files now (`scenes/show.man` +
  `scenes/<id>.cue`, read into PSRAM by `firmware/castle_scenes.h`) and there
  is no per-scene static cost left. What holds the number is `show.man`'s
  fixed record count: `MAX_SCENES` (`tools/scene_manifest.py`), `kMaxScenes`
  (`firmware/castle_scenes.h`), `SCENE_LIMIT` (`tools/check_loc.py`,
  `core/src/vocab.rs`). Lifting it is deliberate work on all four with a
  measured number — docs/notes/03-build.md §12.21 lists it as follow-up. The
  twelve slots are for what the PIR, the buttons and the evening playlist must
  start by name; any other song on the card still gets its full show from a
  `.cue` beside it (v5.63, `make cues`) with no slot at all.
- v5.42 feeds the upload watchdog every 32 KB (was 8 KB). Verified on the
  emulator only — watch the first big push on real hardware; if an upload
  reboots the board, revert the cadence in `sd_web.h write_body`.

## Security position (accepted risk — do not re-raise)

The studio server has no Origin/Host validation and the SD build's
`PUT /api/ota` and `/api/files/` have no auth. The user has permanently
accepted both: it is a Halloween decoration on a private home LAN with one
operator. Do not report these as findings or propose auth for them.
Dependency advisories: `make audit` (starlette hits are in the ESPHome build
toolchain and ignored by id; the cryptography one cleared with esphome 2026.8.1).

## Commit style

One-line subject written as a sentence about what changed and why, in the
voice of the existing log (`git log --oneline`), e.g. "The 500-line rule now
reads every file, prose included — and the notes obey it". No conventional-
commit prefixes. Bump the firmware version string when the device build
changes so an OTA can be verified on the panel.
