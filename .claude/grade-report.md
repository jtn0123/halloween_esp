# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware + castle-core
**Audited:** 2026-08-31
**Stack:** Python 3.13 tools · Rust (castle-core, 9,267 lines, zero-dep, 5 bins + WASM face) · TypeScript 7 / esbuild web app · ESPHome YAML + C++ on ESP32-S2 · 914 Python tests + 36 Rust tests + 35 node suites + 148 Playwright e2e + C++ parity

**Tree audited:** `1be4ef1` (main == origin/main, clean tree). 88 commits since the
2026-08-24 audit (archived at `.claude/grade-report-2026-08-24.md`) — nearly all
of them the Track-B Rust port: `scene_render` and `analyze_track` are now the
production DSP path, and a complete Rust twin of the studio server exists.

**How this was graded.** `make check` run locally (exit 0), `make e2e` run
(148/148 pass), `cargo test --release` run (36/36 pass), the failing GitHub CI
runs read job-by-job, and four parallel audit passes over
architecture/backend, frontend/docs, testing/DevEx, and security/deps/perf,
every claim anchored to file:line.

**Permanently accepted risk — do not re-raise.** The studio's missing
Origin/Host validation and the firmware's unauthenticated OTA/file endpoints
(accepted 2026-08-16) are excluded from Security and are not items.

---

## ⚠ Two things before any item below

1. **GitHub Actions is dead on billing.** Every run since 2026-08-31 fails in
   ~5s with zero steps: *"The job was not started because recent account
   payments have failed or your spending limit needs to be increased."* Only
   the account owner can fix this, in GitHub → Settings → Billing & plans.
   Until then nothing else about CI can be verified.
2. **Before the billing failure, main was already red in CI.** The last real
   runs (2026-08-28, commits `9fa1fba`/`1be4ef1`) failed with 15 failures + 1
   error in the `python` job — 12 of them Rust↔Python parity suites diverging
   on Linux (e.g. `test_synth_rust` "probe diverged: organ", last-digit float
   differences), plus `test_clippy_is_clean`, `test_wasm_face_builds` and the
   page-budget test. **The Rust port has never been green in CI** — it is
   verified only on this Mac. See D1.

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | B+ | 8 |
| B | Backend Quality | B+ | 6 |
| C | Frontend Quality | B+ | 6 |
| D | Testing & Reliability | B | 7 |
| E | Security | B+ *(excl. accepted risk)* | 2 |
| F | Dependencies & Tech Currency | B+ | 4 |
| G | Performance & Scalability | B+ | 3 |
| H | Documentation & Onboarding | B− | 5 |
| I | Developer Experience & Tooling | B− | 4 |
| **Overall** | | **B+** | **45** |

**Top 5 highest-leverage fixes:** D1, F1, G1, H2, I1

The drop from the 2026-08-24 A− is not code quality decay — the Rust work
itself is exceptional (bit-exact differential gates, zero dependencies, clean
subprocess seams, careful `unsafe`). It is **integration debt**: 9,267 lines
of a fifth language landed without a Makefile target, toolchain pin, CI job,
pre-commit coverage, or a single mention in README/CLAUDE.md — and CI has
never once validated it. The project's own (excellent) checklist was simply
not run on the new language.

---

## A — Architecture & Design — B+

The Python→Rust seam is exactly right: one home (`tools/core_bins.py`),
subprocess-only, hard-stop instead of silent fallback. But the migration is at
its maximum-duplication point — ~4,900 of the 9,267 Rust lines are a fully
maintained second implementation with no production entry point, and the
parity contract (`docs/PARITY.md`) lists Rust in only one of its rows. Twelve
files sit within 30 lines of the 500 cap (up from three), including the
show's own `scenes/scenes.yaml` at 487.

#### ~~A1~~ ✓ done 2026-08-31 — The restart-race fix landed only in the non-shipping Rust studio
- **Where:** `core/src/bin/studio.rs:104-120` (bind_retry) vs `tools/studio.py:74-78,193`
- **What's wrong:** The diagnosed exec-races-dying-connections port bug (plan, eighth run) was fixed with a bind retry in the Rust studio only. `POST /studio/server/restart` on the default Python studio still fails ~50% of the time and the process is gone when it does.
- **Fix:** Port the retry: when `CASTLE_STUDIO_RESTART` is set (set it in `_restart()` before `os.execv`), loop the `ThreadingHTTPServer` construction up to ~100× with 0.1s sleeps.
- **Effort:** S
- **Grade lift:** B+ → B+ (closes the one known drift where the shipping server is the worse one)

#### ~~A2~~ ✓ done 2026-08-31 — The Rust studio hardcodes `.venv/bin/python`
- **Where:** `core/src/studio_scenes.rs:19-27` vs the warning comment at `tools/studio.py:66-68`
- **What's wrong:** Reintroduces a bug the Python side documents as fixed: launched from a worktree or CI checkout, `py()` silently falls back to system `python3` (no numpy/scipy/yaml) and every rebuild/import fails confusingly.
- **Fix:** Honour `CASTLE_PY` first, then `.venv/bin/python`, then `python3`; fail loudly at startup if the resolved interpreter can't `import yaml`.
- **Effort:** S

#### ~~A3~~ ✓ done 2026-08-31 — netguard is the only port with no cross-language differential gate — and it's the SSRF guard
- **Where:** `core/src/netguard.rs:115-172` (snapshot tests only); `tests/test_netguard.py` never touches Rust
- **What's wrong:** Every other port is held by a Python-vs-Rust corpus; this one is held to a hand-copied snapshot. Tighten `tools/netguard.py` and the Rust studio silently keeps the weaker policy.
- **Fix:** Add `tests/test_netguard_rust.py` driving both implementations over the corpus in `tests/test_netguard.py:83-121`, asserting refusal strings equal.
- **Effort:** M

#### ~~A4~~ ✓ done 2026-08-31 — PARITY.md omits five of the six duplicated-logic pairs Rust created
- **Where:** `docs/PARITY.md:12-19`; live copies: effects (C++/TS/Rust), pulse (Py/TS/Rust), bridge (Py/Rust), netguard, manifest, sandbox paths, the whole studio HTTP surface (Py 1,921 lines / Rust 4,239)
- **What's wrong:** The document whose thesis is "a divergence is invisible, so list every copy" doesn't know about the studio surface (defended by six test files) or the other Rust twins.
- **Fix:** Add rows for the studio HTTP surface (citing `tests/studio_rust_case.py`), effects-in-Rust, pulse, bridge, netguard, manifest; update the "four implementations" title.
- **Effort:** S

#### ~~A5~~ ✓ done 2026-08-31 — `POST /studio/compare` reports the caller's typo as a 500, in both languages
- **Where:** `tools/studio_routes.py:226-236`; faithfully ported at `core/src/studio_probe.rs:144-188`
- **What's wrong:** Non-numeric `bitrate`/`take` raises `ValueError` → 500 + traceback, while every other route 400s bad input.
- **Fix:** Wrap the coercions in `BadRequest` in Python, return 400 in Rust, add one parity assertion.
- **Effort:** S

#### ~~A6~~ ✓ done 2026-08-31 — Stale Rust module headers describe several passes ago
- **Where:** `core/src/studio_routes.rs:1-5` (claims write paths 404 — they're implemented), `core/src/lib.rs:1-8` (claims maths-only in a crate that now holds a server and the production renderer)
- **Fix:** Rewrite both; add a `#![doc]` map naming the five faces and which are production vs parity-only.
- **Effort:** S

#### A7 — Flat 22-module crate, no feature gates, 25× WASM size slack
- **Where:** `core/src/lib.rs:10-42`; `core/Cargo.toml:12-14`; size assert `tests/test_castle_core.py:274` (`< 200_000` vs ~8 KB actual)
- **Fix:** Group `dsp::`/`net::`/`studio::`; put server/bridge/media behind a default-on `native` feature that the wasm build disables; tighten the size assert to ~20 KB.
- **Effort:** M

#### ~~A8~~ ✓ done 2026-08-31 — `scenes/scenes.yaml` is 487 lines against the hard 500 cap
- **Where:** `scenes/scenes.yaml` (487); `tools/check_loc.py` (no exemption)
- **What's wrong:** A code-hygiene rule is about to block *content* — roughly one more scene fails `make check` and the pre-commit hook, forcing an unplanned split under deadline (and October is the deadline).
- **Fix:** Decide now: split into `scenes/*.yaml` with a loader merge in `tools/scene_schema.py`, or add a documented exemption plus a separate scene-count budget.
- **Effort:** M

Also noted (S, no separate items): the `studio_scenes ↔ studio_publish`
import cycle held open by an unannotated function-local import
(`tools/studio_publish.py:25` ↔ `tools/studio_scenes.py:80`); and the
Rust-studio→Python-tools→Rust-bins process cycle (documented design, fine —
but it means `core/` can't be reasoned about independently of `tools/`).

---

## B — Backend Quality — B+

Route structure, error boundaries, the 512 MiB body cap and input validation
are solid and genuinely mirrored across both studio servers. Held at B+ by a
handful of real defects in the new Rust HTTP plumbing.

#### ~~B1~~ ✓ done 2026-08-31 — Zero-byte file + Range → `Content-Length: 1`, nothing written, connection desynced
- **Where:** `core/src/httpd.rs:313,317` vs the correct Python at `tools/studio_http.py:132-147`
- **What's wrong:** `total - 1` underflows in u64 (release build), clamps to `lo=0, hi=0, partial=true`, promising one byte that never arrives — the keep-alive connection is desynchronized for every later request. Debug builds panic outright. Reachable via any 0-byte file (interrupted stem split, truncated mp3).
- **Fix:** Special-case `total == 0` (200, `Content-Length: 0`); use `saturating_sub` in both spots; add the empty-file case to the httpd parity tests.
- **Effort:** S
- **Grade lift:** B+ → A− territory combined with B2 (removes the two ways the Rust server is strictly worse than the Python one)

#### ~~B2~~ ✓ done 2026-08-31 — Request bodies buffered ~3× in RAM; connection buffer never shrinks
- **Where:** `core/src/httpd.rs:167-173` against `MAX_BODY = 512 MiB`
- **What's wrong:** Doubling growth (~1 GB capacity), then `.to_vec()` copy, then O(n) `.drain()` — and `Conn::buf` keeps its capacity for the whole keep-alive lifetime, pinning ~1 GB after one big drag-and-drop import.
- **Fix:** `split_off` + `mem::replace` instead of `to_vec` + `drain`; `shrink_to_fit()` after bodies over ~8 MB; longer-term, stream large bodies to a temp file (import already stages to `tracks/_upload`).
- **Effort:** M

#### ~~B3~~ ✓ done 2026-08-31 — Decode LRU bounded by count, not bytes — up to ~2.5 GB resident
- **Where:** `core/src/studio_media.rs:18` and `tools/studio_media.py:180` (`KEEP_DECODED = 8`)
- **What's wrong:** One decoded 5-minute track ≈318 MB (three f64 buffers); eight is ~2.5 GB. Auditioning eight songs is ordinary use. Also two concurrent cold requests each spawn their own ffmpeg pair and build duplicate entries.
- **Fix:** Evict by total samples (~50M ≈ 400 MB) in BOTH languages in one commit (parity); add a per-key in-flight marker.
- **Effort:** M

#### ~~B4~~ ✓ done 2026-08-31 — Lean-page cache clones ~3 MB per request while holding the mutex
- **Where:** `core/src/studio.rs:108,114`
- **Fix:** Store `Arc<Vec<u8>>`, return `Arc::clone`, drop the guard before returning.
- **Effort:** S

#### ~~B5~~ ✓ done 2026-08-31 — Multipart boundary scan is O(n·m) on the request thread
- **Where:** `core/src/httpd.rs:429-432`
- **Fix:** Skip on the boundary's first byte before comparing windows — ~10× on a 100 MB import.
- **Effort:** S

#### ~~B6~~ ✓ done 2026-08-31 — `core_bin()` runs `cargo build --release` at every child-process start
- **Where:** `tools/core_bins.py:28-40`; render/import children are spawned per job by the studio, and CI runs `render_audio.py` in two jobs with no cargo cache
- **Fix:** mtime check (binary newer than every `core/src` file → skip); in CI build once in an explicit step and cache `core/target`.
- **Effort:** S

---

## C — Frontend Quality — B+

tsconfig is stricter than most production TS, state management is a tested
discriminated union, zero swallowed catches, no orphan modules, and a11y is
above par (aria-pressed, labels, role=alert, focus restore, reduced-motion).
Held at B+ by cap pressure that has gone backwards and two cheap a11y misses.

#### ◐ C1 — device.ts + device_panel.ts split 2026-08-31; waveform.ts needs a DI refactor — Prior C2 regressed: three files within 32 lines of the hard cap
- **Where:** `web/src/device.ts` (483, **grew 15** since last audit), `web/src/waveform.ts` (473), `web/src/device_panel.ts` (468)
- **What's wrong:** Any of the three blocks a commit on the next real feature — the only frontend item that can hard-stop work.
- **Fix:** Split `device.ts` at the transport/status seam; `device_panel.ts` at the render/bind seam (~line 338); `castle_act.ts` shows the extraction pattern.
- **Effort:** M
- **Grade lift:** B+ → A− (with C2/C3; removes the standing commit-blocker risk)

#### ~~C2~~ ✓ done 2026-08-31 — The generated page has no DOCTYPE and no `lang` — quirks mode + WCAG 3.1.1 failure
- **Where:** `previewer/template.html:1` (first line is `<meta charset>`)
- **Fix:** Prepend `<!DOCTYPE html><html lang="en">` and close it; re-run `tests/test_gen_previewer.py`.
- **Effort:** S

#### ~~C3~~ ✓ done 2026-08-31 — The scrub slider has no `aria-valuemax`
- **Where:** `previewer/template.html:60-61`; `web/src/panels.ts:286-287` updates now/text but never max (valuenow is a percentage)
- **Fix:** `aria-valuemax="100"` on the template element.
- **Effort:** S

#### ~~C4~~ ✓ done 2026-08-31 — `dom_discipline` guard misses `querySelector`; `dom.ts` has no scoped helper
- **Where:** `web/test/dom_discipline.ts:17` (only bans `getElementById`); `web/src/main.ts:125` walks through it; 59 of 60 raw lookups are subtree-scoped `this.body.querySelector(...)` that `dom.ts` can't serve
- **Fix:** Add `document.querySelector` to the guard; add `reqIn(root, sel)` to `dom.ts`; make the guard recursive.
- **Effort:** S

#### ~~C5~~ ✓ done 2026-08-31 — 92 non-null assertions, 12 of them re-creating the exact bug `req()` prevents
- **Where:** `web/src/device_panel.ts:239-451` (12× `querySelector(...)!`), `onsets.ts` (18), `track_sections.ts` (14), `stems_view.ts` (11)
- **Fix:** C4's `reqIn()` converts the device_panel sites mechanically; sweep the rest opportunistically.
- **Effort:** M

#### ~~C6~~ ✓ done 2026-08-31 — Seven dead exports invisible to `noUnusedLocals`
- **Where:** `rig_panel.ts:251` `fixtureSummary`, `bands.ts:60` `isBand`, `types.ts:75-76` `isLed`/`isAudio`, `castle_bus.ts:31` `castleLive`, `effects.ts:44,253` `PaletteName`/`OverlayName`
- **Fix:** Delete, or actually use the three type guards at the `c.bus === "LED"` sites they duplicate.
- **Effort:** S

Also noted (M, low priority): module-level mutable job state in
`stems_view.ts:54` and `castle_bus.ts:28` sits outside the tested state
machine — the site of a past real bug (JB1-10); inject via `StemsDeps` to
make it assertable.

---

## D — Testing & Reliability — B

The substance is A-grade — 914 Python tests, 148 e2e, 36 Rust tests, bit-exact
five-language parity, seeded fuzz, chaos, zero disabled tests, and a skip
idiom that converts to hard failure in CI. But the category grades the
*guarantee*, and the guarantee is broken: **main has been red in CI since the
Rust port landed**, and locally the Rust gates silently vanish on machines
missing a C++ compiler. All items [both] unless tagged.

#### ~~D1~~ ✓ done 2026-08-31 — CI has never validated the Rust port: parity diverges on Linux, wasm target missing, clippy red
- **Where:** CI run 33135664945 (2026-08-28, `python` job): 12 `test_*_rust` parity failures ("probe diverged: organ" — last-ulp float differences), `test_clippy_is_clean` FAIL, `test_wasm_face_builds_loads_and_computes` FAIL, `test_built_page_stays_under_its_byte_budget` ERROR. `.github/workflows/ci.yml` (untouched since 2026-08-24, before the crate landed) installs no Rust toolchain, no wasm32 target, no cargo cache.
- **What's wrong:** The plan's "same bytes on every machine" claim is currently a hope, not a test — the float-profile probing verified on this Mac does not hold on CI's x86_64 wheels, and the toolchain CI uses is whatever the runner image ships. Everything merges on local green only.
- **Fix:** (1) unblock billing (user); (2) add a toolchain step (`dtolnay/rust-toolchain` pinned, `targets: wasm32-unknown-unknown`, components clippy+rustfmt) + `Swatinem/rust-cache`; (3) reproduce the Linux probe divergence (a Linux container or the CI runner itself) and extend the mode-probing to cover the x86_64 wheel profile — this is the real engineering; (4) fix or budget the page-size test failure.
- **Effort:** L (the divergence); S (the workflow lines)
- **Grade lift:** B → A− (restores the entire point of the parity architecture)

#### ~~D2~~ ✓ done 2026-08-31 — Rust test/fmt/clippy gates are transitively gated on a C++ compiler
- **Where:** `tests/test_castle_core.py:91-95` — `skipIf((CARGO is None or COMPILER is None) ...)` guards the class holding the three pure-Rust gates
- **Fix:** Split them into `TestCastleCoreToolchain` gated on cargo alone.
- **Effort:** S

#### ~~D3~~ ✓ done 2026-08-31 — `make test-fast` now triggers two full Rust release builds + clippy + twelve server spawns
- **Where:** `Makefile:133-136` — `SLOW_SUITES := chaos|relay|fuzz` predates the crate
- **Fix:** `SLOW_SUITES := chaos|relay|fuzz|_rust|castle_core`; update `make help` and the comment; pair with I1's `make rust` so the excluded work stays one word away.
- **Effort:** S

#### ~~D4~~ ✓ done 2026-08-31 — The Rust studio half (≥2,700 lines) has zero unit tests
- **Where:** `core/src/httpd.rs` (467, 0 tests), `studio_scenes.rs` (431), `studio_routes.rs` (404), `studio_media.rs`, `studio_import.rs`, `studio_probe.rs`, `studio_relay.rs`, `studio_tracks.rs`, `effects.rs` (rests entirely on the C++ dump comparison, which D2 can skip)
- **What's wrong:** Fastest feedback on an `httpd.rs` change is a multi-minute two-server Python run; a parsing panic surfaces as a mystery timeout (or, per E1, a dead process).
- **Fix:** `#[cfg(test)]` tests at the parsing seams first — request-line/header/range parsing, jsonio round-trips on malformed input (pure functions, no server needed). B1 gets its regression test here.
- **Effort:** M

#### ~~D5~~ ✓ done 2026-08-31 — The 148-test e2e gate against the Rust studio ran once, by hand
- **Where:** `web/playwright.config.ts:78-79`; `ci.yml:115-119` never sets `CASTLE_STUDIO_CMD`
- **Fix:** Matrix axis or fourth job: build `--bin studio`, re-run Playwright with `CASTLE_STUDIO_CMD=../core/target/release/studio`. ~15 lines of YAML, zero new test code.
- **Effort:** S

#### ~~D6~~ ✓ done 2026-08-31 — `free_port()` TOCTOU across two servers
- **Where:** `tests/studio_rust_case.py:101-104,279`
- **Fix:** Cheapest: retry `setUpClass` once on `wait_up` failure. Better: port-0 + print bound port.
- **Effort:** S

#### ~~D7~~ ✓ done 2026-08-31 — The enforced coverage number describes a shrinking fraction of shipped code
- **Where:** `Makefile:143,151` — 82% ratchet scopes `--source=tools` while logic migrates into `core/`
- **Fix:** Report `cargo llvm-cov --summary-only` non-gating (the `make audit` pattern); note the scope in the `COVERAGE_MIN` comment.
- **Effort:** M

---

## E — Security — B+ *(excluding accepted risk)*

Actively probed and clean: path traversal (raw un-decoded request path, id
regex, whitelisted layers), command injection (argv-only, deliberate `--key=value`
flag-injection defense), deserialization (safe_load everywhere), secrets
(example file + gitignore + throwaway CI wifi), and all four real `unsafe`
blocks are minimal FFI shims. The new Rust code is *more* careful than average.

#### ~~E1~~ ✓ done 2026-08-31 — `panic = "abort"` makes the studio's documented error boundary dead code
- **Where:** `core/Cargo.toml:22` vs the `catch_unwind` at `core/src/bin/studio.rs:74` ("never a dead socket")
- **What's wrong:** The bin only ever builds in release (`tools/core_bins.py:28`), where `panic = "abort"` means any handler panic aborts the whole thread-per-connection process — every in-flight connection dies with a bare SIGABRT. The Python twin degrades gracefully; the Rust one dies. (wasm32 aborts on panic regardless, so the setting buys nothing there.)
- **Fix:** Delete the line, or give the server bin its own `panic = "unwind"` profile.
- **Effort:** S
- **Grade lift:** B+ → A− (the one substantive availability defect, one line)

#### ~~E2~~ ✓ done 2026-08-31 — `.claude/worktrees/` (675 MB, other projects' full checkouts) ignored only per-clone
- **Where:** `.git/info/exclude:7`; `.gitignore` has no `.claude` entry
- **What's wrong:** `info/exclude` never leaves this clone. On any other machine a `git add -A` sweeps two unrelated repos (workflows included) into this repo's history.
- **Fix:** Add `.claude/worktrees/` (and probably `.claude/grade-report*.md`) to the committed `.gitignore`.
- **Effort:** S

Recorded, no action (accepted threat model): netguard's resolve-then-refetch
TOCTOU, and the fact that it is bypassed entirely for loopback callers — in
the default 127.0.0.1 binding netguard is effectively off. Noted so it isn't
mistaken for a live defence.

---

## F — Dependencies & Tech Currency — B+

113-package pip lock, committed npm lock, committed (empty — zero-dep, the
best supply-chain answer) Cargo lock, current TS/esbuild, every advisory
suppression documented by ID with a review date. Slips from A− because five
Dependabot PRs are open, two of them mutually blocking behind a constraint
the repo created, and the newest language has no toolchain pin.

#### ~~F1~~ ✓ done 2026-08-31 — The esphome pin lives in three places; the two blocked PRs must land as one
- **Where:** `requirements.txt:11`, `requirements.lock:30`, and a literal in `ci.yml`'s esphome job; `requirements.txt:12` `aioesphomeapi~=45.7` forbids the open 46.2 PR, and esphome 2026.8.1 almost certainly requires ≥46 — merging either PR alone breaks resolution or the "CI validates the pin" invariant
- **What's wrong:** Also blocking six accepted advisories (`.pip-audit-ignore`: 5 starlette + cryptography, "clears on bump", review 2026-10-01).
- **Fix:** One commit: esphome 2026.8.1 + `aioesphomeapi~=46.2`, single-source the pin (ci.yml installs from requirements), `make lock`, delete the six stale ignore lines, close PRs #12/#13. Add a Dependabot `groups:` entry pairing the two packages.
- **Effort:** M
- **Grade lift:** B+ → A− (clears the whole advisory backlog and the PR queue)

#### F2 — Merge the three GitHub Actions major bumps (PRs #9, #10, #11)
- **Where:** `ci.yml` — checkout v4→v7 ×4, setup-python v5→v7 ×4, setup-node v4→v7 ×2; the Aug-28 logs already show the Node 20 deprecation warning that becomes a hard failure
- **Fix:** Merge all three (needs F-billing fixed first so checks can run); verify setup-node v7's cache inputs still resolve.
- **Effort:** S

#### ~~F3~~ ✓ done 2026-08-31 — No `rust-toolchain.toml`; clippy `-D warnings` floats on the runner's rustc
- **Where:** absent from `core/`; `core/Cargo.toml` has no `rust-version`; edition still 2021
- **What's wrong:** The one unpinned toolchain in an otherwise conviction-pinned repo, feeding the version-sensitive gate. Also undercuts RUNBOOK's byte-determinism sentence — the canonical CRC currently rests on an undeclared compiler.
- **Fix:** `core/rust-toolchain.toml` (pinned channel, clippy+rustfmt components, wasm32 target — also fixes half of D1 mechanically); `rust-version` in Cargo.toml; edition 2024 in the same pass.
- **Effort:** S

#### ~~F4~~ ✓ done 2026-08-31 — No `cargo` ecosystem in dependabot.yml
- **Where:** `.github/dependabot.yml`
- **Fix:** Add the `/core` cargo block now while it's a no-op, so the first-ever crate dependency arrives scanned.
- **Effort:** S

---

## G — Performance & Scalability — B+

Delegation checked specifically: no double DSP work anywhere —
`render_scene_py` and `analyze.py` survive only as parity references with
zero runtime callers. Held at B+ because the port's payoff is unshipped and
last audit's page-budget ratchet has already failed once.

#### ◐ G1 — CI matrix runs e2e against the Rust studio 2026-08-31; the default flip stays off-season — The Rust studio is finished, parity-gated, and unreachable by default
- **Where:** `Makefile:72`, `.claude/launch.json:6-9`, `playwright.config.ts:78` — all Python; `grep -rn "release/studio"` hits only comments
- **What's wrong:** All 148 e2e pass against it, and nothing runs it — not `make studio`, not launch.json, not CI. ~4,239 lines maintained in parallel with no automated gate and no realized benefit.
- **Fix:** D5's CI matrix first; after it holds green for a bit, flip `make studio` and launch.json to the Rust binary with a Python fallback when the binary is absent. (The plan says the flip is off-season — the CI gate should not wait for it.)
- **Effort:** M
- **Grade lift:** B+ → A− (with D5; ships the port's actual payoff)

#### ~~G2~~ ✓ done 2026-08-31 — The page budget was raised once and will be breached again in ~2 weeks
- **Where:** `tools/gen_previewer.py:357-359` — `PAGE_BUDGET_KB = 4 * 1024`, comment still says "3 MB held"; page is 3.24 MB today (1.1 → 2.17 → 3.24 MB across three audits, ~1 MB/week as scenes are added)
- **What's wrong:** Warn-only budget + line-moves-when-crossed = the exact ratchet failure the prior G2 predicted. Growth is O(scenes × audio length) because scene audio is inlined.
- **Fix:** Stop inlining past N scenes — emit the `/studio/scene-audio/<id>` links the lean page already uses — making the portable build O(1) in scene count; make the ceiling fail, not warn; fix the stale comment.
- **Effort:** M

#### ~~G3~~ ✓ done 2026-08-31 — See B6 (cargo no-op build on every render/import child; uncached in CI)
- **Effort:** S

---

## H — Documentation & Onboarding — B−

The docs *culture* is still this repo's best asset — RUNBOOK is current,
comments are exceptional. But the two orientation documents don't know the
largest subsystem exists, one actively denies it, and the five-line Getting
Started cannot complete on a fresh machine. Scoreboard: RUNBOOK ✅ ·
PARITY ◐ (missing studio layer, three broken commands) · API.md ◐ ·
README ❌ · CONTRIBUTING ❌ · CLAUDE.md ❌.

#### ~~H1~~ ✓ done 2026-08-31 — README: setup path broken, diagram pre-B3, "four implementations" now five
- **Where:** `README.md:83` (`make setup` doesn't install Rust; the next line, `make audio`, hard-stops without cargo — by design, `RUNBOOK.md:31-33`); `README.md:21` (diagram says render_audio.py renders); `README.md:131` (four implementations); `README.md:113-121` (studio.py described as *the* studio)
- **Fix:** Name the Rust toolchain as a prerequisite; add `core/` to the diagram; five implementations; cross-link PARITY.md.
- **Effort:** M
- **Grade lift:** B− → B+ (with H2/H3; a fresh clone can reach green by reading)

#### ~~H2~~ ✓ done 2026-08-31 — CLAUDE.md line 6 tells every agent Rust does not apply to this repo — and the previewer paragraph argues the opposite of current behavior
- **Where:** `CLAUDE.md:5-7` ("…(ESP32-S3, Rust, espflash) — none of that applies here"); layout section lists no `core/`; `CLAUDE.md:15,27-31` still says the previewer blob is "generated AND tracked" — it was gitignored (`.gitignore:40`) when prior-A2 was executed, and `ci.yml:105-109` says "generated, not tracked"
- **What's wrong:** The repo's canonical orientation file misleads every agent session twice: treat `core/` as out of scope, and expect a tracked blob that isn't.
- **Fix:** Reword line 6 ("the espflash/ESP32-S3 parts don't apply; this repo *does* have its own Rust crate at `core/`"); add a `core/` bullet (scene_render, analyze_track, the studio twin, the parity gates); replace the previewer paragraph with the current rule; drop the dead `EXEMPT_PATHS` entry in `tools/check_loc.py:35`.
- **Effort:** S

#### ~~H3~~ ✓ done 2026-08-31 — CONTRIBUTING's setup cannot produce a green `make check`
- **Where:** `CONTRIBUTING.md:3-13` — no Rust; its `make check` description omits the Rust gates; `make setup` (`Makefile:50-55`) says nothing either
- **Fix:** One clause (install rustup before `make setup`); a `command -v cargo || echo note` line in the setup target; update the check description.
- **Effort:** S

#### ~~H4~~ ✓ done 2026-08-31 — API.md's party list and PARITY.md's runbook commands are stale
- **Where:** `docs/API.md:3-6` ("Three parties", only the Python studio — the Rust twin serves the same 20+19 routes, verified matching); `docs/PARITY.md:38-41` — `node test/rig_parity.mjs` **does not exist** (it's `dist/rig_parity.mjs` after esbuild), and the `node test/*.ts` hunting commands only work post-bundle — these are the commands you reach for when parity is red
- **Fix:** Fourth party in API.md naming which server is production; rewrite the four commands to the `dist/` form or add an npm script honoring the seed env vars; `.mjs`→`.ts` on PARITY line 17.
- **Effort:** S

#### ~~H5~~ ✓ done 2026-08-31 — web/MIGRATION.md's "is now the actual layout" is 47 modules out of date
- **Where:** `web/MIGRATION.md:1-4` (8 rows vs 56 modules), linked as current from `PROJECT_NOTES.md:73`
- **Fix:** One sentence to past tense.
- **Effort:** S

---

## I — Developer Experience & Tooling — B−

The prior report's DevEx items were all closed and the tooling around
Python/TS remains genuinely thoughtful. The regression is structural: an
entire language landed with no Makefile target, no hook coverage, no CI job,
no toolchain pin, no onboarding line — none of the repo's own checklist.

#### ~~I1~~ ✓ done 2026-08-31 — Zero Rust targets in the Makefile; `cargo` appears in no entry point
- **Where:** `Makefile` (0 occurrences), `githooks/pre-commit` (0), `make help`
- **What's wrong:** The only route to building/linting 9,267 lines is knowing a test file shells out to cargo. Every other language has a first-class spelling; this is also the mechanism behind D3.
- **Fix:** `make rust` / `rust-test` / `rust-lint` (fmt --check + clippy -D warnings) with `--manifest-path core/Cargo.toml`; `lint` depends on `rust-lint`; `test_castle_core.py` invokes the Make targets so "the Rust gate" has one definition.
- **Effort:** S
- **Grade lift:** B− → B+ (with I2/I3; the language becomes addressable)

#### ~~I2~~ ✓ done 2026-08-31 — Pre-commit hook: no `cargo fmt --check`
- **Where:** `githooks/pre-commit:10-20` (the `web/node_modules` guard at :18 shows the optional-toolchain pattern)
- **Fix:** `if command -v cargo; then cargo fmt --check --manifest-path core/Cargo.toml; fi` — sub-second, no build.
- **Effort:** S

#### ~~I3~~ ✓ done 2026-08-31 — No dedicated Rust CI job; Rust failures report as "coverage-gated unit tests failed"
- **Where:** `.github/workflows/ci.yml:29-74` — the 25-min python job budget was sized before two uncached release builds + clippy + twelve server spawns joined it
- **Fix:** Parallel `rust` job (pinned toolchain, rust-cache, fmt/clippy/test/wasm); python job keeps only the parity tests; the D2-split toolchain tests then skip in CI.
- **Effort:** M

#### ~~I4~~ ✓ done 2026-08-31 — `core/src/httpd.rs` (467) and `jsonio.rs` (453) are in the cap warning band, and the least-tested file is the closest one
- **Where:** `tools/check_loc.py` warning at 450+; D4's tests will push httpd.rs over
- **Fix:** Split httpd.rs on its natural seam (request parse / response write / serve loop) now, while it's a choice.
- **Effort:** M

---

## Repo hygiene snapshot (2026-08-31, not graded — for the cleanup pass)

- **main is clean and pushed**; `make check`, `make e2e` (148), `cargo test` (36) all green locally.
- **Uncommitted real work:** `firmware/garage.yaml` sits **untracked** in the `esp32-garage-door-remote` worktree — a complete, documented device config (Genie opener, optocoupler, reed switch). Its branch is otherwise fully merged. Commit it or consciously discard it.
- **Stash:** `stash@{0}` = 9-line `launch.json` stems-demo entry from another session. Apply or drop.
- **Stale local branches** (verified superseded by evolved equivalents on main): `backup-before-split`, `claude/ci-apt-fast-path`, `claude/ci-apt-stalls-and-concurrency`, `claude/local-app-startup-fa3df4`. Fully merged: `claude/castle-cue-desk-v3-2e5186`, `claude/castle-remote-polish`, `claude/esp32-garage-door-remote-e4b20a` (after garage.yaml is rescued). All deletable; the two clean detached worktrees (`castle-cue-desk-v3`, `local-app-startup`) removable via `git worktree remove`.
- **Open PRs:** #9–#13, all Dependabot, all red on the billing failure. #9/#10/#11 (Actions v7) merge clean once CI runs; #12/#13 (esphome/aioesphomeapi) must land together — see F1.
