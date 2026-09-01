# Halloween Castle — project notes for Claude

A store-bought decorative castle lit by an ESP32-S2 Feather running ESPHome:
2× Jewel7 RGBW (towers) + Ring12 RGB (door), a MAX98357A amp, a PIR, and a
browser "cue desk" for tuning scenes. The global `~/.claude/CLAUDE.md`
describes a different repo — its ESP32-S3, espflash and Arduino-toolchain
parts do not apply here. Its Rust half is a different matter: this repo has
a Rust crate of its own at `core/`, and it is the production DSP path. This
file is the one that governs.

## Layout and what generates what

- `scenes/scenes.yaml` — THE source of truth: every scene's light cues, audio
  score, length and level. Everything else is generated from it.
- `tools/render_audio.py` → `audio/NN_<id>.mp3` (gitignored, embedded in flash).
- `tools/gen_esphome.py` → `firmware/generated/` (light cue scripts, rig.h).
- `tools/gen_previewer.py` → `previewer/castle-cue-desk.html` (the whole desk,
  `web/src/*.ts` bundled + minified, scene audio inlined). Generated, NOT
  tracked — `make preview` rebuilds it.
- `core/` — castle-core, the repo's Rust crate (zero dependencies, one
  `Cargo.lock` with nothing in it). Eight bins under `core/src/bin/`:
  `scene_render` (the production renderer — `tools/render_audio.py` spawns
  it), `analyze_track` (the importer's onsets/beats), `studio` (the server
  `make studio` runs since 2026-09-01, with `tools/studio.py` as the fallback
  and the parity reference), `castle`, and the four parity dumps
  `parity_dump` / `synth_dump` /
  `pulse_dump` / `netguard_dump` (the SSRF guard's corpus face).
  `src/wasm.rs` is the face the desk page inlines — built
  `cargo build --release --no-default-features --target wasm32-unknown-unknown`,
  because the crate's default `native` feature is the whole server/ffmpeg/
  flock half and none of it belongs in a module that never listens.
  `tools/core_bins.py` is the only door: subprocess, built on demand with
  cargo, a hard stop rather than a silent Python fall-back. The
  cross-language gates are `tests/test_*_rust.py` and
  `tests/studio_rust_case.py`; the copies they hold are listed in
  `docs/PARITY.md`.
- `tools/studio.py` — the local server behind the desk on 127.0.0.1:8765:
  imports tracks (`tools/import_track.py`), serves waveforms, edits
  scenes.yaml under `/studio/*`, relays `/api/*` to the castle
  (`tools/castle_link.py`). Route table: `docs/API.md`. It is no longer what
  starts by default — `tools/studio_launch.sh` (behind `make studio` and
  `.claude/launch.json`) builds and execs the Rust twin when cargo is
  present and falls back here, with a printed reason, when it is not. The
  Python stays the reference the parity gates measure against, and
  `CASTLE_STUDIO=python` picks it deliberately.
- `firmware/` — ESPHome YAML + C++ headers. `castle_flash.yaml` is the show
  build; `castle_sd.yaml` is the EXPERIMENTAL microSD variant whose web API
  (`sd_web.h`) the desk talks to. `firmware/pending/README.md` lists patches
  written but not yet flashed.
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
`build` / `upload` / `logs` · `studio` · `track SRC=… ID=…` · `test` · `lint`
· `check` (= CI) · `e2e` · `check-all` · `coverage` / `audit` (non-gating)
· `lock` · `rust` / `rust-test` / `rust-lint` / `rust-coverage` (castle-core;
`rust-coverage` is a non-gating `cargo llvm-cov` summary; `lint` depends on
`rust-lint`, and `tests/test_castle_core.py` shells out to those three, so the
gate has one definition) · `bench*` (bare-board dry runs) · `sd-build` / `sd-upload`
· `publish` (scene tracks + lean page → the castle) · `ota` (build, stop
audio, flash). `studio` runs `tools/studio_launch.sh`: the Rust server, the
Python one as the fallback. The studio's rebuild publishes on its own when a
castle answers; `docs/RUNBOOK.md` is the operator's end-to-end view.

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
  check (`SCENE_LIMIT`). The desk refuses the thirteenth too, at splice time
  and before the file is touched (`studio_scenes.check()`, which the Rust
  studio asks through `tools/scene_check.py`) — the ceiling should not be
  discovered by a red pre-commit hook after the show is already edited.
  Nothing hand-written is exempt.
- **Every grade-report citation names its audit**: `grade report 2026-08-31
  B1`, never a bare `B1` — item IDs are renumbered by each audit, and six
  reports now exist (`.claude/grade-report*.md`, plus older ones only in git
  history). `tools/check_citations.py` runs in `make check`, the hook and CI,
  and refuses an undated one. Date it by `git blame`, then confirm the ITEM
  matches the topic; if nothing matches, describe the problem in words rather
  than guess an ID.
- ruff + mypy clean (`pyproject.toml`); tsc `--noEmit` clean for `web/`.
- `make check` green before handing work back. Never skip or disable a test
  to get there — fix it or list it as follow-up work.
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
- `CASTLE_STUDIO=rust|python` forces one of the two servers in
  `tools/studio_launch.sh` (`make studio`, `.claude/launch.json`). Unset is
  "Rust if cargo or a built binary is here, else Python with a printed
  reason"; `rust` refuses rather than falling back, which is how the flip is
  tested. It does not reach the e2e suite — that is `CASTLE_STUDIO_CMD`.
- `CASTLE_STUDIO_CMD=<command>` swaps the SERVER the e2e suite runs against
  — the only local way to point the browser suite at the Rust twin:
  `cd web && CASTLE_STUDIO_CMD=../core/target/release/studio npx playwright
  test` (build it first: `make rust`). The port and `--localhost` are
  appended by `web/playwright.config.ts`, whose fall-back is `??`, so an
  EMPTY value is not "absent" — it is a server command of `""` and the suite
  fails to start. CI names it on both matrix axes for that reason.
- `CASTLE_PY=<interpreter>` names the python the studio's children run
  under. The Python studio has `sys.executable` and never needs it; the
  Rust studio bin has no such self-knowledge and asks `CASTLE_PY` first,
  then `.venv/bin/python`, then bare `python3` (`core/src/studio_scenes.rs`
  `py()`/`check_py()`). Set it from a worktree or a CI checkout that shares
  another tree's venv — otherwise the rebuild finds a system python with no
  yaml and every child fails confusingly. `web/playwright.config.ts` honours
  it for the same reason.
- `tests/studio_case.py` and `web/playwright.config.ts` set the first three.
- Hardware-free castle: `.venv/bin/python tools/castle_emu.py 8093`, then
  `CASTLE_HOST=127.0.0.1:8093 .venv/bin/python tools/studio.py 8766 --localhost`
  gives the full desk→studio→castle chain. The emulator is a byte-level port
  of `sd_web.h` (`tools/castle_emu_wire.py`); `tests/test_firmware_contract.py`
  parses the C and fails if the two drift — change both in one commit.
- Ports 8765/8766/8093 may be in use by the user's own sessions; tests bind
  port 0, e2e uses `CASTLE_E2E_PORT`.

## Hardware and firmware facts that bite

- ESP32-S2: no USB serial console, mDNS unreliable, single core. IDF 5.5
  pushed the S2 build to the **static-RAM cliff** (~20 bytes of headroom at
  one point; see the sdkconfig notes in `firmware/castle.yaml`). Firmware
  changes must be RAM-neutral: stack-only, PSRAM for buffers, no new statics.
- RMT on the S2: 4 channels x 64 symbols, 256 in total, no DMA — a budget
  `tools/gen_rig.py` spends per zone and refuses to overspend. ESPHome's
  default of 192 for one strip kills strips 2 and 3.
- The door ring corrupts a frame now and then: `docs/ISSUE-ring-flicker.md`
  has what is already ruled out (with evidence) and the next tests.
- The desk's effects (`web/src/effects.ts`) and `firmware/castle_effects.h`
  share an integer hash and are checked frame-exact (`web/test/firmware_parity.ts`,
  `tests/cxx/`). Change both or neither. The whole parity contract — every
  copy, every check, what to do when one fails — is `docs/PARITY.md`.
- Stop audio before an OTA (`make ota` and `sd_sync ota` do it themselves).
  The ring is RGB, not RGBW (`rgbw: false`).
- Scene ceiling: **12 scenes max** on this board (~9 KB dram0 each; see the
  header comment in `scenes/scenes.yaml` and the weekly CI compile's 92%
  alarm). Past that, cue timelines move to a card-loaded format, not a
  thirteenth generated script.
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
