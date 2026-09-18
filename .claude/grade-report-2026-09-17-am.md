# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware + castle-core + Castle Radio
**Audited:** 2026-09-17 (regrade; the 2026-09-06 audit is archived at
`.claude/grade-report-2026-09-06.md`)
**Tree:** `c67d4ce` on `website-tools-launcher`. The branch is merged (PR #36)
and **0 ahead / 6 behind `origin/main`** — v5.63 (card-loaded cue files,
`d15b372`) and esphome 2026.9.0 are NOT in the graded tree; where an auditor
read them from `origin/main` the item says so. 90 commits and
+29,590/−9,486 lines since the tree the last report graded.
**Stack:** Rust (castle-core, zero deps, edition 2024) · Python 3.13 toolchain
· strict TypeScript desk · ESPHome/C++ firmware (S2 + two S3 targets) · plain
JS + Python "Castle Radio" site · one Swift URL-handler.

**How this was graded.** Run on this tree, not quoted from the last report.
`make check` **exit 0** — 1,109 Python tests, ruff clean, mypy clean over 154
files, LOC check 493 files (largest 490/500), citation check 147 all dated,
tsc clean, node suites green. `make rust-test` **153 passed**. `esphome
config` valid for all five firmware variants; 122 firmware host/contract tests
green. `make coverage` tools/ **84%** (floor 82); `make rust-coverage`
**57.03%** lines (was 40.06%). Playwright **148 tests in 20 files** (listed,
not run). `make audit` 0 vulns (10 ignored by id), `npm audit` 0. CI on main:
green — every merge commit SUCCESS, e2e gating on every PR.

**One thing went wrong while grading, and it is an item (D6).** The first
`make check` was RED: `studio::tests::scene_ids_reads_the_shows_own_file`
looked for `/private/tmp/hw-deps/scenes/scenes.yaml`. The test binary in
`core/target/release` (built 05:28 today, before this session) had another
checkout's `CARGO_MANIFEST_DIR` baked in via `env!`, and cargo called it
`Fresh`. `touch core/src/lib.rs` → rebuild → green. Not a code bug; a test
that a shared target dir can poison.

**Permanently accepted risk — do not re-raise.** Studio Origin/Host
validation; firmware OTA/file endpoint auth. E2 below is a *new* surface
(a second server a web page can start), reported as facts, not as a re-raise.

## Summary

| ID | Category | Grade | Was | Items |
|----|----------|-------|-----|-------|
| A | Architecture & Design | B+ | A− | 4 |
| B | Backend Quality | B | B+ | 5 |
| C | Frontend Quality | B+ | A− | 7 |
| D | Testing & Reliability | B+ | B | 7 |
| E | Security | B *(excl. accepted risk)* | B+ | 4 |
| F | Dependencies & Tech Currency | B+ | B+ | 5 |
| G | Performance & Scalability | B+ | A− | 5 |
| H | Documentation & Onboarding | B | B | 5 |
| I | Developer Experience & Tooling | B+ | B+ | 3 |
| J | Firmware | B+ | B | 7 |
| **Overall** | | **B+** | B+ | **52** |

**Top 5 highest-leverage fixes:** B1, E2, B2, J1, D2

The loop the last report said had slipped is back: main is green, everything
is pushed, the weekly firmware job can now fail, and 20 of the 37 items from
2026-09-06 are closed with tests. What moved the other way is **Castle
Radio**: ~7,600 lines of plain JS and a second Python HTTP server
(`demo/castle-radio/`, port 8871) arrived outside the repo's own standards —
no typecheck, no mypy (18 errors when pointed at it), outside the coverage
gate, a DOM stub that cannot miss an element, dense one-line files that
satisfy the 500-line rule in letter only, and zero mentions in CLAUDE.md. It
is careful code at runtime (origin-pinned bridge, escaped innerHTML, a
fixed-action launcher) — it just is not held to what `web/` and `core/` are.
And the process-kill class fixed in the JSON parser came back through the new
YAML parser (B1).

## Status of the 2026-09-06 items

Fixed (20): grade report 2026-09-06 A2, B1, B3 (at that site), D1, D2, D3, D4,
E1 (for JSON), F2, H1, H3, I1, I2, I4, J1, J2, J3, J4, J5, J6.
Partial (6): B2 (streaming exists; the Rust studio never turns it on — B4),
B4 (emulator ticker still has no `except`), D5, H2, H4, H5.
Open (11): A1 → J4, A3 → A3, C1/C2/C3 → C7, E2 → E3, F1 → F1, F3 → F2,
G1 → G1, G2 → G2, I3 → I1.

---

## A — Architecture & Design — B+

`tools/core_bins.py` is still the only door to the Rust bins; the parsers
split on real seams (`core/src/jsonio_parse.rs`, `yaml_parse.rs`,
`yaml_flow.rs`, `yaml_lines.rs`); the scene validator moved in-process
(`core/src/studio_check.rs`); `repo_root()` is re-anchored
(`core/src/studio.rs:213-229`). Down a notch because the repo that retired its
second server on 2026-09-06 has grown another one — and ships it to the user
as "Castle Studio" from a directory called `demo/`.

#### A1 — A second HTTP server lives in `demo/`, unlinted and unmentioned
- **Where:** `demo/castle-radio/server.py` (489 lines), `radio_jobs.py:113`, `pyproject.toml:43` (`files=["tools","tests"]`), `Makefile:311-313`.
- **What's wrong:** own job runner, own venv, port 8871, started by the Mac launcher — outside ruff/mypy scope and absent from CLAUDE.md and `docs/API.md`. Only `make test-radio` holds it.
- **Fix:** add it to lint/mypy scope (see D2), document it (H1), then either rename it out of `demo/` or write the plan to fold it into the Rust studio, as `docs/RETIREMENT.md` did for the last one.
- **Effort:** M
- **Grade lift:** B+ → A− (one product, one standard)

#### A2 — Two conventions for handing a link to the importer
- **Where:** `core/src/studio_import.rs:115,199` pushes the URL into argv; `demo/castle-radio/radio_jobs.py:227` uses `CASTLE_IMPORT_SOURCE`, and `tools/import_track.py:228` says "a link is data, and data does not belong in an argument list".
- **Fix:** the Rust studio passes it through the env too.
- **Effort:** S
- **Grade lift:** nit; removes a stated-principle violation

#### A3 — `core/src/wasm.rs` is still ungated (carried: grade report 2026-09-06 A3)
- **Where:** `core/src/lib.rs:70` unconditional `pub mod wasm;`; `core/src/wasm.rs:16` `unsafe impl Sync` justified by "WASM is single-threaded", compiled into every native bin.
- **Fix:** `#[cfg(target_arch = "wasm32")] pub mod wasm;`
- **Effort:** S
- **Grade lift:** nit; makes the SAFETY comment true

#### ~~A4~~ ✓ done 2026-09-17 — see J4 (delete `castle_sd_jewels.yaml`; carried: grade report 2026-09-06 A1)

---

## B — Backend Quality — B

The JSON depth guard is exemplary (`core/src/jsonio_parse.rs:26,49`, boundary
tests `:232-246`), lock poisoning is handled uniformly, the crate still has
one `#[allow]`, `http_parse.rs:128-141` refuses before allocating. Down a
notch because two fault-path defects were **reproduced** on this tree, one of
them the same class the last audit closed.

#### ~~B1~~ ✓ done 2026-09-17 — A deep YAML flow sequence aborts the whole studio process (reproduced)
- **Where:** `core/src/yaml_flow.rs:34-66` (`value` → `sequence`/`mapping`, no depth limit); block recursion `core/src/yaml_parse.rs:57-113` (same shape, not tested). Reached from `core/src/studio_check.rs:80` via `POST /studio/scene`.
- **What's wrong:** body `{"id":"zz","yaml":"  - id: zz\n    a: [[[…]]]"}` answers 400 at depth 5,000; at 20,000 (~40 KB) — `thread '<unknown>' has overflowed its stack / fatal runtime error`, process gone, every in-flight connection with it. `catch_unwind` at `core/src/bin/studio.rs:80` cannot catch an abort.
- **Fix:** thread a depth counter through both recursions reusing `MAX_DEPTH`; one test per parser mirroring `jsonio_parse.rs:232`; one golden row for the refusal string.
- **Effort:** S
- **Grade lift:** B → B+

#### ~~B2~~ ✓ done 2026-09-17 — The watchdog kills the child, then waits on pipes the grandchild still holds (reproduced)
- **Where:** `core/src/studio_proc.rs:112-126` (`child.kill()` then `out_t.join()`); `core/src/studio_jobs.rs:33` (`kill(pid,9)`, not a group kill).
- **What's wrong:** a fake `yt-dlp` running `sleep 85 & wait` under `POST /studio/probe`: the "timed out after 60s" reply arrived at 85.1 s. On the 900 s importer path the oplock stays held as long as a wedged ffmpeg/yt-dlp/demucs lives, and the orphan leaks.
- **Fix:** `CommandExt::process_group(0)` and `kill(-pgid, 9)` in both places — `tools/progress_process.py:15,31` already does exactly this on the Python side.
- **Effort:** M
- **Grade lift:** B → B+ (with B1: → A−)

#### ~~B3~~ ✓ done 2026-09-17 — The `compare` shim has no watchdog and runs under the oplock
- **Where:** `core/src/studio_probe.rs:286-296` bare `wait_with_output()`, called from `core/src/studio_routes.rs:311-313` holding `app.oplock`. Verified by reading.
- **Fix:** route through `run_split(cmd, 300)` with stdin fed by a writer thread.
- **Effort:** S
- **Grade lift:** closes the last un-watchdogged child

#### B4 — The studio's import progress still cannot fire (carried: grade report 2026-09-06 B2)
- **Where:** streaming now exists (`tools/progress_process.py`, `tools/import_fetch.py:57`) but only `demo/castle-radio/job_progress.py:73` sets `CASTLE_PROGRESS_STREAM`; nothing in `core/src` does.
- **Fix:** `cmd.env("CASTLE_PROGRESS_STREAM","1")` in `studio_jobs::run_child`.
- **Effort:** S
- **Grade lift:** nit; a progress bar that moves

#### B5 — The emulator ticker still dies silently (carried: grade report 2026-09-06 B4)
- **Where:** `tools/castle_emu.py:286-289` `try/finally`, no `except`; `int(arg)`/`int(cool)` still in `_apply`.
- **Fix:** catch, log, keep ticking.
- **Effort:** S
- **Grade lift:** nit

---

## C — Frontend Quality — B+

The cue desk still earns A− on its own: `web/tsconfig.json` strict with
`noUncheckedIndexedAccess` + `exactOptionalPropertyTypes`, 60 files max 440
lines, one AudioContext closed at `web/src/stems_view.ts:413`, reduced motion
honoured, 157 KB bundle in a 3.3 MB page (budget 4 MB). Castle Radio is
well-behaved at runtime — every innerHTML site escapes (`app.js:83`,
`imports.js:9`), the popup bridge checks `event.origin` and `event.source` on
both ends — but poorly tooled, and it is now most of the frontend by line
count.

#### C1 — ~2,200 lines of hand-written JS with no static checking
- **Where:** `demo/castle-radio/*.js` — 11 scripts sharing globals via `/* global … */`; `Makefile:217-220` runs tests only.
- **Fix:** `jsconfig.json` with `checkJs` + a `globals.d.ts`; `tsc -p demo/castle-radio` from `make lint`.
- **Effort:** M
- **Grade lift:** B+ → A−

#### C2 — Dense lines defeat the 500-line rule
- **Where:** `demo/castle-radio/style.css` (35 lines, one of 10,616 chars), `device-tools.css` (3,602), `imports.js:20` (1,295), `app.js:87` (1,028).
- **What's wrong:** `tools/check_loc.py` counts lines only; whole render functions sit on one line and diffs are unreviewable.
- **Fix:** add a max line length (~200) for non-exempt files to `check_loc.py`, then reformat.
- **Effort:** M
- **Grade lift:** restores the rule's intent

#### C3 — `visuals.js` is a tracked bundle with no freshness gate
- **Where:** `demo/castle-radio/visuals.js`; build command only in `demo/castle-radio/README.md:190-195`; not in `EXEMPT_PATHS`.
- **What's wrong:** editing `web/src/stage.ts`/`show.ts`/`rig.ts` silently stales it. Rebuilt to /tmp today: byte-identical, so latent.
- **Fix:** a test that rebuilds and compares; an `EXEMPT_PATHS` entry naming the generator.
- **Effort:** S
- **Grade lift:** nit → prevents a silent drift

#### C4 — Scene name and blurb reach innerHTML unescaped
- **Where:** `web/src/panels.ts:155-161`, `web/src/budget.ts:222-247`; `esc` exists at `web/src/dom.ts:81`.
- **What's wrong:** a blurb with `"` or `<` breaks the tile. Low risk (names derive from a slug).
- **Fix:** wrap in `esc()`.
- **Effort:** S
- **Grade lift:** nit

#### C5 — The castle's IP is hardcoded in markup
- **Where:** `demo/castle-radio/index.html:5,16`, `device-tools.js:52`, `device-words.js:18`; `device_site.py:89-97` patches by exact text replacement.
- **Fix:** render the host from `/radio/device`.
- **Effort:** S
- **Grade lift:** nit

#### C6 — Page switches move neither focus nor `aria-current`
- **Where:** `demo/castle-radio/app.js:145-146`.
- **Fix:** `aria-current="page"` on the active button; focus the new section's `h1`.
- **Effort:** S
- **Grade lift:** a11y nit

#### C7 — Three desk nits carried untouched (grade report 2026-09-06 C1, C2, C3)
- **Where:** `web/src/import_opts.ts:62-63` (`Math.round` renders "0:60"); `clamp` ×3 (`track_sections.ts:88`, `wave_analysis.ts:47`, `waveform_view.ts:58`); private exports `effects.ts:46`, `stage.ts:51`.
- **Fix:** one `mmss`, one `clamp`, un-export.
- **Effort:** S
- **Grade lift:** nit

---

## D — Testing & Reliability — B+

Up from B. Every gate defect from the last audit is closed (`ci.yml:35-37`
pipefail, `:418/439/450` RAM checks, `tools/fuzz_corpus.py:72` now poisons
0x7F–0xFF — the class that kept main red), CI is green with e2e gating, skips
are still guarded on `IN_CI`, and the real firmware headers still compile
against a fake IDF. Held off A− by the new code being tested to a lower
standard than the old.

#### D1 [FE] — The radio site's JS only runs against a DOM stub that cannot miss an element
- **Where:** `demo/castle-radio/test_support.mjs:16-36` — `$` auto-creates any id; `querySelector` always returns an element. No Playwright spec touches the site.
- **Fix:** one smoke spec serving `index.html` asserting no console errors and key controls wired; or a test that every static `$('id')` exists in the HTML.
- **Effort:** M
- **Grade lift:** B+ → A−

#### ~~D2~~ ✓ done 2026-09-17 [BE] — `demo/castle-radio` Python is outside mypy and the coverage gate
- **Where:** `Makefile:244,312-313`; `ci.yml:176-183`.
- **What's wrong:** `mypy demo/castle-radio/server.py` → **18 errors in 7 files** (e.g. `remote_library.py:80`); coverage is `--source=tools`, so 2,058 lines of Python are uncounted.
- **Fix:** add the directory to ruff, mypy and coverage scopes.
- **Effort:** M
- **Grade lift:** B+ → A− with D1

#### ~~D3~~ ✓ done 2026-09-17 [BE] — Tests that assert on source text
- **Where:** `demo/castle-radio/test_server_fixes.py:87-94` — five `inspect.getsource` assertions plus `parse_qs` on a literal.
- **Fix:** drive the handlers through the `_Caller` fake already at `:77`.
- **Effort:** S
- **Grade lift:** nit

#### D4 [both] — Coverage floors do not move; Rust coverage is blind to half the crate
- **Where:** `Makefile:236` (floor 82, measured 84, untouched since 2026-08-23), `Makefile:298-301`.
- **What's wrong:** `rust-coverage` runs in no CI job and reports 0% for `effects.rs`, `pulse.rs`, `overlay.rs`, `manifest.rs`, `studio_lean.rs` and 3% for `studio_import.rs` — all exercised only by Python gates spawning uninstrumented bins.
- **Fix:** floor → 83; run the `_rust`/`_rs` suites under `cargo llvm-cov show-env`; add a non-gating CI step.
- **Effort:** M
- **Grade lift:** makes the number mean something

#### D5 [FW] — Timing tests with thin margins and no retries
- **Where:** `tests/test_firmware_tick_cxx.py:161-173` (sleeps 0.6 s inside a 1.5 s window); `web/test/e2e/device.spec.ts:259,437`; `web/playwright.config.ts` has no `retries`.
- **Fix:** inject a clock into the harness tick, or poll to a deadline.
- **Effort:** M
- **Grade lift:** flake prevention

#### D6 [BE] — One Rust test bakes in a compile-time path, and it bit this audit
- **Where:** `core/src/studio.rs:281` `env!("CARGO_MANIFEST_DIR")`.
- **What's wrong:** see the header — a stale-but-`Fresh` test binary read another checkout's scenes.yaml and reddened `make check`.
- **Fix:** `std::env::var("CARGO_MANIFEST_DIR")` at run time (cargo sets it for `cargo test`).
- **Effort:** S
- **Grade lift:** removes a false red

#### D7 [BE] — The Swift launcher is never compiled or run by any gate
- **Where:** `tools/castle_launcher.swift`; `tests/test_castle_launcher.py` mocks `swiftc`; `tests/castle_launcher_process_test.swift` is referenced by no Makefile or CI line — the log-handle fix in `c67d4ce` is covered only by that orphan.
- **Fix:** a macOS-gated unittest that runs `xcrun swiftc -D CASTLE_LAUNCHER_TEST …` and executes it.
- **Effort:** S
- **Grade lift:** nit

---

## E — Security — B *(excluding the accepted risk)*

The launcher is a fixed-action design (`tools/castle_launcher.swift:47-48`
exact-match on `castle-tools://start`, no arguments, path from Info.plist);
`demo/castle-radio/companion.js:10-19,84-103` is a careful origin-pinned
bridge with route and header allow-lists; children are argv-only with `--`
before URLs; `server.py:92-105` never turns a request string into a path.
Down a notch for a 40 KB remote process-kill and an untouched staging race.

#### ~~E1~~ ✓ done 2026-09-17 — B1 from the network
- **Where/What:** in `--lan` mode ~40 KB to `POST /studio/scene` from anyone on the Wi-Fi ends the studio. Fix is B1's.
- **Effort:** S
- **Grade lift:** B → B+

#### ~~E2~~ ✓ done 2026-09-17 — What an arbitrary web origin can cause through the launcher (new surface, stated as facts)
- **Where:** `tools/castle_launcher.swift:47-65`; `demo/castle-radio/server.py:347` (`json_body` never checks Content-Type), `:436-462` (upload).
- **What's wrong:** any page can navigate to `castle-tools://start` (one browser prompt, "always allow" available) → `127.0.0.1:8871` comes up. Any origin can then send no-preflight "simple" POSTs: `/radio/import` writes up to 100 MB into `.radio-data/` and queues ffmpeg + Demucs over the posted bytes (unbounded queue, one worker); `/radio/device/command`, `/radio/device/sync`, `/radio/retry`, `/radio/reprocess`, `/radio/restore/*` likewise. Link imports and DELETE are blocked only because there is no `do_OPTIONS`. No argument injection was found; the launcher itself runs exactly one fixed command.
- **Fix:** require `Content-Type: application/json` in `json_body`; require a custom header (`X-Castle: 1`) on upload — the companion bridge can send it and it forces a preflight for everyone else; cap queue depth.
- **Effort:** S
- **Grade lift:** B → B+ (your call whether this falls under the accepted position; it is a different server than the one that position was written for)

#### E3 — Upload staging race (carried: grade report 2026-09-06 E2)
- **Where:** `core/src/studio_import.rs:130` stages into shared `_upload` before the lock at `:160`; `remove_dir_all` after at `:163`.
- **Fix:** stage under `_upload_<pid>_<rand>/` and remove only that.
- **Effort:** S
- **Grade lift:** nit under one operator

#### E4 — netguard: first hop only, fails open, drifted from the Python oracle
- **Where:** `core/src/netguard.rs:118-125` (unresolvable host allowed); `:55` classes `2002::/16`, Teredo and `2001:db8::/32` as public (CPython `is_global` says False for all three; `64:ff9b::` inverted). Rust side verified by reading.
- **Fix:** fail closed for non-loopback callers on empty resolution; add the ranges and corpus rows; document the redirect limit.
- **Effort:** S
- **Grade lift:** nit; only matters for non-loopback callers

---

## F — Dependencies & Tech Currency — B+

113 exact pins, three lockfiles, Dependabot on four ecosystems, zero
advisories, zero open PRs, esphome current on main.

#### F1 — The lock is a darwin freeze (carried: grade report 2026-09-06 F1)
- **Where:** `tools/lock_deps.py:92` (`sys.executable`); `requirements.lock:75-78` four darwin markers, zero linux.
- **Fix:** `uv pip compile --universal`, or `--no-deps` on CI's install.
- **Effort:** M
- **Grade lift:** B+ → A−

#### F2 — Five toolchain literals in CI not tied to the pin (carried: grade report 2026-09-06 F3)
- **Where:** `.github/workflows/ci.yml:71,136,238,304,373` vs `core/rust-toolchain.toml`.
- **Fix:** a 10-line test comparing them to `cargo_gate.pinned_channel()`.
- **Effort:** S
- **Grade lift:** nit

#### F3 — Rust pin 1.88.0 is ~15 months old and nothing bumps it
- **Where:** `core/rust-toolchain.toml`.
- **Fix:** bump; note a quarterly review date beside the pin.
- **Effort:** S
- **Grade lift:** nit

#### F4 — `@types/node ^26` against a Node 22 runtime
- **Where:** `web/package.json:18,23`; `.nvmrc`.
- **Fix:** pin `^22`.
- **Effort:** S
- **Grade lift:** nit

#### F5 — `types-PyYAML` is the one unpinned requirement
- **Where:** `requirements-dev.txt`.
- **Fix:** add `~=`.
- **Effort:** S
- **Grade lift:** nit

---

## G — Performance & Scalability — B+

The 30 s read timeout (`core/src/http_parse.rs:170`), Arc-shared caches and
the float-budgeted decode cache still stand. Down a notch: both old items
untouched, three new unbounded things.

#### G1 — The oplock is held across the network publish (carried: grade report 2026-09-06 G1)
- **Where:** `core/src/studio_scenes.rs:177` guard held through `publish_body` at `:188`.
- **Fix:** drop the guard before the publish.
- **Effort:** M
- **Grade lift:** B+ → A−

#### G2 — `job.log` and `castle-cmp-*` grow without bound (carried: grade report 2026-09-06 G2)
- **Where:** `core/src/studio_jobs.rs:190`; `core/src/studio_probe.rs:220`.
- **Fix:** 200-line ring; startup sweep of `temp_dir()/castle-cmp-*`.
- **Effort:** S
- **Grade lift:** nit

#### G3 — `startup.log` grows forever, fed by every poll
- **Where:** `tools/castle_launcher.swift:60-65` appends, never rotates; `server.py` has no `log_message` override, so each polled `/radio/jobs` writes a line to `~/Library/Logs/Castle Tools/startup.log`.
- **Fix:** truncate at launch past 1 MB; silence 2xx access lines.
- **Effort:** S
- **Grade lift:** nit

#### G4 — 100 MB uploads are fully buffered per thread
- **Where:** `demo/castle-radio/server.py:438` `rfile.read(length)`.
- **Fix:** stream to disk in 64 KB blocks after sniffing the first 128 bytes.
- **Effort:** S
- **Grade lift:** nit

#### G5 — No write timeout on responses
- **Where:** `core/src/http_parse.rs:170` sets read only; a client stalling mid-mp3 pins a thread (`--lan` only).
- **Fix:** `set_write_timeout(30s)`.
- **Effort:** S
- **Grade lift:** nit

---

## H — Documentation & Onboarding — B

Held. A script checked every backticked path in README, CLAUDE.md,
CONTRIBUTING and `docs/*.md`: exactly one genuinely stale; every cited `make`
target exists; `docs/API.md` matches `core/src/studio_routes.rs` both ways on
16 sampled routes. The launcher is documented well in
`demo/castle-radio/README.md:5-57`. But the file that governs the repo does
not know a third of the repo exists.

#### H1 — CLAUDE.md has zero mentions of Castle Radio, the launcher, or port 8871
- **Where:** `CLAUDE.md:14-80` (layout), `:95-110` (targets), the port warning.
- **What's wrong:** no `demo/`, `make test-radio`, the three `.command` files, `tools/castle_launcher.swift`, `tools/register_castle_launcher.py`, `tools/install_castle_tools.sh`, `CASTLE_RADIO_HOST`, `.venv-desktop`. Also `CLAUDE.md:37` says `tests/studio_rust_case.py` (it is `studio_rs_case.py`) and `:57` says neither S3 build "has been on hardware" (see J2).
- **Fix:** one layout bullet, the target, the port, the two corrections.
- **Effort:** S
- **Grade lift:** B → B+

#### H2 — Getting started omits hard prerequisites
- **Where:** `README.md:89-109`; `tools/render_audio.py:253` shells to `lame` with no preflight; `make studio` → `preview` dies without `web/node_modules`; `tools/gen_previewer.py:307` says `npm install` where CONTRIBUTING says `npm ci`.
- **Fix:** a prerequisites line (`brew install lame ffmpeg node`), a software-only path block, a `shutil.which("lame")` check with a clear message.
- **Effort:** S
- **Grade lift:** B → B+

#### H3 — The `/radio/*` surface (18 routes) is undocumented
- **Where:** `docs/API.md` has 0 hits for `/radio/`.
- **Fix:** route table in `demo/castle-radio/README.md`, linked from API.md.
- **Effort:** S
- **Grade lift:** nit

#### H4 — The design record's standings block is stale again (carried: grade report 2026-09-06 H2)
- **Where:** `docs/notes/05-decisions-and-roadmap.md:83` ("Two firmware targets" — three), `:89` ("907 Python tests" — 1,109).
- **Fix:** refresh; generate the counts with a command.
- **Effort:** S
- **Grade lift:** nit

#### H5 — One-liners
- **Where:** `docs/SECURITY.md:7` (port 8820 → 8765); `docs/PARITY.md:160` (cites `ci.yml:101`, export is at `:122` — cite the variable); `firmware/sd_audio.h:60` ("flash scenes" → the chirp); `make help` lists only one of `test-radio`/`test-fast`.
- **Effort:** S
- **Grade lift:** nit

---

## I — Developer Experience & Tooling — B+

The hook takes 1.2 s, error messages are written with care, `check-all`
exists, the weekly job is hand-dispatchable. Local gate: `make check` ≈ 3 min.

#### I1 — The cap is now shaping the code (carried, worse: grade report 2026-09-06 I3)
- **Where:** `tools/check_loc.py:49-56`. Eight files ≥ 480: `demo/castle-radio/test_castle_radio.test.mjs` 490, `.github/workflows/ci.yml` 484, `core/src/studio_routes.rs` 479, `castle-wiring.html` 479 (still not exempt)… Already 17 `studio_*.rs`, 9 `castle_emu*.py`, 9 `sd_web*.h`.
- **Fix:** split the closest six now on chosen seams; decide `castle-wiring.html`; pair with C2 so the rule measures density, not just newlines.
- **Effort:** M
- **Grade lift:** B+ → A−

#### I2 — `make setup` does not yield a venv that can run `make check`
- **Where:** `Makefile:77-94` installs `requirements.txt` not the lock; never runs `cd web && npm ci`, which `check` needs at `:321-322`.
- **Fix:** `-c requirements.lock`; add the `npm ci` step.
- **Effort:** S
- **Grade lift:** B+ → A− with I1

#### I3 — `make coverage` nests `make rust-test` inside the unit suite
- **Where:** `tests/test_castle_core.py:148-168`; one cargo collision fails a 3-minute run.
- **Fix:** honour `CI_RUST_GATES_ELSEWHERE` inside `make coverage`.
- **Effort:** S
- **Grade lift:** nit

---

## J — Firmware — B+

Up from B. All six firmware items from 2026-09-06 are fixed with tests;
`tests/cxx/` grew mailbox fuzzing and event/tick checks;
`firmware/sd_web_upload.h:107-136` is a three-rename upload that never leaves
the card without a complete copy. Held off A− by a reproduced name-handling
defect, a pending-patches file twelve versions behind, and a static the RAM
rule forbids.

#### ~~J1~~ ✓ done 2026-09-17 — A file with `+` or `%` in its name is listed and queued but can never play (reproduced on host)
- **Where:** `firmware/castle_sd_common.yaml:200,279` build the loopback URL raw; `firmware/sd_web_util.h:31-51` then URL-decodes it; `firmware/sd_web_site.h:157`.
- **What's wrong:** `a+b.mp3` passes `safe_name`, decodes to `a b.mp3` → 404. `100%.mp3` decodes to empty → 400. Clients encode `+` as `%2B` (`tools/sd_sync.py:61`, `web/src/api.ts:258`), so such files do land on the card. The emulator has no loopback hop, so parity tests cannot see it. v5.63's "any song on the card" widens the exposure.
- **Fix:** a `url_encode` helper in `sd_web_util.h` used by both `set_media_url` lambdas and generated `audio_sd.yaml` — or refuse `+ % # ?` in `safe_name` and the emulator together. Add a `web_check` case.
- **Effort:** S
- **Grade lift:** B+ → A−

#### ~~J2~~ ✓ done 2026-09-17 — `firmware/pending/README.md` stops at v5.50; three files say the S3 Feather was never on hardware
- **Where:** `firmware/pending/README.md:7`; `firmware/castle_feather_s3.yaml:30`; `CLAUDE.md:57`.
- **What's wrong:** v5.51–v5.62 absent (status handler to core 0, upload worker task, RTC event ring, SNTP); still opens "Pending: v5.42 … NOT yet flashed"; its own v5.50 entry and commit `a3fd0ac` say "Proven on the board over Wi-Fi".
- **Fix:** one table — version, change, which board it has run on. Correct the two claims.
- **Effort:** S
- **Grade lift:** B+ → A− with J1

#### ~~J3~~ ✓ done 2026-09-17 — The event ring is 4 KB of static dram0 on the S2
- **Where:** `firmware/sd_web_state.h:109-128` — `std::array<Event,64>` × 64 B, no RAM accounting in the header (where `castle_rtc.h` carries its own). Rule: "stack-only, PSRAM for buffers, no new statics". ≈ half a scene of headroom.
- **Fix:** allocate once with `heap_caps_malloc(MALLOC_CAP_SPIRAM)` as `boot_log.h` does, smaller internal fallback.
- **Effort:** S–M
- **Grade lift:** buys back headroom at the cliff

#### ~~J4~~ ✓ done 2026-09-17 — Delete `castle_sd_jewels.yaml` (carried: grade report 2026-09-06 A1)
- **Where:** `firmware/castle_sd_jewels.yaml:7-11,21-22` — a false "DO NOT flash" header over values `firmware/castle.yaml:57,69` already hold; still in the CI loop at `.github/workflows/ci.yml:327`. `esphome config` differs from `castle_sd` only by key order.
- **Fix:** delete; drop from the loop.
- **Effort:** S
- **Grade lift:** nit

#### ~~J5~~ ✓ done 2026-09-17 — Upload sidecar suffixes collide with real file names
- **Where:** `firmware/sd_web_upload.h:75,122-123` — uploading `X` unlinks `X.old` and writes `X.part`; `safe_name` allows both, `h_list` shows them. Verified by reading.
- **Fix:** refuse names ending `.part`/`.old` in `h_put`, mirrored in the emulator.
- **Effort:** S
- **Grade lift:** nit

#### ~~J6~~ ✓ done 2026-09-17 — The generated S3 light file's banner is wrong for the Feather
- **Where:** `firmware/generated/lights_s3.yaml:11`; `tools/gen_rig.py:452` — says "1 block(s) spare"; `castle_feather_s3.yaml` spends that block on the status pixel. Only a unit test catches an overspend, not the generator.
- **Fix:** state both builds' spend, or run the reserved-blocks check for the Feather in the generator.
- **Effort:** S
- **Grade lift:** nit

#### ~~J7~~ ✓ done 2026-09-17 — v5.63 cue loader (on `origin/main`, NOT in this tree): sound, two notes
- **Where:** `origin/main:firmware/castle_cues.h`. Record cap, exact-length and magic checks, PSRAM-failure handling, downstream clamps all hold.
- **What's wrong:** `load()` reads up to 512 KB in one `fread` on the main loop at song start — the moment `docs/ISSUE-scene-start-audio.md` is still open on; `level` is unclamped (u8 254 → 2.54).
- **Fix:** chunked read with a yield; clamp `level` ≤ 1.0.
- **Effort:** S
- **Grade lift:** nit

**Carried, unverifiable here:** the 32 KB watchdog cadence in `write_body`
(`firmware/sd_web_upload.h:99`) is still emulator-verified only, and now runs
on a priority-4 worker task.
