# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware + castle-core + Castle Radio
**Audited:** 2026-09-24. The previous report (2026-09-17 pm) is archived at
`.claude/grade-report-2026-09-17-pm.md`. Cite THIS report as
`grade report 2026-09-24 X`.
**Tree:** `061c2d0` (main after PRs #45–#51: Sonar rounds, hash-locked
requirements, the Castle Radio import queue, the light-show lab, prepared
light shows). Firmware unchanged since v5.70.
**Scope note — lighting excluded.** The lighting is being overhauled, so this
audit does not grade effects, cue content, the light-show lab, choreography,
`render_cues`, `gen_rig`/RMT, pulse dynamics or the ring flicker. Last
report's A3 (`PULSE_CAP` parity guarding dead code) and G3 (the desk
rescanning every cue every frame) are parked, not closed.

**How this was graded.** Five auditors, one per category cluster, each
re-verifying every open 2026-09-17 pm item and hunting new defects with
file:line evidence. `make setup` then `make check` were run in a clean Linux
container:

- As shipped: **fails** — `lame` missing (audio step), then 1,193 tests with
  19 failures + 1 error (13 numpy-vs-libm parity, 7 `yt-dlp`). See I1.
- With `lame`/`ffmpeg` installed, `NPY_DISABLE_CPU_FEATURES` exported (CI's
  value) and `yt-dlp` in the venv: the failing suites pass (58/58), `test-radio`
  green, ruff/mypy clean (199 files), `check_loc` pass, citations 258 all
  dated, `tsc --noEmit` clean.
- `make check` rewrites the tracked `audio/markers.json` (key order only). See I2.
- Radio suite under coverage: 133 tests, 80% (floor 67). `cargo test --release` green.

**Permanently accepted risk — not raised:** studio Origin/Host validation;
firmware `PUT /api/ota` and `/api/files/` auth.

## Summary

| ID | Category | Grade | Last | Items |
|----|----------|-------|------|-------|
| A | Architecture & Design | B+ | B+ | 5 |
| B | Backend Quality | B+ | B+ | 7 |
| C | Frontend Quality | B | B | 8 |
| D | Testing & Reliability | B+ | B+ | 4 |
| E | Security *(excl. accepted risk)* | B+ | B+ | 4 |
| F | Dependencies & Tech Currency | A− | B+ | 5 |
| G | Performance & Scalability | B | B | 4 |
| H | Documentation & Onboarding | B− | B | 10 |
| I | Developer Experience & Tooling | B− | B | 6 |
| J | Firmware | A− | B+ | 3 |
| **Overall** | | **B+** | B+ | **56** |

**Top 5 highest-leverage fixes:** I2, B6, H1, I1, B7

Two categories rose. Firmware is A− because last week's J1–J3 regressions are
fixed and verified on the board. Dependencies are A− because the lock now
carries hashes that CI enforces. The overall grade holds at B+ because the new
work has the same weakness as last week: it is under-tested and
under-documented, and it all landed in Castle Radio. Three problems stand out:

- The import queue's Cancel does not stop a job past the download (B6).
- The radio binds the user's real track library before its sandbox exists (B7).
- The docs and the studio still tell the operator to reboot after a publish,
  which v5.69 made unnecessary (H1).

Two categories fell. H fell for that stale reboot advice. I fell because a
fresh `make setup` no longer gives a passing `make check` on Linux, and one
non-deterministic JSON dump keeps the weekly firmware CI job red (I2).

## Status of the 2026-09-17 pm items

Fixed (8): grade report 2026-09-17 pm D3, F1, F6, J6, most of I3, half of H5,
two of H7's five one-liners; B1/D2/D4/E1/G1/G2/H1/J1–J3 (already struck) hold.
Parked as lighting: A3, G3.
Carried, renumbered here: A1 → A1, A2 → A2, A4 → A3, A5 → A4; B2–B5 → B2–B5;
C1–C8 → C1, C2, C3, C5, C6, C7, C8; D1 → D1, D5 → D3, D6 → D2, D7 → D4;
E2 → E2, E3 → E3; F2–F5 → F2–F5; G4 → B5, G5 → G2, G6 → G3; H2 → H5,
H3 → H6, H4 → H7, H5 → H8, H6 → H9, H7 → H10; I1 → I3, I2 → I4, I4 → I1,
I5 → I5; J4 → J1, J5 → J2.

---

## A — Architecture & Design — B+

Held. The Rust core only got mechanical Sonar refactors, the route split is
intact, and no new seams appeared. The pressure is Castle Radio. It grew from
11 to 16 Python modules (3,468 lines) and now reaches into `tools/` through
three `sys.path.insert` bridges. It still lives in `demo/`, and none of its
routes are in `docs/API.md`. None of last week's A items was fixed.

#### A1 — Two servers, one product, and the second is growing (carried: grade report 2026-09-17 pm A1)
- **Where:** `demo/castle-radio/server.py:333-339` (it gained `/radio/cancel`
  and `/radio/rename`); `radio_jobs.py:39-41` (its own executor); `sys.path`
  bridges at `radio_jobs.py:31`, `rich_show.py:13`, `desktop_tools.py:6`.
  `docs/API.md` has zero `/radio/` rows.
- **Fix:** move it out of `demo/` under its real name, write a one-page
  boundary (or fold-in) plan, and add its route table to API.md (H9).
- **Effort:** M · **Grade lift:** B+ → A−

#### A2 — The OTA gate hardcodes one machine's build volume (carried: grade report 2026-09-17 pm A2)
- **Where:** `firmware/build_path.yaml:21`; `tools/check_image.py:55-57`
  (`/Volumes/512Flash/esphome-builds`).
- **Fix:** read the `build_path:` line from the YAML, and fall back to
  `firmware/.esphome/build`.
- **Effort:** S · **Grade lift:** removes a silent wrong-binary path

#### A3 — `wasm.rs` compiled into every native bin (carried: grade report 2026-09-17 pm A4)
- **Where:** `core/src/lib.rs:70`. **Fix:** `#[cfg(target_arch = "wasm32")]`.
- **Effort:** S · **Grade lift:** nit

#### A4 — Import link in argv from Rust, in the env from Python (carried: grade report 2026-09-17 pm A5)
- **Where:** `core/src/studio_import.rs:115,202` vs `radio_jobs.py:290-305`.
- **Fix:** have Rust pass it in the environment too.
- **Effort:** S · **Grade lift:** nit

#### A5 — Syncing a song now waits on building its light show, inside the HTTP request
- **Where:** `demo/castle-radio/remote_library.py:160-166`. `start()` calls
  `rich_show.prepare()` on the request thread. That runs `analyze_track`, then
  esbuild and node (`tools/render_cues.py:61-103`), none of them with a
  timeout. The page gives the POST 15 s (`imports.js:15`).
- **What's wrong:** this is sync plumbing, not light design.
  - If show preparation fails (node missing, stems analysis gone), an
    audio-only sync that used to work now returns 400.
  - On a slow run the page times out while the server goes on with the job.
  - The commit message says older imports keep streamed lights. The code
    instead prepares a show for every row that lacks one.
- **Fix:** move preparation into the pooled transfer job, with a timeout. On
  failure, fall back to syncing audio only and say so in the job phase.
  Revisit once the lighting overhaul lands.
- **Effort:** S–M · **Grade lift:** restores a working path

---

## B — Backend Quality — B+

Held. Last week's B1 (the process-group reaper) holds after the Sonar pass.
The new radio code brings a Cancel button that does not cancel and a latent
hole in the sandbox, so the grade does not rise. B1 is not reused, so the
carried B items keep their numbers.

#### B2 — The studio's import progress still cannot move (carried: grade report 2026-09-17 pm B2)
- **Where:** `core/src/studio_jobs.rs:151-162` never sets
  `CASTLE_PROGRESS_STREAM`. `tools/import_fetch.py:57` and `tools/stems.py:179`
  read it; only the radio sets it (`job_progress.py:99`).
- **Fix:** `cmd.env("CASTLE_PROGRESS_STREAM", "1")`.
- **Effort:** S · **Grade lift:** a progress bar that moves

#### B3 — Emulator ticker dies silently and does card I/O under its lock (carried: grade report 2026-09-17 pm B3)
- **Where:** `tools/castle_emu_loop.py:97-115` (`try/finally`, no `except`).
  `apply` at `:185-187` calls `int(arg)` under `st.lock`, and `_apply_scene`
  reads `show.man` inside the lock.
- **Fix:** catch `Exception`, log it and continue; read the card before
  taking the lock.
- **Effort:** S

#### B4 — The JSON half of `/radio/import` inherits the 100 MB cap (carried: grade report 2026-09-17 pm B4)
- **Where:** `demo/castle-radio/import_routes.py:101-102` (`json.loads(rfile.read(length))`
  with the upload limit) bypasses `json_body`'s 4 KB limit (`server.py:298-303`).
- **Fix:** use `handler.json_body(...)` in the JSON branch.
- **Effort:** S

#### B5 — Studio `job.log` unbounded; `castle-cmp-*` never swept (carried: grade report 2026-09-17 pm B5)
- **Where:** `core/src/studio_jobs.rs:197`; `core/src/studio_probe.rs:223`.
- **Fix:** cap the log at about 500 lines; sweep day-old dirs at startup.
- **Effort:** S

#### B6 — Cancel during analysis is ignored and the job ends "Ready" (new)
- **Where:** `demo/castle-radio/radio_jobs.py:155-167` sets `cancelled`.
  `prepare` checks it only at `:285`, before the download. The later phases
  never look: `crate_analysis` (`:313`), `rich_show.prepare` (`:364`, whose
  subprocesses the Event cannot kill) and the catalog write (`:367-371`).
- **What's wrong:** without splitting (the default), `/radio/cancel` answers
  success, the job runs on to "Ready", and the catalog row is written with
  `cancelled: True` still on the record. The page contradicts itself. Split
  imports happen to honour it, which hides the bug.
- **Fix:** check `job.get("cancelled")` before every phase and before the
  catalog commit. Route `rich_show`'s children through `job_progress.run`.
- **Effort:** S · **Grade lift:** a queue whose Cancel means cancel

#### B7 — Castle Radio binds the real track library before its sandbox env exists (new)
- **Where:** `server.py:11` imports `desktop_tools`, which pulls in
  `rich_show`, `tools/render_cues.py:44` and `track_lib`. `track_lib` fixes
  `TRACKS` at import (`tools/track_lib.py:25`). Only after that does
  `server.py:17` import `radio_jobs`, which sets `CASTLE_TRACKS`
  (`radio_jobs.py:25-30`).
- **Reproduced:** after `import server`, `track_lib.TRACKS` is the repo's
  `tracks/` while `manifest.PATH` is the sandboxed `.radio-data`.
- **What's wrong:** isolation now depends on import order. Subprocesses are
  fine, but any in-process caller of `track_lib.TRACKS` touches the user's real
  library. CLAUDE.md's sandbox rules forbid exactly that. It is latent today.
- **Fix:** set the env in a tiny `radio_env.py` that `server.py` imports
  first, or resolve `TRACKS` lazily the way `build_paths` does. Add a test
  that asserts it.
- **Effort:** S · **Grade lift:** closes a sandbox hole

#### B8 — The catalog persists fields derived from the manifest, so they go stale (new)
- **Where:** `radio_jobs.py:112-115` merges `source_metadata` into rows, and
  `:367-371` writes the merged rows back. `source_available` is then frozen,
  yet `imports.js:47` uses it to enable "Change audio".
- **Fix:** strip derived keys before writing `catalog.json`.
- **Effort:** S · **Grade lift:** nit

---

## C — Frontend Quality — B

Held. The desk is in good shape: `tsc` is clean, there are no `as any` or
`@ts-ignore`, and the a11y gate is intact. The Sonar pass also fixed a real
bug: `device_panel.ts:202` now calls `testPct()` instead of interpolating the
function. Every open radio item carries, and the new import queue adds a
keyboard and screen-reader regression.

#### C1 — Castle Radio's browser half has no static checking (carried: grade report 2026-09-17 pm C1)
- **Where:** there is no `jsconfig.json`. The `/* global */` lists keep
  growing: 21 names at `preview.js:2`, 13 at `imports.js:5`.
- **Fix:** add `checkJs` and a `globals.d.ts`, and run `tsc -p demo/castle-radio`
  in `check`.
- **Effort:** M · **Grade lift:** B → A−

#### C2 — The radio site has zero browser coverage (carried: grade report 2026-09-17 pm C2)
- **Where:** none of the 23 specs in `web/test/e2e/` loads `demo/castle-radio`.
- **Fix:** one Playwright spec that serves the radio and asserts nav focus,
  `aria-current` and queue focus retention (C4).
- **Effort:** M · **Grade lift:** B → B+

#### C3 — The `X-Castle` client contract has no client-side test (carried: grade report 2026-09-17 pm C3)
- **Where:** `imports.js:148-149`, `preview.js:273`. The new multi-file
  upload loops through the same untested `uploadAudio`.
- **Fix:** a source-scan test that every guarded POST carries the header,
  plus `uploadAudio` run in a vm against a stub XHR.
- **Effort:** S

#### C4 — The import queue re-renders under keyboard focus and a live region (new)
- **Where:** `imports.js:78`: `renderJobs` replaces all of `#import-jobs` via
  `innerHTML` whenever the jobs JSON changes, which a running job's percent
  does on every 2 s poll. `holdingJob` (`:69-72,120-121`) guards pointer
  presses only. `#import-jobs` is `aria-live="polite"`, and `:150` rewrites
  "N s elapsed" inside it every second.
- **What's wrong:** a keyboard user on Cancel, Retry or Clear loses focus to
  `<body>` within 2 s. Screen readers re-announce the whole queue every poll.
- **Fix:** patch rows in place by job id, or restore focus by `data-*` key
  after the render. Move `aria-live` to a one-line summary.
- **Effort:** S–M · **Grade lift:** B → B+ with C2

#### C5 — Two tracked derived artifacts, no generator, no freshness gate (carried: grade report 2026-09-17 pm C4)
- **Where:** `demo/castle-radio/scenes.json` (no emitter under `tools/`),
  `visuals.js`. Both are in sync today.
- **Effort:** M

#### C6 — Dense lines defeat the 500-line rule (carried, worse: grade report 2026-09-17 pm C5)
- **Where:** longest lines: `imports.js` 1,387 chars (was 1,295),
  `index.html` 5,381, `style.css` 10,616, `visuals.js` 18,919. The desk's
  longest is 218. See I4.
- **Effort:** M

#### C7 — Radio page switches move neither focus nor `aria-current`; castle IP hardcoded (carried: grade report 2026-09-17 pm C7)
- **Where:** `app.js:145`; `10.27.27.81` at `index.html:5,16`,
  `device-tools.js:52` and `device-words.js:18`.
- **Effort:** S · **Grade lift:** nit

#### C8 — Desk and radio nits (carried, extended: grade report 2026-09-17 pm C8)
- Unescaped `innerHTML`: `panels.ts:178-180`, `budget.ts:213-248`.
- `mmss` can show `0:60` (`import_opts.ts:62-63`).
- `clamp` is defined three times; `APERTURE` is a dead export (`stage.ts:51`).
- New: `track_rows.ts` `sourceHtml` slices after escaping, so it can cut an
  entity (`&am`).
- New: `imports.js:107` refuses a whole multi-file batch when one file is over
  100 MB. Put that file in `left[]` instead.
- **Effort:** S · **Grade lift:** nit

---

## D — Testing & Reliability — B+

Held. Real progress: Rust coverage is now measured in CI, including the bins
the Python suites spawn (`sonar.yml:59-65,85-91`). `heal_missing` runs on both
castles (`tests/test_firmware_tick_cxx.py:283-338`). The queue routes came
with tests. Still, no coverage number can fail a build, and the radio's
destructive HTTP routes are the untested ones.

#### D1 — Rust coverage measured but gates nothing (carried: grade report 2026-09-17 pm D1)
- **Where:** `sonar.yml` has no `--fail-under`. `Makefile:373-376`
  `rust-coverage` still leaves out the spawned bins.
- **Fix:** `cargo llvm-cov report --fail-under-lines N`, with N from the first
  green run. Make `rust-coverage` use the same recipe.
- **Effort:** S · **Grade lift:** B+ → A− with D2

#### D2 — The radio's HTTP layer is 47% covered; its floor is 13 points slack (carried: grade report 2026-09-17 pm D6)
- **Where:**
  - `server.py` is at 47% (was 53%).
  - Uncovered: `byte_range` (`:70-81`), `media` (`:150-171`), the DELETE
    routes (`:268-294`), `post_restore` (`:315-329`), `library_ops.known_key`.
  - The total of 80% is carried by the lab modules at 99%. The floor is
    `COVERAGE_RADIO_MIN := 67` (`Makefile:309`).
- **Fix:** raise the floor to 79. Add `_Caller` tests for DELETE (running
  job → 409, bad key → 404), restore with and without `X-Castle`, and edge
  cases for `byte_range`.
- **Effort:** M

#### D3 — Wall-clock sleeps against firmware tick windows; no Playwright retries (carried: grade report 2026-09-17 pm D5)
- **Where:** `tests/test_firmware_tick_cxx.py:164-242,328,373,390`;
  `web/playwright.config.ts:46-53`. New: `test_rich_show.py:178` sleeps
  0.3 s, then asserts.
- **Fix:** poll until a deadline; `retries: process.env.CI ? 2 : 0`.
- **Effort:** M

#### D4 — Compile-time path in a Rust test; Swift launcher test never run (carried: grade report 2026-09-17 pm D7)
- **Where:** `core/src/studio.rs:283`; `tests/castle_launcher_process_test.swift`.
- **Effort:** S · **Grade lift:** nit

---

## E — Security — B+ *(excluding accepted risk)*

Held. The netguard fix holds, the new radio routes go through
`request_guard`, and titles are escaped before they reach `innerHTML`. One doc
claim is false (E1), and two old items are still open.

#### E1 — "Every CI install uses `--require-hashes`" is false for two installs (new)
- **Where:** `docs/SECURITY.md:59-63` makes the claim. `.github/workflows/ci.yml:304`
  (the esphome job, which builds the image flashed to the castle) and `:234`
  (the studio venv) install with `-c requirements.lock` only.
- **Fix:** use `--require-hashes -r` there too. Add a test that every
  workflow `pip install` carries it.
- **Effort:** S · **Grade lift:** B+ → A− with E2

#### E2 — A manifest id's characters are still trusted (carried: grade report 2026-09-17 pm E2)
- **Where:** `firmware/castle_scenes.h:127-129` joins the raw id into a path;
  `tools/scene_manifest.py:124-151` never checks the character class. Same
  root cause as J1.
- **Fix:** one `row_ok()` (`[A-Za-z0-9_.-]`) in all three walkers and in
  `decode`.
- **Effort:** S

#### E3 — Shared upload staging dir wiped by whichever import finishes first (carried: grade report 2026-09-17 pm E3)
- **Where:** `core/src/studio_import.rs:130-137,166`.
- **Fix:** a per-upload `_upload_<pid>_<rand>/`.
- **Effort:** S · **Grade lift:** nit

#### E4 — Radio JSON routes drop the connection on a non-object body (new)
- **Where:** `import_routes.py:103-106,183-189` call `payload.get`. A body of
  `[]` raises `AttributeError`, which is not in `POST_ERRORS` (`server.py:51`).
- **Fix:** have `json_body` refuse non-dicts, and add a test that posts `[]`.
- **Effort:** S · **Grade lift:** nit

---

## F — Dependencies & Tech Currency — A−

Up from B+. The lock carries sha256 hashes and CI enforces them (last week's
F6). `dbus-fast` joined the lock under a Linux marker (F1). What holds it
below A: the Dependabot queue cannot go green on its own.

#### F1 — Dependabot PRs cannot go green (new)
- **Where:**
  - PR #47 raises `aioesphomeapi>=46.4.1` against esphome's `==46.3.0` pin
    (`requirements.txt:17`). The switch to a floor did not stop these PRs.
  - Dependabot PRs get no `SONAR_TOKEN`, so the Sonar job fails on #46, #47
    and #48.
  - The bot cannot write hashes, so every pip bump needs a hand `make lock`.
- **Fix:** ignore `aioesphomeapi` in `dependabot.yml`, skip Sonar for
  `dependabot[bot]`, and document (or automate) relocking.
- **Effort:** S · **Grade lift:** needed for A

#### F2 — The Rust pin now has six unchecked copies (carried: grade report 2026-09-17 pm F2)
- **Where:** `ci.yml:71,136,242,308,379`, `sonar.yml:50` vs
  `core/rust-toolchain.toml:20`.
- **Fix:** a test that greps both workflows against `pinned_channel()`.
- **Effort:** S

#### F3 — Rust 1.88.0, about 15 months old, no review date (carried: grade report 2026-09-17 pm F3)
- **Effort:** S · **Grade lift:** nit

#### F4 — `@types/node ^26` against Node 22 (carried: grade report 2026-09-17 pm F4)
- **Where:** `web/package.json:18`. **Fix:** `^22`, then close #48.
- **Effort:** S · **Grade lift:** nit

#### F5 — `types-PyYAML` unpinned (carried: grade report 2026-09-17 pm F5)
- **Where:** `requirements-dev.txt:4`. **Effort:** S · **Grade lift:** nit

---

## G — Performance & Scalability — B

Held. The publish fixes (hash compare; oplock dropped before the network
push) hold. The radio kept a single global lock, and the lock now covers
heavier work.

#### G1 — Castle Radio holds its one lock across audio analysis on the 2 s poll path (new)
- **Where:**
  - `server.py:231-236`: `get_waveform` holds `LOCK` across a full decode and
    onset analysis on a cache miss.
  - `server.py:223-225`: `get_library` holds it while `desktop_tools.catalog`
    runs `rich_show.metadata` for every song (`rich_show.py:152-167`), which
    reads and decodes each cue.
  - `imports.js:142` polls both routes every 2 s, even when idle.
  - Import progress updates take the same lock (`radio_jobs.py:112-116`).
- **What's wrong:** one uncached waveform freezes the library, the jobs list
  and the running import's progress. Library polls cost more as the library
  grows.
- **Fix:** copy under the lock, then compute and reply outside it. Cache
  metadata by mtime. Poll the library only when the jobs change.
- **Effort:** S–M · **Grade lift:** B → B+ with G2

#### G2 — 100 MB uploads fully buffered per thread (carried: grade report 2026-09-17 pm G5)
- **Where:** `import_routes.py:118`. The queue-room check (`:58-62`) reserves
  nothing, so parallel uploads all pass it and all buffer.
- **Fix:** stream in 64 KB blocks, and reserve the slot under `LOCK`.
- **Effort:** S

#### G3 — No write timeout; `startup.log` never rotates; every poll logged (carried: grade report 2026-09-17 pm G6)
- **Where:** `core/src/http_parse.rs:170`; `tools/castle_launcher.swift:60-65`.
  New: `server.py` has no `log_message` override, so the page writes about
  86k lines a day while it is open.
- **Effort:** S · **Grade lift:** nit

#### G4 — `job.log` unbounded — see B5.

---

## H — Documentation & Onboarding — B−

Down from B. v5.69 changed how the operator deploys (a publish needs no
reboot), and the operator docs, the emulator and the studio's own reply did
not follow. None of H2–H7 from last week is fully fixed. The Castle Radio
README still describes streamed lights for synced imports. The structural
claims in CLAUDE.md do check out: nine bins, the make targets, the `check`
recipe, 67.9% / 35.2%.

#### H1 — RUNBOOK, API.md and the studio still say a new scene needs a reboot (new)
- **Where:** `docs/RUNBOOK.md:19-25,52-55`; `docs/API.md:50,66`, which
  contradicts `:72`. In `core/src/studio_publish.rs`, `:26` reads the status
  before the push, and `:65,94` pass that stale status to `needs_reboot`.
- **What's wrong:** the firmware re-seeds within 200 ms of a `show.man` PUT
  (`sd_web_upload.h:151`). Yet every publish of a new scene answers
  `needs_reboot:[id]`, the desk dims the tile, and the operator reboots for
  nothing. `tools/sd_sync.py:246` already says "No reboot line any more".
- **Fix:** re-read the status after the push, or drop `needs_reboot` for
  firmware ≥ 5.69. Rewrite the doc lines.
- **Effort:** S · **Grade lift:** B− → B

#### H2 — The emulator still seeds scenes only at boot (new)
- **Where:** `tools/castle_emu.py:154-163`. No path re-seeds on a
  `show.man` PUT.
- **What's wrong:** the "byte-level port" claim is false for this path, and
  that is why nothing caught H1.
- **Fix:** re-seed on a manifest PUT, plus a contract test.
- **Effort:** S

#### H3 — Radio README describes streamed lights; synced imports now carry a card `.cue` (new)
- **Where:** `demo/castle-radio/README.md:84-85,131-134` vs
  `remote_library.py:163-167`.
- **Fix:** rewrite both paragraphs, and document the "ready when all three
  files are on the card" rule and how to re-sync old imports.
- **Effort:** S · **Grade lift:** nit+

#### H4 — The watchdog-cadence pointer names the wrong file (new)
- **Where:** `CLAUDE.md:281`, `docs/RUNBOOK.md:120` and
  `firmware/pending/README.md:55` say `sd_web.h write_body`. It has been
  `firmware/sd_web_upload.h:67` since v5.61. "Verified on the emulator only"
  is probably stale too.
- **Effort:** S · **Grade lift:** nit

#### H5 — The standings block still says the castle is off the network (carried: grade report 2026-09-17 pm H2)
- **Where:** `docs/notes/05-decisions-and-roadmap.md:77-103` (dated
  2026-09-01, 41.9% RAM / 71.8% flash, "waiting on hardware").
- **Effort:** S

#### H6 — Getting started omits `lame` and understates Node (carried: grade report 2026-09-17 pm H3)
- **Where:** `README.md:92-96`; no preflight before `tools/render_audio.py:203-205`.
  Confirmed in this audit: `make check` died on `lame` first. See I1.
- **Effort:** S

#### H7 — README's diagram predates v5.67; its studio paragraph predates the retirement (carried: grade report 2026-09-17 pm H4)
- **Where:** `README.md:23,35-36`. **Effort:** S

#### H8 — §12.20–§12.22 missing from the notes index (carried: grade report 2026-09-17 pm H5, half fixed)
- **Where:** `PROJECT_NOTES.md:32,52-69`. **Effort:** S

#### H9 — `/radio/*` missing from `docs/API.md` (carried: grade report 2026-09-17 pm H6)
- **Effort:** S (M together with A1)

#### H10 — One-liners (carried: grade report 2026-09-17 pm H7, two of five fixed)
- `docs/SECURITY.md:7` says port 8820 (the studio is on 8765).
- `docs/PARITY.md:162` cites `ci.yml:101` (the line is now `:122`).
- `firmware/sd_audio.h:61` still says "flash scenes".
- `tools/check_citations.py:10` says "six" reports; there are nine.
- **Effort:** S

---

## I — Developer Experience & Tooling — B−

Down from B. `make setup` works in a clean container, but the `make check`
after it does not. `make check` also dirties a tracked file, and that same
nondeterminism has kept the weekly firmware CI job red since 2026-09-21. I3
improved: `castle_sd_common.yaml` went from 496 to 386 lines and `server.py`
from 495 to 358.

#### I1 — `make setup` → `make check` fails on a clean Linux box (carried, worse: grade report 2026-09-17 pm I4)
- **Where:**
  - `Makefile:97` installs `requirements*.txt`, not the hashed lock CI uses.
    The result drifts (cbor2 6.1.4 vs locked 5.9.0) and has no `yt-dlp`.
  - `make check` (`:405`) never sets `NPY_DISABLE_CPU_FEATURES`, which CI
    sets (`ci.yml:122`). 13 bit-parity tests then fail on an AVX-512 host.
  - `lame` is not checked for (H6).
  - `npx tsc` runs with no `node_modules` guard.
- **Observed:** 19 failures + 1 error. The same suites pass once those four
  gaps are closed by hand.
- **Fix:** install with `--require-hashes -r requirements.lock`, export the
  numpy variable on x86_64, add a preflight for `lame`/`ffmpeg`/`node`, and
  make the `yt-dlp` tests skip outside CI the way cargo's do.
- **Effort:** S · **Grade lift:** B− → B

#### I2 — `make audio` rewrites the tracked `audio/markers.json`; weekly CI red since 09-21 (new)
- **Where:** `tools/render_audio.py:370-371` merges `{**prev[sid], **new}` and
  dumps without `sort_keys`. It trips the "generated show is the committed
  show" step (`ci.yml:399-400`) in the weekly run. The same flip-flop put
  10k-line churn into d15b372, 8fad819 and 0642843.
- **Fix:** `json.dumps(..., sort_keys=True)`, sort the band lists, regenerate
  once and commit.
- **Effort:** S · **Grade lift:** B− → B; the weekly firmware job goes green

#### I3 — The citation rule reads one spelling; bare IDs are invisible to it (carried: grade report 2026-09-17 pm I1)
- **Where:** `tools/check_citations.py:53`. Bare IDs at `ci.yml:88,133,343`,
  `githooks/pre-commit:46`, `core/src/studio_relay.rs:27`, and new ones
  (`B61`–`B63`) in `demo/castle-radio/device-link.js`.
- **Effort:** M

#### I4 — The 500-line cap counts newlines only (carried: grade report 2026-09-17 pm I2)
- **Fix:** add a maximum line length for non-exempt files (see C6).
- **Effort:** M

#### I5 — `make coverage` still nests the Rust gates in the unit suite (carried: grade report 2026-09-17 pm I5)
- **Where:** `Makefile:293-295` does not export `CI_RUST_GATES_ELSEWHERE`.
- **Effort:** S · **Grade lift:** nit

#### I6 — CI and Makefile nits (new)
- `Makefile:127-136`: the studio comment block now sits above `show-lab`.
- `sonar.yml:25`: `cancel-in-progress` also cancels main pushes. When #49–#51
  merged about 10 s apart, only the last merge was analysed.
- The Sonar job re-runs every suite and adds about 9 min per PR.
- **Effort:** S · **Grade lift:** nit

---

## J — Firmware — A−

Up from B+. No firmware file changed since v5.70. Last week's J1–J3 are fixed
and were verified on the board, and last week's J6 comment drift is corrected.
What holds it below A is two small, still-open input-trust items.

#### J1 — Manifest text fields trusted; Python accepts rows the C refuses (carried: grade report 2026-09-17 pm J4)
- **Where:** `firmware/castle_scenes.h:166,187,211` check only for NUL.
  `cue_path` (`:128-130`) has no `/` or `..` check, unlike
  `castle_cues::path_for`. `json_escape` (`sd_web_util.h:160`) passes raw
  bytes ≥0x80. Same fix as E2.
- **Effort:** S · **Grade lift:** A− → A with J2

#### J2 — `duration_ms` has no floor; `loop` makes zero a restart storm (carried: grade report 2026-09-17 pm J5)
- **Where:** `castle_scenes.h:270,295-298`; `castle_scenes.yaml:209-226`;
  `tools/scene_manifest.py:115`.
- **Fix:** a length under 100 ms drops the loop flag; `encode` raises on ≤ 0.
- **Effort:** S

#### J3 — Hand-written scene buttons use compiled ids (new)
- **Where:** `firmware/castle_inputs.yaml:110-173` has eight literal "Scene: X"
  buttons. The show has ten scenes. A renamed scene leaves a button that
  starts the fallback look, even though "a scene edit is a publish, not a flash".
- **Fix:** generate the buttons, or note the exception in CLAUDE.md.
- **Effort:** S · **Grade lift:** nit
