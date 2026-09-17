# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware + castle-core
**Audited:** 2026-09-06 (regrade; the 2026-09-01 audit, all 32 items executed,
is archived at `.claude/grade-report-2026-09-01.md`)
**Tree:** `b7fc20a`. **main is NOT origin/main** — three commits are unpushed
(`c65bec7` v5.44, `48e6503` v5.45/S3, `b7fc20a`), so none of the firmware work
of the last two days has ever been through CI. **CI is RED on main** and has
been since 2026-09-02 (run 33592881896, `python` job, `test_protocol_fuzz`
×2 — a real firmware bug, see J1). 17 commits and +6,928/−3,017 lines since
the tree the last report graded.

**How this was graded.** Everything below was run on this tree, not quoted
from the last report. `make check` **exit 0** — 1,021 Python tests, 0 skips,
ruff + `ruff format` clean, mypy clean over 132 files, LOC check 369 files
with the largest at 480/500, citation check 122 citations all dated, `tsc
--noEmit` clean, 15 node suites green. `make rust-test` **76 passed**.
`make lint` exit 0. `make validate` — **both** `castle_sd.yaml` and
`castle_s3.yaml` valid under ESPHome 2026.8.1. `npx playwright test --list`
— **148 tests in 20 files** (listed, not run: it needs a browser install and
an order of magnitude more time than everything else together). `make
coverage` — tools/ at **85%** (floor 82). `make rust-coverage` — core/ at
**40.06%** line coverage. GitHub CI read run-by-run and job-by-job with `gh`.
Four parallel audit passes (firmware, backend/Rust, frontend/docs,
testing/CI/deps), each item anchored to file:line, and the two highest-leverage
findings reproduced by hand before being written down.

**Permanently accepted risk — do not re-raise.** Studio Origin/Host
validation; firmware OTA/file endpoint auth.

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | A− | 3 |
| B | Backend Quality | B+ | 4 |
| C | Frontend Quality | A− | 3 |
| D | Testing & Reliability | B | 5 |
| E | Security | B+ *(excl. accepted risk)* | 2 |
| F | Dependencies & Tech Currency | B+ | 3 |
| G | Performance & Scalability | A− | 2 |
| H | Documentation & Onboarding | B | 5 |
| I | Developer Experience & Tooling | B+ | 4 |
| J | Firmware | B | 6 |
| **Overall** | | **B+** | **37** |

**Top 5 highest-leverage fixes:** J1, B1, D1, J2, I1

The code written in the last five days is good — the two-board firmware split
is the cleanest architectural seam in the repo, the Python studio's retirement
is planned like an engineering project, and the S3 target arrived with 26
tests before the board exists. What slipped is **the loop that proves it**.
Main has been red for four days on a genuine firmware bug the developer's Mac
structurally cannot reproduce (J1); the newest firmware work is not pushed at
all; the one gate that compiles that firmware has never once succeeded, and
the S3 half of it cannot fail even when it runs (D1). Two real fault-path
bugs are open in the *default* production server (B1, B3). Against that: 1,021
+ 76 + 148 tests green, both configs valid, exemplary skip discipline, and
every one of the 2026-09-01 report's 32 closures re-verified as holding
(one, D2, only partly). This is the same engineering at a lower altitude of
verification — hence one notch down, not several.

---

## Verification of the 2026-09-01 closures

All 32 items were re-checked against this tree. **Thirty-one hold**, several
beyond their spec: the `Busy` Drop guard (grade report 2026-09-01 E1) is proven
by a test that deliberately panics a decode; `tests/cargo_gate.py` (grade report
2026-09-01 D1) replaced ten bypassing call sites with one door that also asserts
the pinned channel out loud; `web/tools/check-suites.mjs` (grade report
2026-09-01 D5) additionally requires every non-helper `test/*.ts` to be claimed
exactly once. One is **partial** and is carried forward as D5 below: grade
report 2026-09-01 D2 asked for tests on the bare Rust studio layer, and 28
`#[test]`s landed — but `core/src/studio.rs` (332) and `core/src/studio_import.rs`
(290) still have zero.

Two claims the 2026-09-01 report made that are **no longer true**:

- "**CI green** (three consecutive runs)" — CI has been red on main since
  2026-09-02, and the three newest commits have never been submitted to it.
- Its F3 closure ("`firmware-weekly` gets a toolchain and a cache") landed
  correctly, but the job it fixed **has never produced a green run**: exactly
  one scheduled run exists in the repository's whole history (2026-08-31,
  failed in 5 s on the billing outage). The dram0 alarm, the S2 compile and
  now the S3 compile have never actually executed.

---

## A — Architecture & Design — A−

Up from B+. The S2/S3 split is the best structural work in the repo's history:
`castle_sd_common.yaml` holds the show, `castle_sd.yaml` keeps only the
Feather's own NeoPixel, `castle_s3.yaml` states nothing that is not chip or
board, and `gen_rig.py` emits `!extend` RMT overrides so the rig still has one
description — with `tests/test_firmware_s3.py:421` as the control proving the
S2's generated files did not move a byte. The crate's `native` feature landed,
edition 2024 landed, all eight bins are declared, and the Python studio's
retirement has a written plan with a frozen golden corpus as its phase-0
artifact. Held off A by three seams that did not get the same care:

#### A1 — `castle_sd_jewels.yaml` is a byte-identical duplicate whose header warns about a difference that no longer exists
- **Where:** `firmware/castle_sd_jewels.yaml:20-22` sets `pin_towerL: "18"` and `channels_towerL: "GRBW"` "to override castle_sd.yaml's onboard 33" — but `firmware/castle.yaml:51,63` already are those values, and `castle_sd.yaml:31-41` is substitutions-and-comments. `esphome config` on the two files produces the same document. The live harm is `:7-11`: *"DO NOT flash this before the jewels are physically wired: it drives A0 (GPIO18) … The onboard-pixel build (castle_sd.yaml) stays the default until the hardware exists."* Both halves are false — `castle_sd.yaml` has driven GPIO18 since v5.31 (`docs/ISSUE-ring-flicker.md:121-124`) and the jewels are wired. CI validates it as a fifth variant (`.github/workflows/ci.yml:311`).
- **Fix:** delete the file and drop it from the CI variant loop.
- **Effort:** S

#### A2 — `repo_root()` anchors on the one file the retirement plan deletes first
- **Where:** `core/src/studio.rs:288-305` walks ancestors looking for `tools/studio.py` and falls back to `PathBuf::from(".")`. `docs/RETIREMENT.md:31` lists `tools/studio.py` first among the files phase 3 removes. On that day the Rust studio silently roots itself at `"."` and every derived path breaks at once — `App::scenes`, `App::tracks`, `build_root()`, `studio_proc::py()`'s `.venv` lookup, `studio_scenes::check()`'s `tools/scene_check.py`. Nothing pins the anchor and phase 3 does not mention it.
- **Fix:** anchor on something that survives the retirement (`Makefile` or `scenes/scenes.yaml`), add a `#[test]` that the anchor exists, and add the line to RETIREMENT.md's phase-3 checklist.
- **Effort:** S

#### A3 — `core/src/wasm.rs` is the one module the native/wasm split missed, and it carries the crate's only `unsafe impl Sync`
- **Where:** `core/src/lib.rs:66` — `pub mod wasm;`, unconditional: neither feature-gated nor `cfg(target_arch = "wasm32")`-gated, unlike everything else the grade report 2026-09-01 A1 split touched. `core/src/wasm.rs:14-17` declares `unsafe impl Sync for Scratch` on the justification "WASM is single-threaded; there is exactly one caller", and `wasm_render` (`:26-30`) is a `pub extern "C"` writing that shared static — compiled into the multithreaded native `cdylib` and every native bin, where the stated invariant is false. Latent, not live: no in-tree native caller exists.
- **Fix:** `#[cfg(target_arch = "wasm32")] pub mod wasm;`, so the SAFETY comment states something the compiler enforces.
- **Effort:** S

---

## B — Backend Quality — B+

Down from A−. The craft is unchanged and in places exceptional —
`core/src/studio_media.rs` budgets its decode cache in floats resident with
the arithmetic shown, never evicts the newest entry, and frees its in-flight
marker through a real `Drop` guard proven by a panic-injection test; every
write in the tree is atomic and names the failure that taught it; the crate has
zero TODO/FIXME and one `#[allow]`. But the server that `make studio` now
starts by default has a way to be killed outright by one request, and that is
a category of defect the Python twin does not have.

#### B1 — One request kills the entire Rust studio process; the Python twin answers 500 and keeps serving
- **Where:** `core/src/jsonio.rs:229` — `parse_val` recurses per nesting level with no depth counter. Reached from `core/src/studio_routes.rs:46` (`json_body`, every POST route) and from `core/src/studio_relay.rs:150`, which parses the *castle's* reply the same way. **Reproduced on this tree**, sandboxed studios on 8871/8872, body = `b"[" * 100000`: Rust → connection dropped, `thread '<unknown>' has overflowed its stack / fatal runtime error: stack overflow, aborting`, process gone; Python → `HTTP 500 {"ok": false, "error": "RecursionError: …"}` and still answering afterwards. Every existing net misses it: the `catch_unwind` at `core/src/bin/studio.rs:80` cannot catch an abort, and `MAX_BODY` (512 MB, `core/src/http_parse.rs:11`) is five thousand times larger than the ~20 KB needed. Every other in-flight connection dies with it.
- **Fix:** thread a depth counter through `parse_val`, refusing past ~200 (CPython's own limit is 1000 with a far larger frame); one `#[test]` and one golden case so the corpus records the refusal string.
- **Effort:** S
- **Grade lift:** B+ → A− (the last place the twins differ under fault, again)

#### B2 — The import job's progress bar cannot fire, in either language, and both have green tests over the unreachable path
- **Where:** the parsers are `tools/studio_jobs.py:27` (`_PROGRESS`) and `core/src/studio_jobs.rs:292`. The reason nothing reaches them: `tools/import_fetch.py:80-81` runs yt-dlp with `capture_output=True`, so no `[download] …%` line ever hits the studio's pipe, and `tools/import_convert.py:96-97,119,130` captures ffmpeg the same way. The only marker that can fire is `startswith("imported ")` (`tools/import_track.py:413`), printed after the work is done. The desk shows a percent permanently at 0 (`web/src/track_import_url.ts:63-65`), and both unit suites feed the parser synthetic lines production cannot produce (`tests/test_studio_jobs.py:60-103`, `core/src/studio_jobs.rs:329-341`).
- **Fix:** either stream yt-dlp through (`--newline` + line-forwarding `Popen` in `import_fetch.py`) so the parser gets real input, or delete the phase in all three places. If you stream, fix the twins together: Python's `text=True` (`studio_jobs.py:114`) translates `\r`, Rust's `BufReader::lines()` (`studio_jobs.rs:224`) does not and would buffer the whole download into one line.
- **Effort:** S

#### B3 — The splice route's Python child is the one subprocess with no watchdog
- **Where:** `core/src/studio_scenes.rs:88-107` spawns `tools/scene_check.py` and blocks in `child.wait_with_output()` with no deadline. Every other child in the crate goes through `studio_proc::run` (`core/src/studio_proc.rs:77-135`) under a 900 s watchdog or `studio_jobs::run_child`'s wall-clock kill. A wedged interpreter pins the connection thread for the life of the process. The Python twin validates in-process and has no equivalent exposure.
- **Fix:** route it through `run_split(cmd, 60)` like the other five call sites.
- **Effort:** S

#### B4 — One bad `?cooldown=` permanently kills the emulator's tick thread
- **Where:** `tools/castle_emu.py:247` — `int(cool)` on unvalidated queue input from `tools/castle_emu_http.py:382`; `_ticker` (`castle_emu.py:204-216`) wraps `_apply` in `try/finally`, not `try/except`. `POST /api/pir?cooldown=abc` answers `{"queued":true}`, then the thread is gone and every later command is silently ignored. It is also a wire divergence: the firmware uses `atof` (`firmware/castle_sd_common.yaml:252`) and shrugs. Leverage is in the blast radius — the emulator is the substrate for `test_firmware_contract`, `test_castle_emu`, `test_bridge_rust` and the chaos suites, and a dead ticker makes a suite hang or assert on stale state instead of failing cleanly.
- **Fix:** parse like `atof`, and put an `except Exception` that logs and keeps ticking around `_apply`.
- **Effort:** S

---

## C — Frontend Quality — A−

Held. Every 2026-09-01 item verified closed: `waveform.ts` is 406 with
`waveform_view.ts` split out, `tsconfig.json:26` covers `test/**/*.ts`
(e2e included) and `playwright.config.ts`, and the `dom_discipline` guard —
which walks `src/` recursively on purpose — finds **zero** offenders across
100 files. There is no `any`, no `<any>`, no `as any`, and **no `@ts-ignore`
or `@ts-expect-error` anywhere in `web/`**; the four `as unknown as` are narrow
`window` reads. No runtime import cycles (every back-edge is `import type`),
no dead modules. What is left is small and none of it is a type hole:

#### C1 — Two exported `mmss` functions with different rounding; one renders "0:60"
- **Where:** `web/src/import_opts.ts:62-63` uses `Math.round(s % 60)`; `web/src/wave_clip.ts:21-22` uses `Math.floor` and documents why ("a start that rounds *up* eats the first transient"). Neither references the other. The rounded copy is reachable with a float — `import_opts.ts:102` computes `(FLASH_FREE - usedBytes) / bps` and `:106` renders it. Verified in node: `59.6 → "0:60"`, `119.7 → "1:60"`, `3599.8 → "59:60"`.
- **Fix:** delete `import_opts.ts`'s copy and import `mmss` from `wave_clip.ts` — flooring is right for a capacity readout too.
- **Effort:** S

#### C2 — ~13 module-private values are exported, and nothing detects it
- **Where:** each referenced only inside its own file — `web/src/effects.ts:46` `PALETTES`, `stage.ts:50` `APERTURE`, `device_chip.ts:29,47`, `track_card.ts:101,136`, `stems_draw.ts:16,20`, `onsets.ts:108`, `import_opts.ts:43`, `track_style.ts:119`, `device_probe.ts:41`, `wave_analysis.ts:50`, plus ~45 type names in the same position. `noUnusedLocals` (`web/tsconfig.json:19`) does not see exports and there is no `ts-prune`/`knip`.
- **Fix:** drop the `export` where it is not a seam; the durable version is a small checker in `web/tools/` in the style of `check-suites.mjs`, wired into `npm test`, with an allow-list for documented API.
- **Effort:** M

#### C3 — `clamp` is defined three times, twice byte-identically
- **Where:** `web/src/track_sections.ts:88`, `wave_analysis.ts:47`, `waveform_view.ts:58`. The float→hex-colour conversion is likewise duplicated at `insets.ts:123` and `panels.ts:275`. None of the copies disagree today — unlike C1 — so this is tidiness, not risk.
- **Fix:** a `web/src/num.ts` (or `dom.ts`, already the shared-primitives home).
- **Effort:** S

---

## D — Testing & Reliability — B

Down from B+. The suite itself is in excellent shape and its skip discipline
is the best thing in the repo: all 30 skip sites are either `X is None and not
IN_CI` or guarded on an artifact a *named* CI step produces, so a runner that
loses cargo/clang/ffmpeg goes red rather than green — and `tests/test_firmware_cxx.py:52-54`
writes the rule down. 1,021 tests with exactly one `except Exception` in all of
`tests/`, and it re-raises. The grade is about the gates *around* it: one new
CI gate cannot fail, two local gates have no CI home, and the gate that did
catch a real bug caught it in a place the developer cannot reach.

#### D1 — The weekly firmware compile cannot fail: `| tee` with no pipefail, then `|| true`
- **Where:** `.github/workflows/ci.yml:363` (`esphome compile firmware/castle_sd.yaml | tee /tmp/build.out`), `:378` (the S3 equivalent), `:380` (`grep -E '^(RAM|Flash):' … || true`). GitHub's default shell for a plain `run:` step is `bash -e {0}` — **no pipefail**; only an explicit `shell: bash` adds it. The step's exit status is `tee`'s, always 0. This repo already knows the asymmetry: `.github/actions/media-tools/action.yml:65-67` says "GitHub runs composite bash with -o pipefail". For the S2 the failure is caught *by accident* (the next step greps an empty file, `float('' or 101)` → 101 → over the 92% alarm). For the **S3** — added precisely because "the carrier build has no porch and no operator to notice it rotting" (`ci.yml:373-376`) — the only follow-up is `|| true`. The new gate gates nothing. And the job has never run green even once (one scheduled run in the repo's history, 2026-08-31, failed in 5 s).
- **Fix:** `defaults: { run: { shell: "bash -eo pipefail {0}" } }` at workflow level; replace `grep … || true` with a check that the RAM/Flash lines exist. Then trigger the job by hand (`gh workflow run`) so it produces a green run at least once.
- **Effort:** S
- **Grade lift:** B → B+ (the firmware's only compile gate becomes real)

#### D2 — The protocol fuzz can only find the bug it found on Linux; `make check` on the developer's Mac structurally cannot
- **Where:** `tools/fuzz_corpus.py:57-58` puts `"%ff"` and `"%80"` in `ATOMS`, and `POISON` (`:64`) — the bytes the fuzz keeps *off* the card — is `b'"\\' + bytes(range(0x20))`, which stops at 0x1F. So the fuzzer deliberately PUTs names holding a lone 0x80. On ext4 that file is created and J1 fires. On APFS the create fails with `Errno 92 Illegal byte sequence` (verified directly) and the emulator's answer is a documented 5xx, so the case is legal and the storm passes. Result: a gate whose strength depends on the filesystem, red in CI, green on the only machine anyone runs it on.
- **Fix:** have the fuzz's card jail hold names as *escaped* on-disk spellings (or run the storm against an in-memory card), so the byte the wire carries is independent of what the local filesystem will accept. Then close J1 and extend `POISON` in the same commit.
- **Effort:** M

#### D3 — `ruff format --check` and `tools/check_image.py` are local-only gates with no CI home
- **Where:** `Makefile:272` and `githooks/pre-commit:10` run `ruff format --check`; `.github/workflows/ci.yml:157-160` runs only `ruff check` and `mypy`. `Makefile:277` runs `check_image.py castle-sd`; it appears nowhere in CI, and `check: audio test lint` has no `build` dependency, so `tools/check_image.py:52-54` returns 0 with "no built image … run `make build` first" whenever you have not just built. Every other gate in `make lint` has a CI counterpart. The hook only exists after `make setup`, so a web edit or a Dependabot PR can land formatting drift and redden the *next* person's `make check`.
- **Fix:** add `ruff format --check tools tests` to the python job; run `check_image.py` in `firmware-weekly` right after the compile, which is the only place an image exists.
- **Effort:** S

#### D4 — The frame-exact effect parity harness hardcodes "13" outside the four-way vocabulary guard
- **Where:** `tests/cxx/parity_dump.cpp:84` — `% 13` — and `web/test/firmware_parity.ts:57`, a hand-written 13-name array. The four *authoritative* copies are guarded: `tests/test_pulse_dynamics_parity.py:200-211` parses `web/src/effects.ts`, the `EffectName` union and `castle_effects.h`'s `EFF_` enum and holds all three to `tools/effect_vocab.py:17`. The harness is outside it. Add a 14th effect and the guard goes red (good), you fix all four, and then `parity_dump.cpp` never emits `eff == 13`, the TS array never names it, and `firmware_parity.ts:292-293` (`if (!s.n) continue;`) silently prints nothing for it — a green frame-exact run that has never evaluated the new effect. That is the "invisible divergence" `docs/PARITY.md:26-28` names as the worst bug the project can have.
- **Fix:** emit an `EFF_COUNT` sentinel from `castle_effects.h` and use it for the modulus; derive `NAMES` from `Object.keys(EFFECTS)` and assert its length against the count the dump reports; turn `if (!s.n) continue` into a failure.
- **Effort:** S

#### D5 — The coverage ratchet has never ratcheted, and 622 Rust lines still have zero `#[test]` (was 2026-09-01 D2, partially)
- **Where:** `Makefile:189-197` promises "the floor is the measurement minus one, and it moves UP whenever a fresh `make coverage` beats it"; `COVERAGE_MIN := 82` has been touched exactly once, on 2026-08-21, 148 commits and 19 new `tools/` modules ago. Measured today: **85%**. On the Rust side `core/src/studio.rs` (332) and `core/src/studio_import.rs` (290) still carry no `#[test]` — `lean()` (`studio.rs:129-184`, a hand-rolled byte scanner) and `parse_multipart`'s callers are where a bug would hide. `make rust-coverage` reports **40.06%** overall, with `studio_routes.rs` at 20.12% and `studio_tracks.rs` at 25.25%, and it runs in no CI job.
- **Fix:** raise `COVERAGE_MIN` to 84 now and either automate the raise or delete the ratchet language; add the non-gating `make rust-coverage` to the `rust` job so the other half is visible; unit-test `lean()` and the multipart boundary.
- **Effort:** M

---

## E — Security — B+ *(excluding accepted risk)*

Held. New code stays careful — filename refusals, pre-allocation length
checks, log scrubbing, argv-only subprocess, audited `unsafe` with the
arm64 `fcntl` variadic bug written down (`core/src/bin/studio.rs:201-207`).
Two items, both availability rather than confidentiality:

#### E1 — The unbounded-nesting abort is reachable from the network in the documented `--lan` mode
- **Where:** B1's defect, viewed from here: `core/src/jsonio.rs:229` is reached by any POST, and `tools/studio_launch.sh` passes `--lan` straight through (`Makefile:99`, `ARGS="8766 --lan"`). ~20 KB of `[` from anyone on the porch WiFi ends the studio process, not the request. `core/src/studio_relay.rs:150` parses the *castle's* reply through the same function, so a garbled device answer reaches it too.
- **Fix:** B1's depth counter closes both faces.
- **Effort:** S

#### E2 — Upload staging is one shared directory, written outside the lock and deleted after it
- **Where:** `tools/studio_routes.py:325-341` and its twin `core/src/studio_import.rs:130,137,163` — both stage into a single `tracks/_upload/` *before* taking the lock and `rmtree` it after releasing. Upload B, staged and waiting on the lock, has its file deleted by upload A's cleanup; any raise from the run leaks the directory. `tools/import_track.py:302-313` already solved exactly this with `_incoming_<pid>` + `try/finally`, with the race written into the comment.
- **Fix:** stage under `_upload_<pid>_<rand>/`, remove in a `finally`. Both languages in one commit or the parity gate reddens.
- **Effort:** M

---

## F — Dependencies & Tech Currency — B+

Down from A−. 113 exact pins, zero floating lines, the advisory ledger down to
five documented starlette entries with review dates, the cargo ecosystem
registered while still a no-op, all Actions on v7, and the `aioesphomeapi`
major-version ignore landed as specified. The gap is that "113 exact pins" is
a statement about macOS.

#### F1 — `requirements.lock` is a darwin freeze, so Linux transitives float on the platform CI runs
- **Where:** `tools/lock_deps.py:91` builds the throwaway venv with `sys.executable` — the developer's darwin python. `requirements.lock` carries exactly four marker lines (`:75-78`), all `sys_platform == "darwin"`, and not one Linux marker. Proven empirically: the lock pins `bleak==2.1.1` and nothing else in that tree, while run 33592881896's install log shows the ubuntu runner resolving and installing `dbus-fast-5.0.22`, which appears nowhere in the lock. `.github/workflows/ci.yml:131` installs the lock as the whole dependency story.
- **Fix:** resolve universally (`uv pip compile --universal`, or a per-platform `--dry-run --report` merged in), or install the lock with `--no-deps` so an unpinned transitive is a hard failure. `ci.yml:335`'s `--only-binary=":all:"` in `firmware-weekly` is the right instinct one step short.
- **Effort:** M

#### F2 — Two Dependabot PRs are open and red, one of them the exact PR the new ignore rule was written to prevent
- **Where:** PR #14 (`aioesphomeapi ~=45.10 → ~=46.2`, grouped) and #15 (`coverage ~=7.15 → ~=7.16`), both opened 2026-09-01T05:12 — *before* the `ignore` block landed at `.github/dependabot.yml:33-34`, so the rule does not retire them. #14 fails at `pip install -r requirements.lock` (run 33475070979), which is exactly the unmergeable-on-merit outcome grade report 2026-09-01 F2 predicted for #13. An open red PR list is a broken window on a repo whose CI story is otherwise its strongest asset.
- **Fix:** close #14 with that sentence; re-run or rebase #15.
- **Effort:** S — closing is the account owner's call.

#### F3 — Five hardcoded `"1.88.0"` literals in CI, with nothing tying them to the pin
- **Where:** `.github/workflows/ci.yml:53,118,215,286,346`; the real pin is `core/rust-toolchain.toml:20`. `tests/cargo_gate.py:32-36` already parses the channel out of the file and `tests/test_castle_core.py:137-138` asserts cargo reports it — but nothing asserts the *YAML* matches. `ci.yml:47-50` names the drift mode and accepts it; the consequence of a bump is five stale toolchain installs and five `rust-cache` workspaces keyed to the wrong compiler.
- **Fix:** a ~10-line test reading `ci.yml` and asserting every `toolchain:` value equals `cargo_gate.pinned_channel()`.
- **Effort:** S

---

## G — Performance & Scalability — A−

Up from B+. The deferred payoff shipped: `tools/studio_launch.sh` makes the
Rust studio what `make studio` starts, with a Python fallback that prints its
reason, and the waveform cache returns `Arc::clone` instead of deep-cloning
thousands of JSON nodes under a mutex. The decode cache budgets in bytes
resident in both languages. What is left is one lock held too long and some
unbounded growth that a single-operator tool will never notice:

#### G1 — `rebuild()` holds the studio's single oplock across the network publish
- **Where:** `tools/studio_scenes.py:68-89` and `core/src/studio_scenes.rs:236-262` — the lock covers the three generators (deliberate and documented) **and then** `publish`, which pushes scene mp3s and the lean page to the castle over porch WiFi at up to a 60 s read budget per file (`tools/castle_link.py:52`, `core/src/studio_relay.rs:52`). Every import, refresh and codec compare queues behind a card sync that has nothing to do with CPU contention.
- **Fix:** release after the third generator and push outside the lock — the push already tolerates failure without failing the rebuild. Both languages.
- **Effort:** M

#### G2 — `job.log` grows unbounded, and codec-comparison temp dirs outlive the process
- **Where:** `tools/studio_jobs.py:117` and `core/src/studio_jobs.rs:230` append every child line forever while only `[-40:]` is ever served (bounded only by the 40-job registry cap). `tools/studio_media.py:98-120` — `KEEP_COMPARES = 3` evicts only when a *fourth* comparison runs, so each Restart (`tools/studio_routes.py:266-269`) strands up to three `castle-cmp-*` trees.
- **Fix:** `deque(maxlen=200)` / a ring on the Rust side; sweep `castle-cmp-*` at startup.
- **Effort:** S

---

## H — Documentation & Onboarding — B

Down from B+. Every 2026-09-01 doc item verified closed — the budget paragraph,
the two env knobs, the README counts, the design record's Rust section, the
AVX-512 mechanism in PARITY.md. And `docs/PARITY.md` remains a genuinely good
contract document: all 61 paths it names exist, all 22 test files exist, and
its "when one side is going away" section is unusual and correct engineering.
The grade falls because the last three commits — the largest firmware change
in the project's history — updated README.md and `firmware/pending/README.md`
and left the file that *governs* behind, along with the record a future session
is told to read first.

#### H1 — CLAUDE.md, the file that governs, still says there is one castle build
- **Where:** `CLAUDE.md:47-55` ("`castle_sd.yaml` is THE build … `castle_sd_jewels.yaml` and `bench*.yaml` are variants OF the SD build, and `castle.yaml` is the shared core") and `:85-87` ("there is one castle build now"). Commit `48e6503` added `firmware/castle_s3.yaml` (114), `firmware/castle_sd_common.yaml` (331), `firmware/generated/lights_s3.yaml` and four Make targets (`Makefile:134,149,152,155`). None appear in CLAUDE.md, which also never says that `castle_sd_common.yaml` — not `castle_sd.yaml` — is where the show now lives. Two knock-ons in the same file: `:184` ("Scene ceiling: 12 scenes max on this board") now has two boards with different RAM, and `Makefile:59`'s `make help` prints the same one-build line.
- **Fix:** rewrite the `firmware/` bullet to name the three buildable targets and the common file, add the four `-s3` targets to the Make list, and qualify the scene ceiling as the S2's.
- **Effort:** S

#### H2 — The design record's "where the project stands" was outrun by the next two commits, and sends the operator to a panel that no longer exists
- **Where:** `docs/notes/05-decisions-and-roadmap.md:78-95`, written by `db01290` explicitly "so the next session starts from the record rather than memory". It says "**One firmware build**, `castle_sd.yaml`, v5.43" (`firmware/castle.yaml:27` is `5.45`, and there are two), "994 Python tests" (measured: **1,021**), and under *Waiting on hardware*: "OTA to **5.43**, confirm it on the panel" — `c65bec7` deleted the eInk panel. The Rust (76), browser (148), parity-suite (13) and golden (39) counts in the same block are correct.
- **Fix:** re-date the standings block, bump to v5.45/two builds, replace "the panel" with the web page, refresh the test count.
- **Effort:** S

#### H3 — The flash-day checklist tells you to confirm a version that is not the one being built
- **Where:** `firmware/pending/README.md:30-34` — "Flash day is therefore: `make ota` …, confirm **5.44** on the web page". `firmware/castle.yaml:27` is `5.45`. This defeats the repo's own verification protocol (`docs/RUNBOOK.md:79-81`: the version on screen is how you *prove* the OTA took) — an operator following the file sees 5.45 and concludes the flash failed.
- **Fix:** phrase it as "confirm the version in `firmware/castle.yaml` appears on the web page", so it cannot drift again.
- **Effort:** S

#### H4 — Three places where prose states the opposite of what the firmware shipped
- **Where:** (a) `firmware/sd_audio.h:22-25` — "The built-in scenes deliberately stay in flash … they must work when there is no card in the slot"; since `8e55f4e` the build embeds one chirp (`firmware/castle_sd_common.yaml:45`) and `docs/RUNBOOK.md:71-75` says "**The card is not optional.**" Worse, the *runtime* line an operator reads at the exact moment the card is missing on show night says the show is fine: `sd_audio.h:94` — `"no SD card mounted (%s) — flash scenes still work"`. (b) `docs/notes/05-decisions-and-roadmap.md:65-68` closes the roadmap with "**~~SD card streaming~~ — not possible**, and the whole-file path … shipped instead"; `sd_audio.h:11-20` says the reverse in the shipped code ("TRUE STREAMING … the earlier design … **is gone**"). (c) `HARDWARE_FINDINGS.md:216-219` still says the eInk and SRAM chip selects are "driven HIGH at boot in `castle_sd.yaml`"; `c65bec7` deleted them and updated five other docs but missed this one, which `PROJECT_NOTES.md:70-72` presents as a peer record.
- **Fix:** rewrite (a) to flash-holds-the-chirp and change the log line to name the real behaviour; rewrite (b) — streaming *was* achieved, via loopback HTTP; give (c) the same "gone since v5.44" dateline `docs/WIRING.md:5-7` got.
- **Effort:** S

#### H5 — Smaller stale references, each one the only concrete artefact in its sentence
- **Where:** `docs/SECURITY.md:7` names the studio's "default port 8820" — it is **8765** (`tools/studio.py:209`, `core/src/bin/studio.rs:25`, `.claude/launch.json`), and `CONTRIBUTING.md:23-25` sends every contributor to that file. `README.md:217-218` lists 12 effects; the vocabulary is 13 and the missing one, `blood`, is in the show today (`scenes/scenes.yaml:292`). `core/src/lib.rs:23-29`, the crate's front page, still says "the Python one is what `make studio` still starts … Nothing here is the show's path yet", and lists three parity dumps where there are four. `CLAUDE.md:152-154` puts `py()`/`check_py()` in `studio_scenes.rs`; they are in `core/src/studio_proc.rs:24,43`. `docs/PARITY.md:137` cites `ci.yml:101` for the AVX-512 export (it is `:104`), and `:104`'s "SKIP (loudly)" claim covers the browser gate but not the ~19 Python `skipIf` reasons that `make test -q` never prints. `docs/API.md:56-58` says every route also answers its old `/api/…` spelling; `publish` and `scene-audio` have no alias.
- **Fix:** seven one-liners. Prefer citing symbols over line numbers into a churning workflow.
- **Effort:** S

---

## I — Developer Experience & Tooling — B+

Down from A−. The tooling itself is excellent and got better: one definition
of the Rust gate, `tests/cargo_gate.py` as the single door, `web/tools/check-suites.mjs`
holding the bundled set equal to the executed set with a written justification
for not globbing, `.github/actions/media-tools/action.yml` as a
postmortem-as-code with the failed fix recorded as a *wrong* fix, error text
that is a designed artifact throughout. What costs the grade is the state of
the working copy and three gates that do not run where they are believed to.

#### I1 — Three commits — all of the v5.44/v5.45 firmware work — are unpushed, and main has been red for four days
- **Where:** `git log origin/main..HEAD` = `c65bec7`, `48e6503`, `b7fc20a`. `origin/main` is `db01290`, whose CI run (33592881896, 2026-09-02) failed. So: the eInk removal, the entire second board, 431 lines of new tests and the emulator fix have never been submitted to a gate, and the last thing that *was* submitted is red. The 2026-09-01 report's headline was "earned in CI, not only on one Mac"; five days later the position has quietly reverted.
- **Fix:** fix J1, push, confirm the run, and trigger `firmware-weekly` by hand so the S2/S3 compiles produce a green result at least once.
- **Effort:** S

#### I2 — `gen_golden.py --check` is documented as part of `make check` and runs nowhere
- **Where:** `docs/PARITY.md:75` opens the block with "`make check` — everything below except the browser suite" and `:83` lists `gen_golden.py --check` inside it; `:68` doubles down ("fails when the committed files are stale"). `Makefile:276-282` does not run it and neither does any CI job — `grep -rn gen_golden Makefile .github/` finds only prose. The goldens are the half of the studio parity row designed to *outlive* `tools/studio.py`; their staleness gate being manual-only is the one integrity hole in an otherwise exemplary scheme.
- **Fix:** add `@$(PY) tools/gen_golden.py --check` to `check` — it boots one Python studio over a throwaway sandbox, seconds — or scope the PARITY.md sentence honestly.
- **Effort:** S

#### I3 — Cap pressure has re-accrued one band lower, and the S3 commit is what consumed the margin
- **Where:** `tools/check_loc.py --list` — `tools/gen_esphome.py` 480, `docs/castle-wiring.html` 479, `core/src/studio_routes.rs` 478, `tests/test_firmware_contract.py` 477, `tools/castle_emu_http.py` 475, `core/src/studio_media.rs` 472; the hook fails at 490 (`check_loc.py:53`). `gen_esphome.py` went **474 → 480 in `48e6503`** — the newest feature eating the margin is exactly the dynamic grade report 2026-09-01 I3 was written to prevent, and its `main()` is ~180 lines emitting four unrelated files (`:296-476`) with the seams already demonstrated elsewhere (`emit_show_playlist`, `emit_audio_sd`, `emit_rig_header`). Separately `docs/castle-wiring.html` (479) is machine-written by `tools/gen_wiring_diagram.py:426` but is deliberately *not* exempt (`tests/test_loc_scope.py:65` asserts it is measured), so a rig change that lengthens the SVG reddens a commit at a file nobody can split.
- **Fix:** split `gen_esphome.py` and `studio_routes.rs` now, on chosen seams; make an explicit call on whether `castle-wiring.html` is generated-and-exempt or human-owned.
- **Effort:** S each

#### I4 — Neither `make check` nor `make check-all` validates the ESPHome configs
- **Where:** `Makefile:276-282,301`. CI's `esphome` job validates five variants including the new `castle_s3` (`ci.yml:307-313`); nothing local does unless you type `make validate`. `make check`'s epilogue names the e2e gap explicitly (`Makefile:282`) and stays silent about this one — so a broken YAML is discovered by a push, which right now is not happening (I1).
- **Fix:** `check-all: check validate e2e`, or one more line in the `check` note. esphome is in `requirements.txt`, so any `make setup` venv has it.
- **Effort:** S

---

## J — Firmware — B *(new category this audit)*

The owner asked for the firmware specifically, so it gets its own lens rather
than being spread across A/D/E. **The discipline here is genuinely high.**
Uploads are atomic and verified — `write_body` (`sd_web.h:200-259`) pre-checks
free space with 64 KB of slack, writes a `.part` sidecar, unlinks-then-renames
(FAT will not overwrite) and returns a CRC32 the sender compares. OTA rollback
is confirmed on *proof of network*, not at boot (`castle.yaml:242-243`,
`flash_mode.h:38-52`: "Confirming at boot instead would happily bless a
brick"). Thread separation is declared at `sd_web.h:5-19` and then obeyed —
`sd_web_state.h` has no httpd types in it at all. Every bulk loop's
`vTaskDelay(1)` carries the crash that produced it. `CONFIG_LWIP_MAX_SOCKETS:
16` is spent explicitly, socket by socket, with the ENFILE incident recorded.
`max_uri_handlers = 32` carries the live-board bug where the last three
registrations failed silently. The emulator is held to the firmware by
*parsing* the firmware — `tests/test_firmware_contract.py:164-187` re-derives
`safe_name`'s byte rule out of the C and fuzzes 1,500 strings against it — and
`tests/cxx/render_check.cpp` is compiled with `-Wall -Wextra -Werror` so the
render path is actually executed by a host compiler. `firmware/castle_s3.yaml`
arrived with 431 lines of tests, a bring-up checklist, and two places where it
departs from its own spec written down rather than argued.

The grade is B because that discipline has three holes with teeth, and because
**nothing since v5.34 (2026-08-22) has been on hardware** — v5.42, v5.43, v5.44
and v5.45 are all "compiled, NOT yet flashed" (`firmware/pending/README.md`).
Fifteen days of firmware written against an emulator is a defensible choice
with the board offline; it is still fifteen days of unverified change, and the
one CI job that would at least *compile* it has never run green (D1).

#### J1 — `safe_name` accepts bytes ≥ 0x80, so one badly-named file makes `/api/status` and `/api/files` un-parseable — this is what has CI red
- **Where:** `firmware/sd_web_util.h:37-47` refuses control bytes, DEL, `"` and `\` but nothing above 0x7F, and `json_escape` (`sd_web_util.h:75-97`) passes bytes ≥ 0x20 straight through. `h_status` (`sd_web.h:103-127`) and `h_list` (`:178`) print names through it. A file named with a lone 0x80 therefore produces a JSON body that is not valid UTF-8 — `json.loads` raises, and every Python client of the castle breaks: `tools/sd_sync.py:52,80` (that is `make publish`) and `tools/castle_link.py:126` (the desk's device panel). Browsers see U+FFFD rather than an exception, so the desk degrades quietly and the toolchain hard-fails. This is exactly the outage class v5.24's `json_names` fix closed for quotes and control bytes, with the high half left open — and `tools/fuzz_corpus.py:64`'s `POISON` set was derived from the same half-rule. Live evidence: CI run 33592881896, `test_protocol_fuzz.TestStorm` ×2, `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x80 in position 205`.
- **Fix:** refuse bytes ≥ 0x80 in `safe_name` (one line in the C, one in `tools/castle_emu_wire.py:141`), which makes `h_list`'s existing skip-and-count path handle them and `h_put` answer 400. Extend `POISON` in the same commit and let `tests/test_firmware_contract.py`'s derived rule re-check itself. Bump the version string.
- **Effort:** S
- **Grade lift:** B → B+ (main goes green and the JSON contract stops depending on filenames)

#### J2 — The RMT budget never reserves the status pixel's block, so the documented ring-flicker fix overspends the S2 in silence
- **Where:** `tools/gen_rig.py:219-231` — `check_rmt_budget` sums only the generated zone strips against `S2.total` (256) and returns `chip.total - spent`. The fourth consumer is hand-written and uncounted: `firmware/castle_sd.yaml:83-92`, `status_pixel`, `rmt_symbols: 64`. Set the door to the `rmt_symbols: 128` that `docs/ISSUE-ring-flicker.md:90-100` prescribes and the generator emits, without error, a banner that states its own contradiction: *"RMT: 256 of 256 symbols spent, 0 block(s) spare (the SD build's status pixel needs one)"* (`gen_rig.py:341`). The build then asks 320 symbols of a peripheral with 256 — precisely the silent failure `gen_rig.py:150-158` exists to prevent ("strips past the limit get no channel and stay dark", bench-diagnosed 2026-08-19). The S3 path is correct only by accident: that build has no status pixel.
- **Fix:** give `check_rmt_budget` a `reserved_blocks` argument (1 for S2, 0 for S3) passed from `emit_lights`, so "0 spare" already accounts for the pixel and a fourth request is a `SystemExit`. Mirror `tests/test_firmware_s3.py:404` for the S2.
- **Effort:** S
- **Grade lift:** B → B+ (the one open hardware bug's prescribed fix stops being a trap)

#### J3 — The stream server — every note of audio in the show — is outside the firmware contract and the emulator
- **Where:** `firmware/sd_web_stream.h:23` sets `cfg.server_port = 8080`; the same number is spelled at `tools/gen_esphome_audio.py:59`, `firmware/castle_sd_common.yaml:174,206` and `firmware/sd_audio.h:10`. `tests/test_firmware_contract.py:35-40` parses `sd_web.h`, `_ota`, `_site`, `_remote`, `_state` and `_util` — **`sd_web_stream.h` is not in that list**, and the emulator models only port 80. `docs/PARITY.md`'s wire-protocol row does not name it either. Change that port and every scene goes silent with a fully green suite. Same class, one function over: `h_health` (`sd_web.h:133-142`) is the one response whose shape the emulator *invents* (`tools/castle_emu_http.py:214-217`, four hand-typed keys) — `test_status_keys_are_the_firmwares` (`:363`) derives `h_status`'s keys from the C and has no `h_health` equivalent.
- **Fix:** parse `cfg.server_port` out of `sd_web_stream.h` and assert the generated `set_media_url` and both `castle_sd_common.yaml` lambdas use it; add the `h_health` key comparison.
- **Effort:** S

#### J4 — Files under `scenes/` and `site/` can be uploaded but never deleted
- **Where:** `firmware/sd_web.h:426-429` registers PUT for `/api/files/*`, `/api/site/*` and `/api/scenes/*`, and DELETE only for `/api/files/*`. `h_delete` (`:289-298`) builds `/sd/<name>` and `safe_name` refuses any `/`, so nothing addressable reaches a subdirectory. A renamed scene strands its old mp3 — the two real songs are 2.2–2.3 MB each — until someone pulls the card.
- **Fix:** register DELETE on the two prefixes through the switch `h_put` already has, and update the "23 today" count at `sd_web.h:403` (`max_uri_handlers` is 32, so there is room).
- **Effort:** S–M

#### J5 — The OTA slot is a hardcoded S2 constant in two places, and the S3 build has no image guard at all
- **Where:** `tools/check_image.py:26` — `SLOT = 1_835_008`, "ESPHome's default layout on 4 MB flash", pinned by `tests/test_guards.py:123`; `tools/castle_emu_http.py:46` — `OTA_SLOT = 0x1C0000`. `Makefile:277` runs the guard for `castle-sd` only. `castle_s3.yaml:66` declares `flash_size: 8MB` and gets no slot guard anywhere. Worse for rehearsal: the emulator would 400 an S3 image over 1.75 MB as "implausible image size" while the real board compares against `part->size` (`sd_web_ota.h:31`) and would accept it — so the emulator-rehearsed OTA flow that `firmware/pending/README.md:25-34` relies on cannot rehearse the S3 at all. (Measured today for the S2: image 1,148,832 B of 1,835,008 B, **62.6%**, 686 KB spare.)
- **Fix:** key `SLOT`/`OTA_SLOT` off the target rather than the constant; add `check_image.py castle-s3` to the weekly job beside the S3 compile (D3 covers getting it into CI at all).
- **Effort:** M

#### J6 — S2-only prose compiles into the S3 image, and the contract's exemption list has already rotted
- **Where:** `firmware/flash_mode.h:8-11` states "There is no USB serial console (the ESP32-S2 has no USB Serial/JTAG peripheral)" and justifies the one-way download-mode button with "OTA is off … the embedded audio makes the binary too large for two app slots"; `firmware/castle_inputs.yaml:48-53` repeats it. Both are included by `castle_s3.yaml` through `castle.yaml:86-95,282` — and the S3 *does* have that peripheral (`castle_s3.yaml:76-78`), which is bring-up step 2 (`firmware/pending/README.md:52-53`), the exact moment someone reads the file. OTA has been the normal path since the flash build was retired (`castle.yaml:186-190`, `sd_web_ota.h`, `make ota`); the action these comments justify is irreversible by design (`flash_mode.h:18-21`) and takes the show off the air. Separately, `tests/test_firmware_contract.py:45-52`'s `HARDWARE_ONLY` allowlist contains `"opendir failed"`, a string that appears nowhere in `firmware/` (`h_list`'s actual message is `"no such directory"`, `sd_web.h:158`) — nothing asserts the allowlist entries still correspond to firmware strings, so a message renamed *into* that set would stop being mirrored silently.
- **Fix:** rewrite both comment blocks — OTA first, this button as the last resort when the network is gone — and gate the S2-console sentence on the variant. Add `assertLessEqual(HARDWARE_ONLY, {msg for _c, msg in all_fw})` to `test_no_emulator_error_string_is_invented`.
- **Effort:** S

---

## Grade movement vs 2026-09-01

| | Was | Now | Why |
|---|---|---|---|
| A | B+ | **A−** | the two-board split is the repo's cleanest seam; features/edition/bins closed; three stale seams left (A1–A3) |
| B | A− | **B+** | craft unchanged, but one request aborts the *default* server (B1) and two more fault paths are open |
| C | A− | A− | C1–C4 all verified closed; zero DOM offenders, zero `any`/`ts-ignore`; only small strays remain |
| D | B+ | **B** | skip discipline still exemplary, but the new S3 gate cannot fail (D1), two gates have no CI home, and the gate that found a real bug can only find it in CI |
| E | B+ | B+ | new code careful and `unsafe` audited; E1 is B1 seen from the network, E2 a known-solved race re-introduced |
| F | A− | **B+** | pins immaculate on macOS only (F1, proven in a CI log); two red PRs open; five unbound toolchain literals |
| G | B+ | **A−** | the deferred payoff shipped — Rust studio is default, the waveform clone is gone; what is left is one long lock |
| H | B+ | **B** | 2026-09-01's items all closed, then the biggest firmware change in the project's history left CLAUDE.md and the design record behind |
| I | A− | **B+** | tooling still first-rate; the working copy is three commits ahead of a red main, and three gates do not run where they are believed to |
| J | — | **B** | new category: high discipline, one live bug (J1), one silent overspend (J2), and four versions unflashed |
| **Overall** | **A−** | **B+** | the code is as good; the loop that proves it came apart — red main, unpushed firmware, a gate that cannot fail |
