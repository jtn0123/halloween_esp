# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware + castle-core
**Audited:** 2026-09-01 (regrade; the 2026-08-31 audit and its execution log are
archived at `.claude/grade-report-2026-08-31-executed.md`)
**Tree:** `6c0e275`, main == origin/main, **CI green** (three consecutive runs:
rust · python incl. AVX-512 parity · web ×2 incl. the Rust studio e2e axis ·
esphome). 953 Python tests, 50 Rust tests, 148 e2e, all green.

**How this was graded.** Three fresh-eyes audit passes over the tree at
`6c0e275`, each instructed to verify yesterday's 43 executed items still hold
(spot-checks passed — several closures were *better* than specified: the scene
ceiling by delegation, B3's concurrency half, line-for-line bind_retry twins)
and to hunt regressions from the ~25 commits of churn. Grades below reflect
today's tree, not yesterday's promises.

**Permanently accepted risk — do not re-raise.** Studio Origin/Host
validation; firmware OTA/file endpoint auth.

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | B+ | 4 |
| B | Backend Quality | A− | 2 |
| C | Frontend Quality | A− | 4 |
| D | Testing & Reliability | B+ | 5 |
| E | Security | B+ *(excl. accepted risk)* | 3 |
| F | Dependencies & Tech Currency | A− | 4 |
| G | Performance & Scalability | B+ | 2 |
| H | Documentation & Onboarding | B+ | 5 |
| I | Developer Experience & Tooling | A− | 3 |
| **Overall** | | **A−** | **32** |

**Top 5 highest-leverage fixes:** D1, E1, F1, I1, G1

Back to the project's best-ever grade, and this time it is earned in CI, not
only on one Mac: the canonical render is proven byte-identical on macOS arm64,
Linux aarch64 and Linux x86_64 (AVX-512 included), the Rust crate has its own
CI job, toolchain pin, Make targets, hook coverage and docs, and the whole
2026-08-31 integration-debt story is closed. What remains is smaller and
sharper: one gate-integrity hole (D1), one real fault-path bug (E1), one CI
blind spot Dependabot already walked through (F1), and the deliberately
deferred structural work (A1, G1).

---

## A — Architecture & Design — B+

The day's splits were done on real seams behind re-export facades, and the
scene ceiling landed as delegation, not duplication. Held at B+ by the same
ceiling as yesterday, now measurably heavier: the crate is 35 flat `pub mod`s
with zero `[features]`, and ~4,200 Rust studio lines still duplicate ~2,000
Python lines with no default entry point.

#### A1 — Feature-gate the crate; the wasm stubs are the evidence (was A7)
- **Where:** `core/src/lib.rs:41-75` (35 flat mods); `core/Cargo.toml` (no `[features]`); `core/src/manifest.rs:14-27` and `core/src/studio_jobs.rs:14-43` already carry `#[cfg(target_arch = "wasm32")]` stubs solely because the HTTP server compiles into the cdylib; size assert `tests/test_castle_core.py:329` still `< 200_000` vs ~8 KB actual (can never fail).
- **Fix:** default-on `native` feature gating server/bridge/media; wasm builds `--no-default-features`; delete the stubs; tighten the size assert to ~20 KB. Pair with the edition-2024 migration (F4).
- **Effort:** M

#### A2 — 58 comments cite bare grade-report item IDs that renumber every audit
- **Where:** e.g. `tools/studio_scenes.py:75` "grade report A1" vs `web/src/band_style.ts` "A1" — same ID, different reports, both in-tree. Five report files now exist and IDs collide across them.
- **Fix:** date every citation ("grade report 2026-08-31 A1"); a grep in `make check` refusing undated ones keeps the discipline.
- **Effort:** M (mechanical, ~58 sites)

#### A3 — `core/Cargo.toml` declares four of the eight bins
- **Where:** four `[[bin]]` blocks; `scene_render`, `analyze_track`, `pulse_dump`, `netguard_dump` exist only by autodiscovery — the manifest is not the list of bins.
- **Fix:** declare all eight, or none.
- **Effort:** S

#### A4 — The `studio_publish ↔ studio_scenes` cycle is held open by an unannotated deferred import
- **Where:** `tools/studio_scenes.py:81` — the comment explains the feature, not that the function-local import breaks a cycle.
- **Fix:** one sentence, or extract the shared leaf.
- **Effort:** S

---

## B — Backend Quality — A−

Every one of yesterday's fixes verified, several beyond spec (B3 shipped the
in-flight marker with matching Condvar/Condition semantics; B1's fix documents
why the Python was accidentally safe). Caches and job registries all bounded;
no TODO/unimplemented anywhere in `core/src` or `tools/`. Two edge items:

#### B1 — Short read mid-serve under-delivers a promised Content-Length on keep-alive
- **Where:** `core/src/http_resp.rs:185-187` and `tools/studio_http.py:169-170` — a file that shrinks while serving (interrupted stem split, re-import) desynchronizes the connection. Parity-consistent, so no gate catches it.
- **Fix:** on a short read, close instead of keeping alive — both languages, one commit.
- **Effort:** S

#### B2 — `read_request`'s comment credits removing a memmove that's still there
- **Where:** `core/src/http_parse.rs:181-191` — the peak-RAM claim is now true, but line 191's `drain(..head_end+4)` still shifts a 512 MB body by ~200 bytes.
- **Fix:** correct the comment, or `split_off` the head so the body never shifts.
- **Effort:** S

---

## C — Frontend Quality — A−

The C1 splits landed cleanly (device.ts 483→426, device_panel.ts 468→267; the
new modules have leaf-only imports, no cycles), the dom_discipline guard is
coherent with zero offenders, and the stems job state is injected and tested.
Only `waveform.ts` (473) remains near the cap in `web/src`.

#### C1 — `waveform.ts` (473) needs the DI refactor its closure shape demands (carryover)
- **Where:** one 393-line closure over shared mutable state; pure extraction can't clear the 440 bar.
- **Effort:** M

#### C2 — 3,562 lines of e2e specs are outside `tsc --noEmit`
- **Where:** `web/tsconfig.json:21` — `"test/*.ts"` doesn't reach `test/e2e/`; `playwright.config.ts` also unchecked.
- **Fix:** `"test/**/*.ts"` + the config in `include`; fix the fallout (expect `exactOptionalPropertyTypes` noise).
- **Effort:** M

#### C3 — Three DOM non-null assertions survived the sweep, one systematized
- **Where:** `web/src/device_chip.ts:89` (a `q()` helper wrapping the `!`), `zone_designer.ts:102`, `tracks.ts:243` (`closest(...)!` — `dom.ts` has no wrapper).
- **Fix:** `reqIn` for the first two; add `closestIn(el, sel, who)` for the third.
- **Effort:** S

#### C4 — `device.ts` pokes `device_chip.ts`'s subtree eight times by raw querySelector
- **Where:** `device.ts:195-359` — the probe/status half moved out in the split; the chip-poking half stayed.
- **Fix:** have `wireChip` hand back resolved elements, or move the readers into `device_chip.ts`.
- **Effort:** M

---

## D — Testing & Reliability — B+

The day's test work was additive — no loosened tolerance anywhere in the diff,
and `assert_libm_transcendentals` *tightened* parity into named platform
failures with computed remedies. Held at B+ by one gate-integrity hole and the
still-bare Rust studio layer.

#### D1 — Ten parity-gate call sites run cargo from the repo root, silently bypassing the toolchain pin
- **Where:** `tests/test_castle_core.py:101`, `test_netguard_rust.py:130`, `test_onsets_rust.py:45,119,197`, `test_pulse_rust.py:54`, `test_master_rust.py:48,124,173,302` — all `--manifest-path` from the root. `Makefile:202-205` documents the trap ("rustup finds rust-toolchain.toml by WORKING DIRECTORY"); `tools/core_bins.py` obeys it; these ten don't. The bit-exactness gates float on whatever rustc is the local default.
- **Fix:** shared helper in `tests/` running with `cwd=core`; one assertion that `cargo --version` under the gate reports 1.88.0.
- **Effort:** S
- **Grade lift:** B+ → A− (the pin becomes real everywhere, not just in CI)

#### D2 — 2,619 lines of the Rust studio layer still have zero `#[test]`
- **Where:** `studio_scenes.rs` (475) through `studio_tracks.rs` (212) — yesterday's D4 closed the parsing seam only.
- **Fix:** `#[cfg(test)]` on the pure functions first: id/index validation, the twelve-scene refusal, relay target rewriting.
- **Effort:** M

#### D3 — The two page ceilings can disagree by 285 KB and redden CI on a correct build
- **Where:** `tools/previewer_budget.py:39` (4 MB — what the build un-inlines down to) vs `tests/test_previewer_budget.py:242` (3.6 MB hard test). A page landing between them is a successful build that fails `web (python)`.
- **Fix:** derive the test bound from `pgb.PAGE_BUDGET_KB` — the invariant worth pinning is "the build honoured its own ceiling".
- **Effort:** S

#### D4 — `tools/lock_deps.py` (new, 163 lines) has no tests
- **Where:** nothing in `tests/` imports it; `compose()`/`package()`/`read_lock()` are pure; a `compose()` bug silently writes the file CI installs from.
- **Fix:** one module round-tripping a fixture lock (markers reapplied, carry-over kept, order stable). Pairs with F1.
- **Effort:** S

#### D5 — `web/package.json` maintains the suite list twice
- **Where:** `web/package.json:12` — twelve names bundled, the same twelve run; a file added to one half compiles and never runs.
- **Fix:** glob the bundle and `for f in dist/*.mjs`, or assert the lists match.
- **Effort:** S

---

## E — Security — B+ *(excluding accepted risk)*

Today's new code is careful (filename refusals, pre-allocation length checks,
log scrubbing, argv-only subprocess, a socketless parity dump). One real
fault-path bug and two hardening notes:

#### E1 — A panic inside the Rust decode leaves a permanent in-flight marker; later requests for that track deadlock
- **Where:** `core/src/studio_media.rs` `decoded()` — busy key pushed, lock dropped, `build_decoded` unguarded; cleanup + `notify_all` only on the success path. Yesterday's `panic = "abort"` removal means a panic now unwinds to a clean 500 — and skips the cleanup, so every later request for that key blocks forever and pins a thread. The Python twin already does try/finally.
- **Fix:** a Drop guard holding the key (or catch_unwind around the build) so the marker clears on both paths; one test that a poisoned decode doesn't wedge the next caller.
- **Effort:** S
- **Grade lift:** B+ → A− (the last place the twins differ under fault)

#### E2 — Relayed header values reach `head()` unvalidated; the CRLF safety is accidental
- **Where:** `core/src/http_resp.rs:61-71` — `v.trim()`+`lines()` happen to stop a full CRLF; a bare CR survives. Only castle-relayed `Content-Type` flows here today.
- **Fix:** strip/reject `\r`/`\n` in the value loop, with a test.
- **Effort:** S

#### E3 — No read timeout on studio connections
- **Where:** `core/src/http_parse.rs:206-216`; a silent client pins a thread forever. Parity with Python's ThreadingHTTPServer, loopback by default — but `--lan` is a documented mode.
- **Fix:** `set_read_timeout(Some(30s))` in `Conn::new` (and the Python equivalent if parity demands).
- **Effort:** S

---

## F — Dependencies & Tech Currency — A−

113 exact pins, zero floating lines, the esphome literal gone from CI in
favour of grepping requirements.txt, the advisory ledger down to five
documented starlette entries, cargo ecosystem registered while still a no-op,
all Actions on v7. One structural gap and one lingering PR:

#### F1 — Nothing in CI resolves `requirements.txt`; Dependabot already walked through the gap
- **Where:** every job installs `requirements.lock`; PR #13's own run log shows it installing `aioesphomeapi==45.10.3` from the lock — the version the PR claims to replace. An unresolvable constraint could merge green.
- **Fix:** a resolver-only `pip install --dry-run -r requirements.txt -r requirements-dev.txt` step, or a unit test asserting every txt specifier is satisfied by its lock pin (pairs with D4).
- **Effort:** S

#### F2 — Close PR #13 and ignore aioesphomeapi majors until esphome moves
- **Where:** #13 is `UNSTABLE` on a stale red (its branch predates the AVX-512 fix and v7 bump) and unmergeable on merit (esphome pins 45.10.3 exactly). The new dependabot group prevents future splits but won't retire this one.
- **Fix:** close with that sentence; add a dependabot `ignore` for aioesphomeapi major updates.
- **Effort:** S — closing the PR is the account owner's call.

#### F3 — `firmware-weekly` is the one job with no Rust toolchain or cache
- **Where:** `ci.yml:298-332` runs `render_audio.py` → cargo, relying on the runner image's rustup; pays an uncached cold build weekly, unwatched.
- **Fix:** copy the four toolchain/cache lines from the esphome job.
- **Effort:** S

#### F4 — Edition 2021 → 2024 (deliberately deferred yesterday; do with A1)
- **Where:** `core/Cargo.toml:12`; `cargo fix --edition` ran clean when probed.
- **Effort:** S

---

## G — Performance & Scalability — B+

The heavy items all landed: decode cache ~2.5 GB → ~400 MB in both languages,
the keep-alive body pin gone, the lean page an Arc clone, the portable page
O(1) in scene count with a ceiling that fails. Held at B+ by the unshipped
payoff and one missed clone:

#### G1 — Ship the Rust studio as the default (carryover; the CI gate now exists)
- **Where:** `Makefile:82-83` and `.claude/launch.json` still run `tools/studio.py`; 4,202 Rust lines vs 2,031 Python for the same surface, reachable only via `CASTLE_STUDIO_CMD`. The e2e matrix now gates it continuously — the plan's off-season flip is de-risked.
- **Fix:** flip `make studio` + launch.json to the binary with a Python fallback; retire `studio*.py` per the plan once the season ends.
- **Effort:** M

#### G2 — The waveform cache deep-clones thousands of JSON nodes per request under a mutex
- **Where:** `core/src/studio_media.rs:209,218` — the exact pattern B4 fixed for the lean page, one file over; Python returns by reference. The Rust studio is strictly slower than Python on the Tracks panel's hottest route.
- **Fix:** `Arc<Json>` in the cache, `Arc::clone` out, guard dropped before return.
- **Effort:** S

---

## H — Documentation & Onboarding — B+

Yesterday's refresh verified almost entirely accurate (bins, routes, float
profile, setup path, parity commands). What remains is same-day staleness —
paragraphs written at noon that the evening's commits outran — plus one
structural gap:

#### H1 — CLAUDE.md describes the previewer budget the evening replaced
- **Where:** `CLAUDE.md:43-52` — names `gen_previewer.PAGE_BUDGET_KB` (moved to `previewer_budget.py`), says over-budget "fails the build and writes nothing" (it now un-inlines from the back and only fails when nothing is inlined), cites a ratchet that was deleted.
- **Fix:** rewrite the paragraph to the fit-then-fail behaviour and repoint symbols.
- **Effort:** S

#### H2 — Two live env knobs are documented nowhere operator-facing
- **Where:** `CASTLE_STUDIO_CMD` (the only local way to run the e2e suite against the Rust studio; only in CI YAML + playwright config — and an empty value means a server command of `""`) and `CASTLE_BUILD` (in `helpers.SANDBOX_ENV` but missing from CLAUDE.md's sandboxing list).
- **Fix:** one bullet each in CLAUDE.md Sandboxing; a clause in PARITY.md's studio row.
- **Effort:** S

#### H3 — README counts are stale: "three" sandbox vars (now four), 7 of 10 scenes listed, `rust-coverage` absent from both target lists
- **Where:** `README.md:136` ("Three environment variables"), `:173-181` (scene table missing `crypt` + the imported songs that make 10/12 real), `:151` + `CLAUDE.md:58-61` (target lists stop before `rust-coverage`); also `web/src/device_panel.ts:23` "nine scene tracks" and `web/MIGRATION.md:6` "56 modules" (now 59).
- **Fix:** the five one-liners.
- **Effort:** S

#### H4 — The design record (`PROJECT_NOTES.md`, `docs/notes/`) has zero mentions of Rust
- **Where:** README line 7 sends newcomers there for *reasoning*; the files describe a Python-and-C++ project.
- **Fix:** one entry — why a Rust crate, why zero-dep, why the twin studio — pointing at PARITY.md.
- **Effort:** M

#### H5 — PARITY.md claims Linux x86_64 verification without naming the AVX-512 mechanism; API.md points at the wrong file for the route table
- **Where:** `docs/PARITY.md:38-43` (a local x86_64 contributor hits the libm AssertionError with no doc to land on — the `NPY_DISABLE_CPU_FEATURES` story lives only in CI + the assertion text); `docs/API.md:58` says the table is in `tools/studio.py` (it lives at `tools/studio_http.py:242`).
- **Fix:** two sentences in PARITY's runbook; one path in API.md.
- **Effort:** S

---

## I — Developer Experience & Tooling — A−

I1–I4 closed better than asked: one definition of the Rust gate, guard
patterns consistent across Makefile/hook/core_bins, and the working-directory
trap documented at all three sites (which is exactly how D1's ten stragglers
were caught). Remaining friction:

#### I1 — `tools/castle_fuzz.py` is at 499/500; the next one-line change reddens the hook
- **Where:** 499, with `tests/test_import.py` (495), `tests/test_firmware_contract.py` (477), `core/src/studio_scenes.rs` (475), `tools/gen_esphome.py` (474) queued behind it. The cap pressure moved out of `web/src` and this is now the standing commit-blocker.
- **Fix:** split `castle_fuzz.py` (generator/oracle seam) and `test_import.py` now, on chosen seams.
- **Effort:** S each

#### I2 — The grade reports are in the LOC cap's scope and two are within 20 lines of it
- **Where:** `.claude/grade-report-2026-08-16.md` (487), `-23` (480); yesterday's E2 fix text proposed gitignoring `grade-report*.md` and that half wasn't done — the next audit could fail `make check` on its own output.
- **Fix:** gitignore them, or `EXEMPT_PATHS` with "audit output, not code".
- **Effort:** S

#### I3 — A soft warning band that fails the hook at 490 would keep 499 from recurring
- **Where:** `tools/check_loc.py` warns at 450+, fails at 500 — `castle_fuzz.py` reached 499 legally.
- **Fix:** hook-failing threshold at 490 (the 450 warning unchanged).
- **Effort:** S

---

## Grade movement vs 2026-08-31

| | Was | Now | Why |
|---|---|---|---|
| A | B+ | B+ | splits landed well; A7 unresolved and heavier |
| B | B+ | **A−** | every fix verified, several beyond spec; two edge items left |
| C | B+ | **A−** | cap pressure cleared from web/src; guard coherent; small strays |
| D | B | **B+** | CI green ×3 on real iron; parity tightened; D1's pin bypass + bare studio layer hold it |
| E | B+ | B+ | new code careful; E1 is a real fault-path bug |
| F | B+ | **A−** | pins immaculate; F1 is a real (empirically proven) gap |
| G | B+ | B+ | heavy wins landed; payoff still unshipped (G1) + G2 |
| H | B− | **B+** | refresh verified accurate; same-day staleness only |
| I | B− | **A−** | closures better than asked; cap pressure is the residue |
| **Overall** | **B+** | **A−** | integration debt gone; CI proves the parity story on three platforms |
