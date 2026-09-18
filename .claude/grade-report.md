# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware + castle-core + Castle Radio
**Audited:** 2026-09-17 pm (regrade of the same day's morning audit, archived at
`.claude/grade-report-2026-09-17-am.md`; the 2026-09-06 report is at
`.claude/grade-report-2026-09-06.md`). **Citation rule for this pair:** a
citation in the tree reading `grade report 2026-09-17 X` refers to the AM
report — every such citation was written before this one existed. Cite THIS
report as `grade report 2026-09-17 pm X`.
**Tree:** `d15b372` on `grade-2026-09-17-top5` plus **141 uncommitted paths**
(+4,390/−4,666 lines against HEAD): the AM top-five (B1, E2, B2, J1, D2) and
B3/D3/J2–J7; the ESP32-S2 retirement (v5.65); mDNS back on (v5.66); scenes
moved from generated ESPHome scripts to card files (v5.67, v5.68). Nothing is
committed; the grade is of the working tree.
**Stack:** Rust (castle-core, zero deps) · Python 3.13 toolchain · strict
TypeScript desk · ESPHome/C++ firmware, ONE production target (ESP32-S3
Feather #5477) plus the unbuilt WROOM carrier · plain-JS + Python Castle Radio
(port 8871) · one Swift URL-handler.

**How this was graded.** Five auditors, one per category cluster, each
re-verifying every open AM item on this tree and hunting new defects with
file:line evidence; the firmware regression (J2) was re-derived by hand from
`castle_scenes.h` and `castle_scenes.yaml` before it went in. `make check`
**exit 0** on this tree — 1,164 Python tests, ruff clean, mypy clean over 183
files, LOC 510 files (largest 496/500), citations 204 all dated, tsc clean,
node suites green. `cargo llvm-cov` **57.55% lines**. `make coverage-radio`
95 tests, 68% (floor 67). `pip-audit` on the lock: only the five accepted
starlette ids; `npm audit` 0. Hardware: v5.64–v5.68 flashed over Wi-Fi today,
25 boots / 0 crashes; RAM 35.1% of 341,760 B, flash 67.9% of the OTA slot.

**Permanently accepted risk — not raised:** studio Origin/Host validation;
firmware `PUT /api/ota` and `/api/files/` auth.

## Summary

| ID | Category | Grade | AM | Items |
|----|----------|-------|----|-------|
| A | Architecture & Design | B+ | B+ | 5 |
| B | Backend Quality | B+ | B | 5 |
| C | Frontend Quality | B | B+ | 8 |
| D | Testing & Reliability | B+ | B+ | 7 |
| E | Security *(excl. accepted risk)* | B+ | B | 3 |
| F | Dependencies & Tech Currency | B+ | B+ | 6 |
| G | Performance & Scalability | B | B+ | 6 |
| H | Documentation & Onboarding | B | B | 7 |
| I | Developer Experience & Tooling | B | B+ | 5 |
| J | Firmware | B+ | B+ | 6 |
| **Overall** | | **B+** | B+ | **58** |

**Top 5 highest-leverage fixes:** J2, J1, D4, B1, E1

The morning's top five all landed and were verified on the board, and the
day's big move — scenes as card data behind one generic runner — is the best
architectural change in the tree. The overall grade holds at B+ rather than
rising because the same day shipped three regressions with tests looking at
them: a scene that ENDS never frees its cue blob or clears `cues` (J2, a
v5.67 regression on every non-looping scene); the new process-group watchdog
detached every child from the terminal, so Ctrl-C on the studio now orphans a
running Demucs (B1); and `sd_sync` deletes a renamed scene's cue file before
the manifest that names it (D4). Three categories moved down: C because
Castle Radio's browser half, now the larger by line count, still has no type
check, no browser test and no fetch timeouts and gained an untested header
contract today; G because publish now re-downloads ~8 MB off the card under
the oplock to decide it has nothing to send; I because both bespoke guards
(citations, 500 lines) have holes the docs say they do not.

## Status of the 2026-09-17 am items

Fixed (17): grade report 2026-09-17 am A4, B1, B2, B3, D2, D3 (that file), E1,
E2, H1, J1, J2, J3, J4, J5, J6, J7, and the tooling half of A1.
Carried, renumbered here: A1 → A1, A2 → A5, A3 → A4, B4 → B2, B5 → B3, C1–C7 →
C1, C5, C4, C8, C7, C7, C8; D1 folded into C2, D4 → D1, D5 → D5, D6/D7 →
D7, E3 → E3, E4 → E1, F1–F5 → F1–F5, G1 → G2, G2 → B5/G4, G3/G5 → G6, G4 → G5,
H2 → H3, H3 → H6, H4 → H2, H5 → H7, I1 → I3, I2 → I4, I3 → I5.

---

## A — Architecture & Design — B+

Held. A scene is now card data (`tools/scene_manifest.py`,
`tools/gen_scene_cards.py`, read by `firmware/castle_scenes.h`), one reader
serves scene and raw-song cue files (`castle_scenes.h:298` delegates to
`castle_cues::load_at`), and the ceiling's four copies are held equal by a
test (`tests/test_firmware_contract_scenes.py:47-68`). The S2 was removed,
not nursed. Not lifted: the AM headline (a second server shipped from
`demo/`) is only tooling-fixed, and the OTA gate gained a machine literal.

#### A1 — Still two servers and one product (carried: grade report 2026-09-17 am A1)
- **Where:** `demo/castle-radio/server.py:495`, `radio_jobs.py:38` (own
  executor), vs `core/src/bin/studio.rs`; `docs/API.md` has zero `/radio/` rows.
- **What's wrong:** the repo that wrote `docs/RETIREMENT.md` to delete its
  second server runs another one, with its own jobs, port and routes, out of
  `demo/`. Fixed since AM: `pyproject.toml:49`, `Makefile:362`,
  `Makefile:293-297`, CLAUDE.md's Castle Radio paragraph.
- **Fix:** move it out of `demo/` under its real name and write the one-page
  boundary or fold-in plan; route table into `docs/API.md` (H6).
- **Effort:** M
- **Grade lift:** B+ → A−

#### A2 — The OTA gate hardcodes one machine's build volume
- **Where:** `firmware/build_path.yaml:21` and `tools/check_image.py:55`
  (`_roots()` restates `/Volumes/512Flash/esphome-builds/…`).
- **What's wrong:** `make ota` flashes whatever `check_image.py --path` names.
  Point `build_path.yaml` elsewhere (its header invites it) and the gate finds
  no image; `make ota` fails, or `sd_sync ota`'s glob hands over a stale binary.
- **Fix:** have `check_image.py` read the `build_path:` line from
  `firmware/build_path.yaml`; keep `firmware/.esphome/build` as fallback.
- **Effort:** S
- **Grade lift:** removes a silent wrong-binary path

#### A3 — A cross-language parity contract guards dead code
- **Where:** `tools/pulse_dynamics.py:176-179` `PULSE_CAP`/`thin_pulses`;
  `core/src/pulse.rs:102`; `core/src/bin/pulse_dump.rs:97`; gates
  `tests/test_pulse_rust.py:114`, `tests/test_pulse_cap.py`,
  `tests/test_card_cues.py:34`.
- **What's wrong:** `tools/gen_previewer.py:225` says "nothing in the show
  calls it any more". Two implementations, a dump bin and three test files
  for a function no producer calls; part of why Rust coverage reads low.
- **Fix:** delete both implementations and their gates, or keep one and
  demote the parity test to a unit test with a comment saying what for.
- **Effort:** S
- **Grade lift:** nit

#### A4 — `core/src/wasm.rs` still compiled into every native bin (carried: grade report 2026-09-17 am A3)
- **Where:** `core/src/lib.rs:70`.
- **Fix:** `#[cfg(target_arch = "wasm32")] pub mod wasm;`.
- **Effort:** S
- **Grade lift:** nit

#### A5 — A link goes to the importer in argv from Rust, in the env from Python (carried: grade report 2026-09-17 am A2)
- **Where:** `core/src/studio_import.rs:115,199`; `radio_jobs.py:227`;
  principle at `tools/import_track.py:228`.
- **Fix:** env from Rust too.
- **Effort:** S
- **Grade lift:** nit

---

## B — Backend Quality — B+

Up from B. The three reproduced fault paths are closed properly: `MAX_DEPTH`
shared across JSON and both YAML recursions (`core/src/yaml.rs:53`,
`yaml_flow.rs:41-46`, `yaml_parse.rs:71-120`, boundary tests
`yaml_flow.rs:212-231`); children in their own process group with a
group kill and a grandchild reproduction test (`studio_proc.rs:28-43,142,
179,262-300`); the `compare` shim under `run_input(…, 300)`. The manifest
reader is the crate's usual standard. Held off A− by a regression the group
change introduced.

#### ~~B1~~ ✓ done 2026-09-17 — The group fix orphans long children when the studio dies (new today)
- **Where:** `core/src/studio_proc.rs:28-31,142`; `studio_jobs.rs:154` call
  `own_group` on every child; `kill_group` runs only on watchdog expiry
  (`studio_proc.rs:179`, `studio_jobs.rs:179`); no signal handler or shutdown
  reaper anywhere in `core/src/bin/studio.rs` or `studio*.rs`.
- **What's wrong:** `process_group(0)` detaches children from the terminal's
  group, so Ctrl-C on `make studio` (or quitting the launcher) no longer
  reaches a running ffmpeg/yt-dlp/demucs. A 15-minute separation now survives
  the server that started it; before today it took the terminal's SIGINT.
- **Fix:** keep a `Mutex<Vec<i32>>` of live group leaders in `App` (push on
  spawn, remove on reap); on SIGINT/SIGTERM `kill_group` each, then exit. One
  test mirroring `studio_proc.rs:283-297` that the pid is gone after the signal.
- **Effort:** M
- **Grade lift:** B+ → A−

#### B2 — The studio's import progress still cannot move (carried: grade report 2026-09-17 am B4)
- **Where:** `CASTLE_PROGRESS_STREAM` read at `tools/import_fetch.py:57`,
  `tools/stems.py:175`; set only by `demo/castle-radio/job_progress.py:74`;
  `core/src/studio_jobs.rs:144-155` never sets it.
- **Fix:** `cmd.env("CASTLE_PROGRESS_STREAM", "1")` in `run_child`.
- **Effort:** S
- **Grade lift:** a progress bar that moves

#### B3 — The emulator ticker still dies silently, and now does card I/O under the lock (carried, worse: grade report 2026-09-17 am B5)
- **Where:** `tools/castle_emu.py:262-265` `try/finally` with no `except`;
  `_apply` at `:335` does `int(arg)` and, since v5.67, reads `show.man` and a
  cue file (`:298-312,363-370`) inside `with st.lock`.
- **Fix:** `except Exception:` log and continue; read files outside the lock.
- **Effort:** S
- **Grade lift:** removes a silent stall class in the contract tests

#### B4 — The JSON half of `/radio/import` inherits the 100 MB cap
- **Where:** `demo/castle-radio/server.py:451-460` → `link_job` `:424`
  `json.loads(self.rfile.read(length))` with `length` from `upload_length()`
  (`:347-353`, cap `UPLOAD_LIMIT`); `json_body`'s 4 KB ceiling (`:330`) bypassed.
- **Fix:** refuse `length > 4096` in the JSON branch.
- **Effort:** S
- **Grade lift:** nit

#### B5 — Studio `job.log` unbounded; `castle-cmp-*` leaks across crashes (carried, partly: grade report 2026-09-17 am G2)
- **Where:** `core/src/studio_progress.rs:19` pushed at `studio_jobs.rs:188`;
  `studio_reason.rs:44` scans all of it; `studio_probe.rs:245-251` LRU of 3
  but no startup sweep of `temp_dir()/castle-cmp-*`.
- **Fix:** cap `log` at ~500 lines; sweep day-old `castle-cmp-*` at startup.
- **Effort:** S
- **Grade lift:** nit

---

## C — Frontend Quality — B

Down from B+. The desk is unchanged and strong: strict tsconfig, every fetch
under `AbortSignal.timeout` (`web/src/api.ts:110`), DOM lookups gated
(`web/test/dom_discipline.ts:19`), a real a11y gate
(`web/test/e2e/a11y.spec.ts:33-44`). Castle Radio's browser half — 2,148
lines across 12 scripts — has none of that, all seven AM items carry, and
today's `X-Castle` header contract touched three of its files with no
client-side test.

#### C1 — Castle Radio's browser half has no static checking (carried: grade report 2026-09-17 am C1)
- **Where:** `demo/castle-radio/*.js`; `Makefile:363-373` (tsc runs for
  `web/` only); seven `/* global … */` headers (`imports.js:5`, `preview.js:2`,
  `device-link.js:3`, `remote-library.js:2`, `castle-direct.js:10`,
  `device-tools.js:2`, `rig-options.js:2`).
- **What's wrong:** cross-file coupling by bare global (`preview.js:2` names
  14); a rename is a runtime `ReferenceError` on the porch.
- **Fix:** `demo/castle-radio/jsconfig.json` with `checkJs` + a `globals.d.ts`;
  `npx tsc -p demo/castle-radio --noEmit` in `check`.
- **Effort:** M
- **Grade lift:** B → A−

#### C2 — The radio site has zero browser coverage, so the a11y gate stops at `web/src`
- **Where:** `web/test/e2e/*.spec.ts` (22 specs, none for the radio);
  `demo/castle-radio/test_support.mjs:17-36` stubs `element()` with no focus
  and a no-op `classList.add`.
- **What's wrong:** class/focus/ARIA behaviour is unobservable by
  construction; it is why C7 could ship and stay. (Absorbs grade report
  2026-09-17 am D1.)
- **Fix:** one Playwright spec serving `demo/castle-radio`, reusing the
  `UNNAMED` probe from `a11y.spec.ts`, asserting `aria-current` and focus on
  nav; fix C7 alongside.
- **Effort:** M
- **Grade lift:** B → B+

#### C3 — Today's `X-Castle` client contract has no client-side test
- **Where:** `demo/castle-radio/imports.js:45-46`, `preview.js:235`,
  `companion.js:11` vs `request_guard.py:16,40-51`, `server.py:340,392,464`.
- **What's wrong:** `/radio/import` and `/radio/restore/<key>` now 403
  without the header; the only test touching `imports.js` slices one function
  out by string search (`test_castle_radio.test.mjs:150-151`); `preview.js`
  is loaded by no test. Dropping the header in either minified one-liner
  breaks import and undo-delete with only a 403 to show for it.
- **Fix:** a source-scan test asserting every POST to a guarded route carries
  `X-Castle` or `application/json`, plus a vm-harness run of `uploadAudio`
  against a stub `fetch` asserting the header.
- **Effort:** S
- **Grade lift:** prevents a same-day regression

#### C4 — Two tracked derived artifacts, no generator, no freshness gate (carried, extended: grade report 2026-09-17 am C3)
- **Where:** `demo/castle-radio/visuals.js` (build command only in
  `README.md:208-214`) and `demo/castle-radio/scenes.json` (147 KB, one line,
  no command anywhere; mirrors `scenes/scenes.yaml` field-for-field).
- **What's wrong:** both in sync today (rebuilt `visuals.js`: byte-identical;
  `scenes.json` ids/durations match) — drift waiting, not drift.
- **Fix:** a `scenes.json` emitter under `tools/` (same data
  `gen_previewer.py` inlines); a test that rebuilds both and compares;
  `EXEMPT_PATHS` entries naming each generator.
- **Effort:** M
- **Grade lift:** removes a silent-drift class

#### C5 — Dense lines defeat the 500-line rule (carried, worse: grade report 2026-09-17 am C2)
- **Where:** `tools/check_loc.py:180` counts `splitlines()`; `scenes.json`
  1 line / 147,460 chars; `style.css` 35 / 10,616; `index.html` 18 / 4,901;
  `imports.js:20` 1,295; `app.js:87` 1,028. The desk's worst line is 226.
- **Fix:** max-line-length in `check_loc.py` (~200 chars, non-exempt files),
  then `prettier --write demo/castle-radio/*.{js,css,html}` once, after C1.
  Shares the mechanism with I2.
- **Effort:** M
- **Grade lift:** restores the rule's intent

#### ~~C6~~ ✓ done 2026-09-17 — No radio fetch has a timeout; one hung request kills the 2 s poll forever
- **Where:** `demo/castle-radio/imports.js:10,32` (`request`, `refresh`/
  `pollBusy`), `preview.js:13`, `device-tools.js:136`, `remote-library.js:93`,
  `companion.js:110`. Zero `AbortSignal` hits.
- **What's wrong:** `pollBusy` is cleared in `finally`; a fetch that never
  settles leaves it true and `setInterval(refresh, 2000)` silently stops.
  `request` also parses JSON before checking `r.ok`.
- **Fix:** `AbortSignal.timeout` in `request` and the companion relay; check
  `r.ok` first with a text fallback.
- **Effort:** S
- **Grade lift:** removes a silent-stall failure mode

#### C7 — Radio page switches move neither focus nor `aria-current`; castle IP hardcoded in markup (carried: grade report 2026-09-17 am C6, C5)
- **Where:** `app.js:145` `page()` toggles `.selected` only; no `aria-current`
  or `role=` in `index.html`; IP at `index.html:5,16`, `device-tools.js:52`,
  `device-words.js:18`; `device_site.py:89-97` rewrites by exact string.
- **Fix:** `aria-current="page"` + focus the section `h1`; one `data-castle`
  source for the address.
- **Effort:** S
- **Grade lift:** nit → closed by C2's test

#### C8 — Desk nits (carried: grade report 2026-09-17 am C4, C7)
- **Where:** unescaped innerHTML at `web/src/panels.ts:155,158,161`,
  `budget.ts:213,224,230-234,245-248` (`esc` at `dom.ts:81`); `mmss`
  `Math.round(s % 60)` → `0:60` at `import_opts.ts:62-63`; `clamp` ×3
  (`wave_analysis.ts:47`, `track_sections.ts:88`, `waveform_view.ts:58`);
  dead exports `PALETTES` (`effects.ts:46`), `APERTURE` (`stage.ts:51`).
- **Fix:** wrap in `esc()`; `Math.floor`; one `clamp`; drop two `export`s.
- **Effort:** S
- **Grade lift:** nit

---

## D — Testing & Reliability — B+

Held. Today's tests are real: `tests/test_firmware_web_storm.py` fires 2,000
seeded names at the compiled firmware and the emulator with a differential
`--rules` mode in 2.1 s; `tests/test_jsonio_depth_rust.py` asserts the
process survives; `studio_proc.rs:257-300` reproduces the grandchild kill
with margin. Held by: `make check` is documented as CI and is not; the day's
own `heal_missing` fix is asserted as a signature string; a shipped ordering
bug in `sd_sync.py` sits under a test that looks straight at it.

#### D1 [BE] — Rust coverage is 57.55%, has no CI step and no floor; routes at 19% (carried, quantified: grade report 2026-09-17 am D4)
- **Where:** `Makefile:270` `COVERAGE_MIN := 82` (unmoved since 2026-08-23);
  no `llvm-cov` in `ci.yml`. Measured: `studio_routes.rs` 18.97%,
  `studio_import.rs` 3.10%, `studio_relay.rs` 27.09%; `effects.rs`,
  `pulse.rs`, `overlay.rs`, `manifest.rs`, `studio_lean.rs`, `palette.rs` 0%
  (exercised only through uninstrumented spawned bins).
- **Fix:** run the `_rust`/`castle_core` suites under `cargo llvm-cov
  show-env` so spawned bins report; non-gating CI summary; `COVERAGE_MIN` 83.
- **Effort:** M
- **Grade lift:** B+ → A−

#### ~~D2~~ ✓ done 2026-09-17 [both] — `make check` is not CI: the radio's 95 tests and 7 node suites are outside it
- **Where:** `Makefile:368` `check: audio test lint`; `test-radio` at
  `:251-254`; CI runs "castle radio tests" and "castle radio coverage";
  CLAUDE.md says `check` (= CI).
- **What's wrong:** the documented hand-back rule can pass work that reddens
  three CI steps — in the directory where today's security fix landed.
- **Fix:** `check: audio test test-radio lint`; add `coverage-gate` or drop
  the "= CI" claim.
- **Effort:** S
- **Grade lift:** the gate stops lying

#### D3 [FW] — `heal_missing` (v5.68) is asserted as a C++ signature string and never run
- **Where:** `tests/test_firmware_contract_scenes.py:81-82` (`assertIn` of the
  declaration + `hasattr` on the emulator); impl `firmware/sd_web_state.h:
  184-197`; only caller `firmware/castle_scenes.yaml:123-124` (YAML, which no
  gate compiles); `tests/cxx/scenes_check.cpp`'s `missing` op drives
  `missing_csv`, not the status string.
- **Fix:** `note:`/`heal:`/`status-missing` ops in `scenes_check.cpp`, the
  note→heal→empty cycle on both castles; grep the YAML call site in the test.
- **Effort:** S
- **Grade lift:** the day's fix becomes a tested fix

#### ~~D4~~ ✓ done 2026-09-17 [FW/BE] — `sd_sync` deletes a stale cue file BEFORE publishing the manifest, breaking its own invariant
- **Where:** `tools/sd_sync.py:196-199` (DELETE loop) runs before `:202`
  (`show.man` PUT); the invariant is stated at `:134-138` and `:200-201`;
  `tests/test_sd_sync.py:323-340` asserts DELETE membership, not position.
- **What's wrong:** on a rename, `gone.cue` is deleted while the old
  `show.man` still names `gone`; a reboot or PIR in that window arms a scene
  whose cue file is gone (audio, no lights — the partial failure `missing`
  exists to report). The studio publishes after every scene save.
- **Fix:** move the delete loop after the `show.man` upload; assert the
  DELETE index is past `show.man`'s in one ordered call log.
- **Effort:** S
- **Grade lift:** closes a shipped ordering bug and the blind spot

#### D5 [FW] — Wall-clock sleeps inside a 1.5 s firmware window; no Playwright retries (carried: grade report 2026-09-17 am D5)
- **Where:** `tests/test_firmware_tick_cxx.py:164,176,204,212,233,242` vs
  `kSoundWaitUs` at `firmware/castle_cues.h:66`; `web/playwright.config.ts:
  52-53`.
- **Fix:** inject the clock into the tick harness or poll to a deadline;
  `retries: process.env.CI ? 2 : 0`.
- **Effort:** M
- **Grade lift:** flake prevention

#### D6 [BE] — The radio's HTTP surface is 53% covered one day after entering the gate
- **Where:** `make coverage-radio`: `server.py` 53%, `radio_jobs.py` 54%,
  `light_show.py` 57%; `request_guard.py` 97%.
- **Fix:** per-file floors for `server.py` and `radio_jobs.py` through the
  `_Caller` fake at `test_server_fixes.py:77`.
- **Effort:** M
- **Grade lift:** with D2, real

#### D7 [BE] — Compile-time path in a Rust test; Swift launcher test never run (carried: grade report 2026-09-17 am D6, D7)
- **Where:** `core/src/studio.rs:282` `env!("CARGO_MANIFEST_DIR")`;
  `tests/castle_launcher_process_test.swift` named by no gate.
- **Fix:** run-time `env::var`; a macOS-gated unittest that `swiftc`s and runs it.
- **Effort:** S each
- **Grade lift:** removes a false red; covers a real fix

---

## E — Security — B+ *(excluding accepted risk)*

Up from B. Both AM items closed properly: depth bounded in all three parsers
with one constant and a survives-the-body black-box; the radio's
cross-origin surface is un-simple by construction (`request_guard.py`,
415/403/429 asserted against real handlers), upload suffix from magic bytes.
New card/scene surfaces checked and sound: `client_ip` is the socket peer
(`core/src/http_parse.rs:167`); `sd_web_upload.h:246-258` `safe_name` +
`.part`/`.old` refusal; `device_bridge.py:259-305` allow-list per action.
Held off A− by the netguard, whose differential gate misses exactly the
ranges it has drifted on.

#### ~~E1~~ ✓ done 2026-09-17 [BE] — netguard fails open on an unresolvable host, and the Rust/Python drift is real (carried, confirmed: grade report 2026-09-17 am E4)
- **Where:** `core/src/netguard.rs:118-124` (`find(!is_public)` over an empty
  vector → allowed), `:55` (`2000::/3` shortcut); oracle `tools/netguard.py:
  33-46,96-99`; corpus `tests/test_netguard_rust.py` has no row for `2002::`,
  `2001:db8::`, `100::`, `64:ff9b::`.
- **What's wrong:** (1) a name that does not resolve is allowed for any
  caller, in both twins. (2) CPython says `2002::1`, `2001:db8::1`, `2001::1`,
  `100::1` are not global; the Rust calls all four public; `64:ff9b::7f00:1`
  is the inverse. (3) The gate cannot see either.
- **Fix:** fail closed for non-loopback callers on empty resolution (both
  sides); explicit private list replacing the `/3` shortcut; route
  `64:ff9b::/96` through its embedded v4; one corpus row per range.
- **Effort:** S
- **Grade lift:** B+ → A−

#### E2 [FW] — A manifest id's characters are trusted where everything else about the file is not
- **Where:** `firmware/castle_scenes.h:128` `cue_path()` concatenates the id;
  `find()`/`ids_csv()`/`missing_csv()` at `:154-193` check magic, version,
  entry size, length and NUL but never character class;
  `tools/scene_manifest.py:73-83` likewise; the only `is_ident` is the
  studio's (`core/src/scene_schema.rs:139`). See also J4 (same site).
- **What's wrong:** a hand-written or `PUT` manifest gets `../../foo` read as
  `/sd/scenes/../../foo.cue`; a comma splits `ids_csv` into names `find()`
  never matches; `sd_sync.py:196-199` will DELETE by a name that came off the
  device. Read-only, one operator — low impact, but the header argues
  defence-in-depth and stops one field short.
- **Fix:** one `row_ok()` (`[A-Za-z0-9_.-]`, both fields) used by all three
  walkers; the same refusal in `scene_manifest.decode` and the emulator; a
  corpus row in `tests/test_scene_manifest_cxx.py`.
- **Effort:** S
- **Grade lift:** closes the last unvalidated path from card bytes to the wire

#### E3 [BE] — Upload staging race (carried: grade report 2026-09-17 am E3)
- **Where:** `core/src/studio_import.rs:130-131,159-163`.
- **Fix:** `_upload_<pid>_<rand>/`, remove only that directory.
- **Effort:** S
- **Grade lift:** nit

Dependency posture: `pip-audit` on the lock returns exactly the five accepted
starlette ids (`.pip-audit-ignore`, review 2026-10-01); `npm audit` 0.

---

## F — Dependencies & Tech Currency — B+

Held; nothing moved. 113 exact pins, a v3 npm lock, an empty-by-design
`Cargo.lock`, Dependabot on four ecosystems with reasoned ignores
(`.github/dependabot.yml:16-33`), the esphome pin single-sourced from
`requirements.txt` by CI (`ci.yml:302`), zero open Dependabot PRs. Off A−
because every currency lever is manual and none has been pulled.

#### F1 — The lock is a darwin freeze with a concrete Linux hole (carried: grade report 2026-09-17 am F1)
- **Where:** `tools/lock_deps.py:40-44,92`; `requirements.lock:12`
  (`bleak==3.0.2`, whose Linux extra `dbus-fast` is absent from the lock);
  `ci.yml:149,368` install it on `ubuntu-latest`.
- **Fix:** `uv pip compile --universal`, or lock per platform and merge.
- **Effort:** M
- **Grade lift:** B+ → A− with F6

#### F2 — Five CI toolchain literals tied to the Rust pin by comment only (carried: grade report 2026-09-17 am F2)
- **Where:** `ci.yml:71,136,242,308,379` vs `core/rust-toolchain.toml:20`;
  helper `tests/cargo_gate.py:32 pinned_channel()` already exists.
- **Fix:** ~10 lines in `tests/test_castle_core.py` asserting each literal
  equals the pin.
- **Effort:** S
- **Grade lift:** closes the last unchecked pin

#### F3 — Rust pin 1.88.0, ~15 months old, no review date (carried: grade report 2026-09-17 am F3)
- **Where:** `core/rust-toolchain.toml:20`.
- **Fix:** a review date beside the channel, `.pip-audit-ignore` style.
- **Effort:** S
- **Grade lift:** nit

#### F4 — `@types/node ^26` against a Node 22 runtime (carried: grade report 2026-09-17 am F4)
- **Where:** `web/package.json:18`; `.nvmrc` 22; `ci.yml:170,221`.
- **Fix:** `"@types/node": "^22"`.
- **Effort:** S
- **Grade lift:** nit

#### F5 — `types-PyYAML` is the one unpinned requirement (carried: grade report 2026-09-17 am F5)
- **Where:** `requirements-dev.txt:4`.
- **Effort:** S
- **Grade lift:** nit

#### F6 — 113 pins, no hashes
- **Where:** `requirements.lock`; installed at `ci.yml:149,368`.
- **What's wrong:** a version pin without a hash pins a name, not bytes.
- **Fix:** `uv pip compile --generate-hashes` through `lock_deps.py`; pair
  with F1.
- **Effort:** M
- **Grade lift:** nit alone; the B+ → A− pair with F1

---

## G — Performance & Scalability — B

Down from B+. All five AM items open, and today added two on the path the
user waits on: publish now pulls ~8 MB back off the card under the oplock to
prove it has nothing to send, and un-capping pulses made the desk's per-frame
cue scan ~8× longer without the cursor the firmware has. Firmware side is the
good news: cursor tick (`firmware/castle_cues.h:259-268`), chunked load with
a yield, clamped level.

#### ~~G1~~ ✓ done 2026-09-17 — A publish downloads the whole scene library to decide it is unchanged, oplock held
- **Where:** `tools/sd_sync.py:161` (size match, then `_scene_bytes_match` →
  full `GET /sd/scenes/<name>`, `:99-105`); under `core/src/studio_publish.rs:
  45-50`, inside the oplock taken at `studio_scenes.rs:177`. `audio/card/` is
  8.2 MB.
- **What's wrong:** the size check exists to skip unchanged files, then the
  byte check pulls each unchanged file over Wi-Fi anyway; every encode,
  import and scene write queues behind it.
- **Fix:** a local `.published.json` of name→sha256 of what was last PUT,
  byte-compare only on mismatch; drop the oplock before the publish (G2).
- **Effort:** M
- **Grade lift:** B → B+

#### ~~G2~~ ✓ done 2026-09-17 — The oplock is held across the whole network publish (carried: grade report 2026-09-17 am G1)
- **Where:** `core/src/studio_scenes.rs:177` held through
  `publish_body(app)` at `:188` (two `sd_sync` runs, 900 s timeout each).
- **Fix:** drop the guard after the generator steps.
- **Effort:** M
- **Grade lift:** with G1: B → B+

#### G3 — The desk rescans every cue every frame; v5.67 made it 8× longer
- **Where:** `web/src/show.ts:202-206` linear scan per frame;
  `tools/gen_previewer.py:220-227` no longer thins (~1,600 cues for
  `the_citizens_of_halloween___this`, was 200); cues are sorted
  (`gen_previewer.py:230`).
- **Fix:** a cursor in `ShowState`, advanced while `cues[cursor].t <=
  elapsed`, reset where `st.fired` is rebuilt (`show.ts:165-169`).
- **Effort:** S
- **Grade lift:** B → B+ on the desk's frame budget

#### G4 — `job.log` unbounded (carried: grade report 2026-09-17 am G2)
- **Where:** see B5 (same site; listed there).
- **Effort:** S
- **Grade lift:** nit

#### G5 — 100 MB uploads fully buffered per thread (carried: grade report 2026-09-17 am G4)
- **Where:** `demo/castle-radio/server.py:438` on `ThreadingHTTPServer`
  (`:495`); `upload_suffix` needs `body[:128]`.
- **Fix:** sniff 128 bytes, stream the rest in 64 KB blocks.
- **Effort:** S
- **Grade lift:** nit

#### G6 — No write timeout on responses; `startup.log` never rotates (carried: grade report 2026-09-17 am G5, G3)
- **Where:** `core/src/http_parse.rs:170` read timeout only;
  `tools/castle_launcher.swift:60-65`; `server.py` has no `log_message`
  override so every `/radio/jobs` poll logs.
- **Fix:** `set_write_timeout(30s)`; truncate past 1 MB at launch; silence 2xx.
- **Effort:** S
- **Grade lift:** nit

---

## H — Documentation & Onboarding — B

Held. The S2 retirement is documented better than most releases: every
deleted file is named at its old site with date and section, no uncaveated
stale reference survives, every markdown link resolves, `docs/RUNBOOK.md:
19-30` already teaches the v5.67 workflow, `firmware/pending/README.md`
carries 5.67/5.68 as flashed with measured numbers. Not lifted because the
mDNS re-enable was documented in three of six places, leaving `devices.toml`
contradicting itself, and the standings block says the castle is off the
network on the day it was flashed five times.

#### ~~H1~~ ✓ done 2026-09-17 — mDNS is back on; four documents and one tool say it is off
- **Where:** truth `firmware/castle.yaml:264-271`, `pending/README.md:25`.
  Contradicting: `devices.toml:6-14` (vs its own `:21`; dead "No `fallbacks`"
  block at `:24-28` below the `fallbacks` it sets), `tools/castle_link.py:
  21-22`, `firmware/castle.yaml:198-200`, `pending/README.md:58-60`,
  `CLAUDE.md:241` ("mDNS first").
- **Fix:** one pass moving mDNS from "next" to "done, +2,080 B" and naming
  what IS next from the diet.
- **Effort:** S
- **Grade lift:** B → B+

#### H2 — The standings block claims the castle is off the network (carried, now false: grade report 2026-09-17 am H4)
- **Where:** `docs/notes/05-decisions-and-roadmap.md:77` (dated 2026-09-01),
  `:91` (cites the 09-06 report by the live filename), `:98-103` ("Waiting on
  hardware" — all done today).
- **Fix:** re-date, move `:98-103` into the log as done, cite the report by
  its dated filename, regenerate the counts from a command.
- **Effort:** S
- **Grade lift:** B → B+ with H1

#### H3 — Getting started omits binaries `make check` needs (carried: grade report 2026-09-17 am H2)
- **Where:** `README.md:91-95`; `tools/render_audio.py:251-265` shells to
  `lame` with no preflight; `check: audio`; `gen_previewer.py:313` says
  `npm install` where `CONTRIBUTING.md:7` and `Makefile:386` say `npm ci`.
- **Fix:** prerequisites line; `shutil.which("lame")` guard in the shape of
  `import_fetch.py:29`; `npm ci`.
- **Effort:** S
- **Grade lift:** B → B+

#### H4 — README's diagram predates v5.67; its studio paragraph predates the retirement
- **Where:** `README.md:20-23` (no card branch; `firmware/generated/` labelled
  "light cue scripts"), `:34-37` ("with the Python one behind it").
- **Fix:** add the `show.man` + `<id>.cue` branch; relabel; cut the clause.
- **Effort:** S
- **Grade lift:** nit, but it is the front door

#### H5 — §12.20–§12.22 are not in the index; the ceiling is still explained by S2 RAM
- **Where:** `PROJECT_NOTES.md:32,52-72` end at §12.15; `firmware/pending/
  README.md:61-62` ("derived from the S2's 172,032-byte dram0") contradicts
  its own `:24` and `scenes/scenes.yaml:104-115`.
- **Fix:** three finder rows; rewrite the ceiling bullet to name the record
  count and the four constants.
- **Effort:** S
- **Grade lift:** nit

#### H6 — `/radio/*` still undocumented (carried: grade report 2026-09-17 am H3)
- **Where:** `docs/API.md` zero hits; `server.py:272-280,470-476` (13 routes
  plus three prefixes); `docs/API.md`'s `/studio/*` table checked against
  `studio_routes.rs` — agrees both ways.
- **Fix:** a route table in `demo/castle-radio/README.md`, linked from
  `docs/API.md`.
- **Effort:** S
- **Grade lift:** nit

#### H7 — One-liners (carried: grade report 2026-09-17 am H5, none fixed)
- **Where:** `docs/SECURITY.md:7` names port 8820 (it is 8765);
  `docs/PARITY.md:162` cites `ci.yml:101` (export is at `:122`);
  `firmware/sd_audio.h:61` "flash scenes"; `make help` omits `test-radio`;
  `CLAUDE.md:243` and `check_citations.py:9` say six reports (there are eight).
- **Effort:** S
- **Grade lift:** nit

---

## I — Developer Experience & Tooling — B

Down from B+. The inner loop is good: a 0.25 s hook, optional-toolchain
skips, error messages written to be read (`Makefile:330,385-390`). Down
because both bespoke guards have holes their docs deny — the citation check
passes 204/204 while ~24 bare IDs go unread, and the 500-line cap passes
`docs/API.md` with a 3,324-character line — and `make setup` still cannot
produce a tree that runs `make check`.

#### I1 — The citation rule reads one spelling; two dozen bare IDs are invisible to it
- **Where:** `tools/check_citations.py:53` fires only on the words "grade
  report". Bare: `githooks/pre-commit:46`, `ci.yml:88,133,343`,
  `firmware/sd_web.h:55`, `sd_web_state.h:205,333`, `sd_web_upload.h:145,
  268`, `sd_web_util.h:197`, `sd_web_events.h:32`, `sd_web_site.h:164,203`,
  `castle_sd_common.yaml:151`, `core/src/studio_relay.rs:27`, `filters.rs:2`,
  `bin/synth_dump.rs:1`, `previewer/panels.css:394`, `styles.css:152`,
  `tests/cxx/web_check.cpp` ×5, `events_check.cpp:71`, `docs/notes/03-build.md:
  451`, and today's `tools/castle_emu_status.py:55`, `castle_emu_http.py:111,
  144`, `castle_emu_upload.py:65,78,95,113`, `castle_emu_wire.py:27`.
- **What's wrong:** `(A5)` appears in four firmware headers and nothing in
  the tree says which audit numbered it — the failure CLAUDE.md:246 says the
  checker refuses.
- **Fix:** second pattern for parenthesised `[A-J]\d{1,2}` with an exclusion
  list for GPIO aliases (`HARDWARE_FINDINGS.md:213-215`, `docs/WIRING*.md`);
  date the sites by `git blame`.
- **Effort:** M
- **Grade lift:** B → B+

#### I2 — The 500-line cap counts newlines, so the densest prose evades it
- **Where:** `tools/check_loc.py:41,180`; `docs/API.md` 75 lines with a
  3,324-char line; `docs/PARITY.md` 1,307; `docs/notes/05-…md` 1,201;
  `firmware/pending/README.md` 1,009. Same mechanism as C5.
- **Fix:** max-line-length (500 chars, warn 300) for non-exempt files; split
  the table cells.
- **Effort:** M
- **Grade lift:** B → B+ with I1

#### I3 — The cap is shaping the code, slightly worse (carried: grade report 2026-09-17 am I1)
- **Where:** largest 496 (`firmware/castle_sd_common.yaml`); eleven files at
  480+: `server.py` 495, `sd_sync.py` 490, `test_castle_radio.test.mjs` 490,
  `device-link.js` 489, `test_firmware_web_cxx.py` 488, `ci.yml` 487,
  `render_audio.py` 486, `test_firmware_s3.py` 485, `studio_routes.rs` 480,
  `docs/castle-wiring.html` 479 (half-generated by `gen_wiring_diagram.py:19`).
- **Fix:** split the closest four on chosen seams (`castle_sd_common.yaml`'s
  web-action dispatch at `:330-400`); decide `castle-wiring.html`.
- **Effort:** M
- **Grade lift:** B → B+

#### I4 — `make setup` does not yield a tree that can run `make check` (carried: grade report 2026-09-17 am I2)
- **Where:** `Makefile:91` installs from requirement files (CI uses the lock,
  `ci.yml:149,368`); never `npm ci`; `check` has no node_modules guard
  (`e2e` does, `:385-386`); `check: audio` needs `lame` (H3).
- **Fix:** `-c requirements.lock`; guarded `npm ci`; the guard `e2e` has.
- **Effort:** S
- **Grade lift:** B → B+ with I3

#### I5 — `make coverage` nests the Rust gates inside the unit suite (carried: grade report 2026-09-17 am I3)
- **Where:** `Makefile:277-280` no `CI_RUST_GATES_ELSEWHERE`;
  `tests/test_castle_core.py:63,148-150`.
- **Fix:** export it in the recipe; document that `make rust-lint` gates Rust.
- **Effort:** S
- **Grade lift:** nit

---

## J — Firmware — B+

Held. The AM's J1–J7 are genuinely fixed and better than asked (`url_encode`
mirrored and round-tripped in C; a real board table; PSRAM event ring with a
static fallback; deleted builds with tests that refuse their return). v5.67's
card show is careful: four header checks plus exact length on `show.man`
(`castle_scenes.h:135-152`), per-row NUL checks, clamps, chunked reads with a
yield, 25 real-bytes cases in `tests/test_scene_manifest_cxx.py`. Held at B+
by two gaps in the same feature: a scene that ends never gives its cues
back, and a published scene is not startable until a reboot.

#### ~~J1~~ ✓ done 2026-09-17 — A published scene is not startable until a reboot, and the PIR cannot name it without a flash
- **Where:** `firmware/castle_sd_common.yaml:155-170` (ids seeded once at
  boot); `sd_web.h:250-253,310-313` validate against that list;
  `generated/scenes.yaml:108-115` (`pir_scene` options compiled),
  `:56-95` (playlist delays compiled); `tools/sd_sync.py:203` prints "reboot
  to re-read it" and `make publish` never does.
- **What's wrong:** after `make publish` a new scene is 404 on `/api/scene`
  until a reboot; after the reboot `/api/pir?s=<new>` is accepted and handed
  to a `select` whose options are the compiled list (the silent-log case
  `sd_web.h:307-309` says was fixed); a changed `duration_ms` is cut or
  gapped by compiled playlist delays. CLAUDE.md says the publish is the deploy.
- **Fix:** re-seed after `PUT /api/scenes/show.man` (or `/api/reload`) and
  have `sd_sync scenes` queue a restart when the manifest changed; make
  `pir_scene` a text field validated against the card list; derive playlist
  holds from `castle_scenes::length_ms()`. Failing that, say so in CLAUDE.md.
- **Effort:** M
- **Grade lift:** B+ → A− (the headline feature's remaining gap)

#### ~~J2~~ ✓ done 2026-09-17 — A non-looping scene that ends never gives its cues back (v5.67 regression)
- **Where:** `firmware/castle_scenes.yaml:155-170` — after `wait_until
  finished` there is `if loops` and no else; `castle_scenes.h:230-239`
  (`stop()` clears `g_armed`), `:295-298` (`finished()` returns true but
  leaves `g_armed`); the 16 ms clock at `castle_cues.yaml:52-68` only ends
  cues when `!armed()`; the v5.63 auto-dark at `castle_sd_common.yaml:286-295`
  fires only for raw tracks (`current_scene == "stop"`).
- **What's wrong:** verified by reading the code: `g_armed` stays true, the
  PSRAM cue blob (1,258 records for citizens) stays allocated, `cues` stays
  non-zero in `/api/status`, `current_scene` still names the scene, the zones
  hold the last look. The desk and radio suppress their own light frames on
  the strength of `cues`. Pre-v5.67 a script's end held no PSRAM.
- **Fix:** in the non-looping branch execute `cues_end` (routes through
  `castle_scenes::stop()`) and publish `current_scene` "stop"; a
  `scenes_check` op asserting `active()==false` after `finished()`.
- **Effort:** S
- **Grade lift:** B+ → A− with J1

#### ~~J3~~ ✓ done 2026-09-17 — Scene starts are exempt from the OTA quiesce gate
- **Where:** `firmware/sd_web_ota.h:41` sets `castle_sd::g_quiesce`;
  `sd_audio.h:52-58` states the rule; new readers `castle_scenes.h:135-226`,
  `castle_cues.h:143-180` check nothing; `castle_inputs.yaml:39-60` (PIR)
  can fire them any time.
- **What's wrong:** `make ota` stops audio from outside; a PIR trip 20 s into
  the upload starts a scene that reads `show.man` and 32 KB `fread`s on the
  watched main loop while flash is burning, then gets its audio 503'd. Same
  class as the v5.61 fix, re-opened by v5.67's card I/O.
- **Fix:** return early from `scene_run`'s first lambda and `manifest_check`
  when `g_quiesce` is set, and `run_scene("halt")` at the top of `h_ota`;
  mirror in the emulator.
- **Effort:** S
- **Grade lift:** removes an OTA-time watchdog class

#### J4 — Manifest text fields trusted; Python accepts rows the C refuses
- **Where:** `castle_scenes.h:166,188,211` (NUL only), `:127-129` (no `..`/
  slash check, unlike `castle_cues::path_for:125-131`); `sd_web_util.h:
  173-180` `json_escape` passes ≥0x80 raw; `tools/scene_manifest.py:124-152`
  returns a 40-byte unterminated id the C treats as corrupt; `ids_csv` checks
  only `id`'s NUL while `find` checks `audio` too.
- **What's wrong:** a byte ≥0x80 in a card manifest makes `/api/status`
  invalid UTF-8 (the v5.46 bug, now via file contents); the emulator and the
  device disagree about an unterminated row. Shares the fix with E2.
- **Fix:** `row_ok()` in the C used by all three walkers; the same in
  `scene_manifest.decode`; a `write_card(manifest=…)` subtest per row defect.
- **Effort:** S
- **Grade lift:** closes the last card-bytes-to-wire gap

#### J5 — `duration_ms` has no floor, and `loop` makes zero a spin
- **Where:** `castle_scenes.h:266` clamps volume, `castle_cues.h:54` clamps
  level, `:295-298` trusts `g_len_ms`; `scene_manifest.py:110-119` no floor
  (`scene_schema.py:357-361` guards scenes.yaml only).
- **What's wrong:** `loops=1, duration_ms=0` makes `finished()` true at once,
  so `scene_run` re-executes every main-loop pass calling `sfx` each time.
- **Fix:** floor in `begin()` (< 100 ms drops the loop flag, logged once);
  raise in `encode` on `duration_ms <= 0`.
- **Effort:** S
- **Grade lift:** nit, but it is a hang

#### J6 — Comments stating numbers and mechanisms v5.67/5.68 replaced
- **Where:** `tools/check_image.py:6-7` (71.8% / 41.9% — board is 67.9% /
  35.1%); `firmware/castle.yaml:194`, `pending/README.md:57` (same stale
  41.9%); `sd_web_ota.h:105-107` (argues a hazard fixed by the latch at
  `sd_web_state.h:45`); `sd_web.h:249` ("seeded from pir_scene's options").
  `WARN_AT`/`FAIL_AT` (0.90/0.97) still read correctly; leave them.
- **Fix:** one pass over the five sites.
- **Effort:** S
- **Grade lift:** nit

Verified sound, no item: the four `SCENE_LIMIT` copies agree under
`tests/test_firmware_contract_scenes.py:58-67`; `missing` is bounded (16)
and heals identically on both castles; no live S2/eInk/64-symbol code
remains. One asymmetry too small to itemise: `manifest_check` names a
missing track as `NN_<id>.mp3` while `heal_missing` withdraws only `<id>`
and `<id>.cue`, so a republished AUDIO file stays listed until reboot.
