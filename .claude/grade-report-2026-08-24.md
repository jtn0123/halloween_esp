# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware
**Audited:** 2026-08-24
**Stack:** Python 3.13 tools (stdlib HTTP studio server, generators, DSP/analysis) · TypeScript 7 / esbuild no-framework web app · ESPHome YAML + C++ headers on ESP32-S2 · unittest (795) + 15 node engine suites + Playwright e2e (135 tests / 20 specs) + host-compiled C++ parity + cross-language seeded fuzz

**Tree audited:** `db621c4` (origin/main, pulled during this audit — the local
checkout was 52 commits / ~21k changed lines behind when the run started).

**How this was graded.** Every claim below was verified by running the code, not
by reading commit messages. `make check` was executed (exit 0), coverage was
measured (83%), `tsc` was run under the declared TypeScript 7.0.2, and each
prior-report item was re-tested against the current tree rather than assumed.

**Reconciliation note (2026-08-24, later):** PR #8 merged after this
execution — a parallel session's UI/UX push plus ITS OWN report execution
(archived at `.claude/grade-report-2026-08-23.md`). The two sessions
independently made twin `castle_act.ts` / `sd_web_util.h` splits; the
branch's versions won, everything unique here was replayed on top.

**Prior report:** the 2026-08-16 audit (graded **B** overall, 36 items) is at
`.claude/grade-report-2026-08-16.md`. **31 of its 34 open items are fixed.**
IDs below are freshly numbered and do not correspond to that report's.

**Permanently accepted risk — do not re-raise.** The studio's missing
`Origin`/`Host` validation and the firmware's unauthenticated OTA/file
endpoints were accepted as won't-fix on 2026-08-16 (local device, private
network). They are excluded from the Security grade below and are not items.

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | A− | 3 |
| B | Backend Quality | B+ | 3 |
| C | Frontend Quality (code) | B+ | 2 |
| D | Testing & Reliability | A− | 3 |
| E | Security | B+ *(excl. accepted risk)* | 1 |
| F | Dependencies & Tech Currency | A− | 1 |
| G | Performance & Scalability | B+ | 2 |
| H | Documentation & Onboarding | A− | 1 |
| I | Developer Experience & Tooling | B+ | 2 |
| **Overall** | | **A−** | **18** |

**Top 5 highest-leverage fixes:** I1, A2, A1, E1, B1

This is the strongest the project has graded. The 52 commits since the last
audit closed the backend robustness gap, the documentation gap and the DevEx
gap more or less completely, and added a firmware test layer that did not
exist. What remains is small, specific and mostly hygiene.

The engineering culture continues to sit above the grade: comments explain
*why* rather than *what*, guards have their own tests because "a guard that
silently stops working reads as a pass", and the four-implementation parity
contract (`docs/PARITY.md`) is a genuinely unusual thing to have built and to
keep green.

**Scope note.** This audit assessed frontend *code* — module structure, typing,
state handling, DOM discipline. Visual design, layout and interaction polish
were not sampled and are not graded. Accessibility is no longer excluded (the
suite now has `web/test/e2e/a11y.spec.ts`, 5 tests) but was not audited beyond
noting that coverage exists.

---

## A — Architecture & Design — A−

The sharpest structural trap in the previous audit is gone: `/studio/…` and
`/api/…` are now separate namespaces with a documented one-release alias
(`tools/studio.py:120`, `docs/API.md`), so the two backends behind one bundle
no longer share route spellings. `studio.py` split cleanly into six modules
along a real seam (transport vs. meaning), `web/src/desk_mode.ts` replaced six
interacting module-level flags with a tested pure `transition()`, and
`web/src/dom.ts` gives the page a leaf-level typed accessor that cannot create
cycles. Held back by one live import cycle, a multi-megabyte generated artifact
tracked in git, and three files pressed against the 500-line cap.

#### ~~A1~~ ✓ done 2026-08-24 — Live value-import cycle between `device.ts` and `device_panel.ts`
- **Where:** `web/src/device.ts:34` (`import { DevicePanel } from "./device_panel.js"`), `web/src/device_panel.ts:26` (`import { castleAct } from "./device.js"`)
- **What's wrong:** A circular *value* import, not type-only. It survives today only because both sides are used lazily — `castleAct` is called inside click callbacks (`device_panel.ts:271` onward) and `DevicePanel` is constructed inside a function (`device.ts:239`). One refactor that moves either to module top-level evaluation gives a TDZ error or `undefined` at load. This is the same hazard class the 2026-08-14 audit found in `track_lights` ↔ `track_style` and fixed by extracting a leaf.
- **Fix:** Same remedy as last time. Extract `castleAct` (and anything else `device_panel` needs from `device`) into a leaf module — `castle_act.ts` — importing nothing from either. Both files import the leaf; `device.ts` keeps a re-export if call sites depend on the current name.
- **Effort:** S
- **Grade lift:** A− → A (removes the only live structural hazard in the TS half)

#### ~~A2~~ ✓ done 2026-08-24 — 2.17 MB of generated output is tracked and rewritten every `make preview`
- **Where:** `previewer/castle-cue-desk.html` (2,175,290 bytes, 67 revisions in history); `.git` is 17 MB, so this one file dominates the repo's growth
- **What's wrong:** It is generated by `tools/gen_previewer.py` from `scenes/scenes.yaml` plus rendered audio, and `make preview` rewrites it wholesale. Every preview run that gets committed stores another ~2 MB blob. It grew from 1.1 MB at the 2026-08-16 audit — the problem is compounding, not static. `tools/check_loc.py` already exempts it, which correctly identifies it as generated but does not stop it being versioned.
- **Fix:** Gitignore `previewer/castle-cue-desk.html` and build it in the one place that needs it committed — or, if the portable inlined build must stay downloadable, publish it as a release asset rather than a tracked file. The studio already serves a lean page (`tools/studio.py:169`) and does not need the inlined copy at runtime. Existing history can stay; the point is to stop adding to it.
- **Effort:** S
- **Grade lift:** A− → A− (repo hygiene; prevents a slow, irreversible bloat)

#### ~~A3~~ ✓ done 2026-08-24 — Three files sit within 8 lines of the 500-line cap
- **Where:** `firmware/sd_web.h` (498), `firmware/castle.yaml` (498), `tools/studio.py` (492)
- **What's wrong:** `tools/check_loc.py` reports these as "nearing the cap". At 498/500 the next two lines in either firmware file force a split under deadline pressure rather than along a designed seam — and both are the files most likely to change when hardware behaviour does.
- **Fix:** Split each now, on the seam that already exists. `sd_web.h` has five sibling headers (`sd_web_ota/site/state/stream/remote.h`) — the routing table is the obvious next extraction. `castle.yaml` splits at the substitutions/packages boundary. `studio.py` splits at `_get`/`_post` (see B1).
- **Effort:** M
- **Grade lift:** A− → A− (prevention; the cap has already forced three good splits, this keeps it that way)

---

## B — Backend Quality — B+

Every backend item from the previous audit is closed: `/api/tracks` no longer
re-decodes through ffmpeg (manifest cache at `tools/studio_tracks.py:158`, with
a contract test at `tests/test_studio_cache.py`), `send_range` reads only the
requested bytes, the duplicated three-generator rebuild is factored,
`X-Import-Opts` goes through a guarded parse, and scene YAML is schema-validated
(`tools/scene_schema.py`, 283 lines, 94% covered). The error boundary is
genuinely good — `BadRequest` turns client mistakes into 400s instead of dead
sockets, and `MAX_BODY` caps allocation. What is left is one long dispatch, one
residual full-file read, and thinner coverage on the media job paths.

#### ~~B1~~ ✓ done 2026-08-24 — Route dispatch is a 92-line linear `if` chain
- **Where:** `tools/studio.py:162-254` (`_get`, 92 lines, 22 `if` branches), `tools/studio.py:286-374` (`_post`, 88 lines, 19 branches)
- **What's wrong:** Every GET walks up to 22 string comparisons in source order, and adding a route means adding a branch to a method in a file already at 492 of 500 lines. The route *names* already exist as data (`STUDIO_ROUTES`, `studio.py:120`), so the structure is half-built: the set is declarative, the dispatch is not.
- **Fix:** Turn the chain into a table — `{(method, prefix): handler}` — resolved by longest-prefix match, with the handlers as small methods. This drops `studio.py` well under the cap (addressing part of A3) and makes the route list assertable in one test rather than by exercising each path.
- **Effort:** M
- **Grade lift:** B+ → A− (removes the growth pressure on the largest backend file)

#### ~~B2~~ ✓ done 2026-08-24 — `send_range` still buffers the whole file when no `Range` header is sent
- **Where:** `tools/studio_http.py:139-141`
- **What's wrong:** The range path was fixed — `fh.seek(lo); fh.read(hi + 1 - lo)` reads only what was asked for. But when the client sends no `Range`, `lo, hi = 0, total - 1` and that same read pulls the entire file into RAM before the first byte goes out. For a four-minute import that is a ~4 MB allocation per request, and the comment above it ("only the bytes asked for leave the disk") now overstates what the code does.
- **Fix:** Stream the response in chunks — `shutil.copyfileobj(fh, self.wfile, 64 * 1024)` after seeking, bounded by the requested length. Content-Length is already computed from `hi - lo`, so no header change is needed.
- **Effort:** S
- **Grade lift:** B+ → B+ (bounds worst-case memory; also the G-side of the same defect)

#### ~~B3~~ ✓ done 2026-08-24 — The importer truncates track ids mid-word and can leave a trailing underscore
- **Where:** `tools/import_track.py:374-376`
- **What's wrong:** `"".join(...).strip("_")[:32]` strips underscores *before* truncating, so a cut that lands on an underscore leaves one dangling. The scene currently in `scenes/scenes.yaml:294` shows both failure modes at once: `the_citizens_of_halloween___this` — cut mid-title, with the run of underscores from the separator preserved. That id is user-visible on the desk and is the filename on the SD card.
- **Fix:** Swap the order to `[:32].strip("_")`, and prefer cutting at the last `_` before the limit so the id ends on a whole word. One unit test in `tests/test_import.py` covering a title that truncates onto a separator.
- **Effort:** S
- **Grade lift:** B+ → B+ (small correctness fix in a user-visible identifier)

---

## C — Frontend Quality (code) — B+

The state problem the last audit led with is solved properly:
`web/src/desk_mode.ts` is a discriminated union with a pure `transition()`
function and its own test suite (`web/test/desk_mode.mjs`, 18 assertions), and
`main.ts` now holds one `mode` value plus two paint flags instead of six
interacting booleans. Typing is clean under TypeScript 7.0.2 with
`tsc --noEmit` passing. The remaining friction is that the shared DOM accessor
exists but has not displaced the raw lookups it was written to replace.

#### ~~C1~~ ✓ done 2026-08-24 — `dom.ts` exists but 74 raw DOM lookups still bypass it
- **Where:** 74 `getElementById`/`querySelector` calls across `web/src/`, concentrated in `device_panel.ts` (17), `import_opts.ts` (9), `device.ts` (8), `zone_designer.ts` (7), `rig_panel.ts` (7)
- **What's wrong:** `web/src/dom.ts` provides exactly the right three helpers — `el` (absence is normal), `req` (throws naming the id) and `val` — and its docstring explains why. But the count went *up* from 58 at the last audit to 74, so new code is not reaching for it. Each raw `getElementById` returns `HTMLElement | null` and gets cast or non-null-asserted at the call site, which is the `null.addEventListener` failure `req` was written to prevent.
- **Fix:** Mechanical migration, highest-count file first: `device_panel.ts`, then `import_opts.ts`, then `device.ts`. Each raw lookup becomes `el`, `req` or `val` by whether absence is legitimate. Then add a lint rule (an `eslint` `no-restricted-syntax`, or a grep assertion in the node suite alongside the other invariant checks) so the count cannot climb again.
- **Effort:** M
- **Grade lift:** B+ → A− (converts a written-down convention into an enforced one)

#### ◐ C2 — partially done 2026-08-24 — Three frontend files are within 32 lines of the cap
- **Where:** `web/src/waveform.ts` (472), `web/src/panels.ts` (468), `web/src/device.ts` (468)
- **What's wrong:** Same pressure as A3 on the TS side. `device.ts` is also one half of the A1 cycle, so it is carrying two structural problems at once.
- **Fix:** Split `device.ts` first — extracting `castleAct` per A1 removes lines and breaks the cycle in the same change. `waveform.ts` and `panels.ts` split at the render/interaction seam.
- **Effort:** M
- **Grade lift:** B+ → B+ (prevention; A1's fix does part of this for free)

---

## D — Testing & Reliability — A−

The strongest category, and the one that improved most. Measured this run:
**795 Python unit tests passing**, **83% coverage** of `tools/` against a **72%
gate enforced in CI**, 15 node engine suites, 135 Playwright tests across 20
specs, and — new since the last audit — the firmware C++ is actually tested.
`tests/cxx/render_check.cpp` compiles the real headers with a host compiler and
checks canary bytes around every zone buffer, finiteness at t=0, t=10⁷ s and
negative t, and the strike envelope's exact curve; `parity_dump.cpp` feeds
`web/test/firmware_parity.mjs` so the C++ and TypeScript effect maths are held
numerically identical. Add seeded cross-language fuzz, protocol fuzz and chaos
suites and the reliability story is close to complete. Zero TODO/FIXME markers
anywhere in `tools/`, `web/src/`, `firmware/*.h` or `tests/`; the only skips are
conditional on a missing toolchain.

#### ~~D1~~ ✓ done 2026-08-24 — `check_loc.py` is 53% covered despite having a dedicated test file
- **Where:** `tools/check_loc.py` (78 statements, 37 missed); tests at `tests/test_guards.py`
- **What's wrong:** `test_guards.py` opens by arguing that "a guard that silently stops working is worse than no guard, because it reads as a pass" — and then covers barely half of the guard. The untested half is the reporting and exemption-listing path (`--list`, `--exempt`, the "nearing the cap" output), which is exactly the part whose silent breakage would look like a pass. The neighbouring guard `check_image.py` is at 90%.
- **Fix:** Extend `TestLocCheck` to drive the CLI entry point over a temp tree: a file over the cap exits 1 and names it, `--exempt` lists reasons, the 450+ warning band fires. Roughly six assertions.
- **Effort:** S
- **Grade lift:** A− → A (closes the gap in the check that gates every other file)

#### ~~D2~~ ✓ done 2026-08-24 — The media job paths are the thinnest-covered real code
- **Where:** `tools/stems.py` (137 stmts, 55%), `tools/import_fetch.py` (34 stmts, 59%), `tools/codec_compare.py` (63 stmts, 62%), `tools/studio_media.py` (109 stmts, 69%)
- **What's wrong:** These are the long-running subprocess paths — Demucs splitting, yt-dlp fetching, codec A/B rendering — where failures are most likely (missing binary, non-zero exit, partial output) and least likely to be noticed, since they run as background jobs whose errors surface only as a stalled progress bar. The surrounding job machinery is well covered (`studio_jobs.py` at 99%); it is the tool-shelling leaves that are not.
- **Fix:** Mock at the `subprocess.run` boundary (as `tests/test_import.py` already does) and cover the failure branches: non-zero exit, empty stdout, missing output file, and the timeout path. Aim for the failure arms specifically — the success arms are already exercised end-to-end.
- **Effort:** M
- **Grade lift:** A− → A (covers where the real-world breakage lives)

#### ~~D3~~ ✓ done 2026-08-24 — Four modules report 0% coverage
- **Where:** `tools/gen_wiring_diagram.py` (175 stmts, 0%), `tools/gen_eink_font.py` (35, 0%), `tools/gen_qr.py` (26, 0%), `tools/fuzz_check.py` (33, 0%)
- **What's wrong:** Three are one-shot asset generators where the output is checked by eye, which is a defensible reason not to unit-test them — but `gen_wiring_diagram.py` is 175 statements and is now the largest untested module in the tree, and it silently rewrites a tracked documentation file. `fuzz_check.py` is different: it is part of the parity apparatus and being at 0% means the checker itself is unverified.
- **Fix:** For `gen_wiring_diagram.py`, one smoke test asserting the splice is idempotent and the SVG stays inside its viewBox (the geometry check is scriptable — it was run manually during this audit). For `fuzz_check.py`, assert it *fails* on a deliberately divergent pair, which is the only property that matters.
- **Effort:** S
- **Grade lift:** A− → A− (the parity checker being unverified is the part that matters)

---

## E — Security — B+ *(excluding the two permanently accepted items)*

Real, verified defences: `tools/netguard.py` refuses URL imports that resolve
to private, loopback or link-local addresses unless the caller is the studio's
own machine — closing the SSRF hole the last audit raised; `scrub()`
(`studio_http.py:21`) escapes control characters so a carriage return in a URL
cannot forge a log line; `MAX_BODY` caps allocation and drops the connection so
unread bytes are not parsed as the next request; explicit-id imports are
alphabet-checked after a path-traversal escape was found (`import_track.py:378`,
with the incident recorded in the comment); `safe_name`/`safe_subpath` guard the
emulator's file routes; there is no `shell=True` anywhere in `tools/` or
`tests/`. Protocol and relay fuzz suites exercise the wire format. Including the
two accepted risks the honest number is C+; excluding them, B+.

#### ~~E1~~ ✓ done 2026-08-24 — `docs/SECURITY.md` is cited by the code but does not exist
- **Where:** `tools/netguard.py:13` — "the accepted position is that the studio is a local-only tool (docs/SECURITY.md)"; no such file in `docs/`
- **What's wrong:** The module implementing the project's defence-in-depth boundary points at the document that defines the threat model, and that document was never written. The two permanently accepted risks — unvalidated `Origin`/`Host`, unauthenticated OTA — currently exist as a decision recorded only in a grade report under `.claude/`, which is not where a future reader will look. Without it, the next audit re-raises them (this one had to be told not to), and anyone deploying beyond a porch LAN has no statement of what was assumed.
- **Fix:** Write `docs/SECURITY.md`: the threat model in a paragraph (single-user, private network, physical access assumed), the two accepted risks with the date and reasoning, what `netguard.py` does and explicitly does not protect against, and the one condition that would invalidate the whole position — exposing the studio or the castle beyond the LAN. Link it from `README.md` and `CONTRIBUTING.md`. This is the only item in the category and it is documentation, which is the accurate picture.
- **Effort:** S
- **Grade lift:** B+ → A− (turns an undocumented decision into a stated, re-checkable one)

---

## F — Dependencies & Tech Currency — A−

Materially better than last audit. `requirements.lock` pins 113 packages and
`make lock` regenerates it; `esphome` is pinned to exactly the version CI
validates against, with the reasoning in the comment; TypeScript is on **7.0.2**
and esbuild on **0.28.2**, both current (verified: `npm outdated` shows the
declared versions *are* latest — my local `node_modules` was stale, not the
manifest). `requirements.txt` now names `aioesphomeapi` and `Pillow` directly
rather than relying on them arriving transitively through esphome, with a
comment explaining that a release dropping either used to break `device.py`
silently. `pip-audit` runs in CI with per-advisory ignores that are documented
by ID and reason in the Makefile.

#### ⚠ F1 — investigated 2026-08-24, blocked on a GitHub setting — Dependabot's runs are being cancelled at the 24-hour mark
- **Where:** GitHub Actions history — three Dependabot jobs (`pip`, `npm_and_yarn`, `github_actions`) each recorded `cancelled` after `24h0m3s` on 2026-08-22
- **What's wrong:** A 24-hour runtime ending in cancellation is a job that never started work, not one that did it slowly — most likely queued against an unavailable runner or a permissions/config problem. Whatever the cause, the practical effect is that automated dependency PRs are not arriving, so currency depends entirely on someone noticing. The manifests are current *today*, which is what makes this easy to miss.
- **Fix:** Open one of the cancelled runs to read why it queued, then fix the trigger — usually a `.github/dependabot.yml` schedule/target mismatch or missing permissions. Confirm by watching for the next scheduled run to complete rather than cancel.
- **Effort:** S
- **Grade lift:** A− → A (restores the automation that keeps this grade where it is)

---

## G — Performance & Scalability — B+

The expensive paths named in the last audit are all addressed and, in two
cases, tested: `/api/tracks` answers from the manifest instead of re-decoding
(`tests/test_studio_cache.py` asserts a second call does no audio work), the
served page is rewritten lean with scene audio moved to
`/studio/scene-audio/<id>` links (`tools/studio.py:169`, with an e2e test), the
frame loop skips entirely when nothing is playing and the tab is hidden
(`web/src/main.ts:411-418`, with a `settle` flag for one trailing paint), and
there is now a hardware-side budget: RMT symbols are allocated per zone and
checked, the audio partition budget is reported at render time ("46% used —
fits"), and one scene's 1,216 pulses expanding to 2,402 actions was capped to
the strongest 200 after it exhausted the castle's RAM.

#### ~~G1~~ ✓ done 2026-08-24 — See B2: a `Range`-less request still buffers the whole file
- **Where:** `tools/studio_http.py:139-141`
- **What's wrong:** The performance face of B2. Media elements almost always send `Range`, so this rarely fires in the desk — but `/studio/track/<id>` and `/studio/stem/<id>/<layer>` are also plain URLs a user can open directly, and the SD-sync path fetches without ranges.
- **Fix:** As B2 — `copyfileobj` with a 64 KB buffer. One change fixes both entries.
- **Effort:** S
- **Grade lift:** B+ → A− (bounds per-request memory under concurrency)

#### ~~G2~~ ✓ done 2026-08-24 — The on-disk portable page is 2.17 MB and doubling between audits
- **Where:** `previewer/castle-cue-desk.html`
- **What's wrong:** The lean-page rewrite means the *studio* never serves this, so runtime is fine. But it is what someone gets if they open the file directly or copy it to a phone, and it grew from 1.1 MB to 2.17 MB in eight days as scenes were added — it scales with the number of scenes times their audio length, with no ceiling.
- **Fix:** Give the inlined build the same budget treatment the firmware audio already gets: have `gen_previewer.py` print the size and fail (or warn loudly) past a threshold, so the growth is visible at generation time. Pairs naturally with A2.
- **Effort:** S
- **Grade lift:** B+ → B+ (makes an unbounded artifact bounded)

---

## H — Documentation & Onboarding — A−

Transformed since the last audit, which graded this C+. There is now a
`README.md` that covers the browser half and explains the pin choices with the
reasoning ("putting 800 kHz NeoPixel data on the SD card's chip select is not a
mistake you find quickly"), a `CONTRIBUTING.md` naming the exact commands and
the inner loop, `docs/API.md` giving the complete HTTP contract for all three
parties with route ownership by prefix, `docs/PARITY.md` explaining the
four-implementation contract and why a divergence there is the worst bug the
project can have, `docs/ROADMAP.md`, `docs/WIRING.md`, two `ISSUE-*.md`
working documents, and the design record split into five parts under
`docs/notes/`. Zero broken internal markdown links across all 22 documents.

#### ~~H1~~ ✓ done 2026-08-24 — The setup path documented in CONTRIBUTING does not survive a fresh clone
- **Where:** `CONTRIBUTING.md` ("Before handing work back. `make check` green"), against `Makefile:152` (`check: test lint`)
- **What's wrong:** `make check` has no `audio` prerequisite, but the node suites load rendered scene audio. On a fresh clone — or any pull that adds a scene, since `audio/*.mp3` is gitignored — `make check` fails with `rendered audio 09_….mp3 missing under audio/ (make audio)`. This was reproduced during this audit: `make check` failed on the freshly pulled tree, and passed (exit 0) immediately after `make audio` with no other change. The document tells a new contributor to run a command that cannot work yet, and the error names the fix only if you read to the end of a node suite's output.
- **Fix:** This is really I1's fix — add the prerequisite. Until then, one line in CONTRIBUTING under Setup: `make audio` once after `make setup`, and again after any pull that adds a scene.
- **Effort:** S
- **Grade lift:** A− → A (the last gap between the documented path and the working one)

---

## I — Developer Experience & Tooling — B+

Every DevEx item from the last audit is closed. `make setup` now finds Python
3.13 via `command -v` rather than a hardcoded Homebrew path, and installs the
commit hook (`git config core.hooksPath githooks`) that previously existed but
was never wired up. `PY` falls back to `python3` when `.venv` is absent, which
fixes the git-worktree breakage. The 51 duplicated `# noqa: E402` comments are
gone, replaced by one `per-file-ignores` entry in `pyproject.toml` with the
reason written once. mypy gained `check_untyped_defs`, `warn_return_any` and
`warn_unused_ignores`. `make test-fast` gives a real inner loop by excluding the
suites that exist to wait, and `make coverage` / `coverage-gate` / `audit` /
`lock` are all present and documented in `make help`.

#### ~~I1~~ ✓ done 2026-08-24 — `make check` is missing its `audio` prerequisite
- **Where:** `Makefile:152` — `check: test lint`, compared with `preview: audio` on line 55
- **What's wrong:** The project's primary gate cannot run from a clean tree. `make preview` correctly declares `audio` as a prerequisite; `check` does not, even though the node suites it runs read `audio/*.mp3` — which is gitignored, so it is absent on every fresh clone and stale after every pull that adds a scene. CI papers over this by running `python tools/render_audio.py` as an explicit step in both the `web` and `esphome` jobs, so the gap is invisible there and only bites locally. Verified this run: failed before `make audio`, exit 0 after.
- **Fix:** `check: audio test lint`. `make audio` is a no-op when the files are current, so the inner loop pays nothing. Do the same for `test-fast` if its excluded set still touches rendered audio. Then drop the workaround line from H1.
- **Effort:** S
- **Grade lift:** B+ → A− (makes the documented gate actually runnable; the single highest-value fix in this report)

#### ~~I2~~ ✓ done 2026-08-24 — `make audit` needs a `make setup` re-run that nothing prompts for
- **Where:** `requirements-dev.txt:7` (`pip-audit~=2.10`), `Makefile:114`
- **What's wrong:** `pip-audit` was added to `requirements-dev.txt` and `requirements.lock` upstream, but an existing `.venv` created before that does not have it — `make audit` then dies with `No module named pip_audit` rather than saying the venv is stale. Hit during this audit on a venv that was current eight days ago. The same applies to any future dev dependency.
- **Fix:** Have `make audit` (and `coverage`) check for the module first and print `run 'make setup' — .venv predates a dev dependency` instead of a traceback. A three-line guard, or a `setup`-freshness check comparing `requirements-dev.txt` mtime against `.venv/pyvenv.cfg`.
- **Effort:** S
- **Grade lift:** B+ → B+ (turns a confusing failure into an instruction)

---

## What closed since 2026-08-16

For the record, since this is the second consecutive re-audit. Verified fixed,
not assumed: `/api` vs `/studio` namespacing · typed API client · the
`main.ts` six-flag state problem (now `desk_mode.ts` + tests) · `/api/tracks`
ffmpeg re-decode · `send_range` whole-file read (range path) · duplicated
rebuild routes · raw `json.loads` on `X-Import-Opts` · scene YAML schema
validation · **no coverage measurement anywhere** (now 83% with a 72% CI gate)
· device-network layer untested · **3,036 lines of firmware C++ validated only
by config parse** (now host-compiled with invariant and parity harnesses) ·
noisy test output · SSRF on `/api/import` (now `netguard.py`) · no Python
lockfile · `make setup` naming a missing interpreter · TypeScript a major
version behind · frame loop running when idle · 1.1 MB page served eagerly ·
README omitting the browser half · the parity contract undocumented · no
contribution doc · `.venv/bin/*` hardcoding breaking worktrees · pre-commit
hook not installed · 51 identical `noqa` comments.

---

## Execution log — 2026-08-24 ("do all")

All 18 items were executed in one pass (G1 rode along with B2; H1 was
resolved by I1). Every change verified: 800+ Python unit tests, all node
engine suites (including the new dom-discipline check), `tsc --noEmit`
clean under TS 7.0.2, ruff + mypy clean, all four ESPHome variants
(`castle_flash`, `castle_sd`, `castle_sd_jewels`, `bench`) validate, and
the firmware contract test passes against the split headers.

**What moved where:**
- `tools/studio.py` (492 → ~230) → dispatch stays; handler bodies now in
  `tools/studio_routes.py`, which reaches shared state through the studio
  module at call time so `mock.patch.object(studio, …)` still lands.
- `firmware/sd_web.h` (498 → 407) → helpers in `firmware/sd_web_util.h`;
  the contract test reads both halves as one text.
- `firmware/castle.yaml` (498 → 405) → PIR + buttons in
  `firmware/castle_inputs.yaml`, merged back via `packages:`.
- `web/src/panels.ts` (468 → 355) → sheet builders + sound catalogue in
  `web/src/panels_sheet.ts` (re-exported, so importers are unchanged).
- `web/src/device.ts` (468 → 374) → `castleAct`/`toast`/`failReason` in the
  new leaf `web/src/castle_act.ts`; the A1 import cycle is gone.
- `previewer/castle-cue-desk.html` untracked + gitignored; CI's web job now
  builds it before the e2e suite.

**C2, the honest partial:** `waveform.ts` (472) was not split. Its body is
one closure-heavy `initWaveform` with shared mutable state across every
section; a split means a state-object refactor, not a seam. Deferred with
reasons rather than done badly.

**F1, root cause found — one checkbox to flip.** Every "Dependabot
Updates" run (9 of 9, and the 3 fresh ones a config push retriggered on
2026-08-24) queues for 24 h and is cancelled. The stuck jobs request runner
label **`dependabot`** — GitHub's marker for the *"Dependabot on
self-hosted runners"* mode — while the repo has **zero self-hosted
runners**, so nothing can ever serve them. (CI works because it asks for
`ubuntu-latest`.) The fix is the UI-only toggle: **Settings → Code security
and analysis → uncheck "Dependabot on self-hosted runners"** — check the
account level (github.com/settings/security_analysis) as well as the repo —
then re-trigger by editing `.github/dependabot.yml` or via Insights →
Dependency graph → Dependabot → "Check for updates". Done in-repo along the
way: schedules pinned to Monday and the retrigger push (`9f7073f`), which
is also how the label diagnosis was captured.

**New tests added along the way:** `tests/test_wiring_diagram.py` (splice
idempotence, viewBox containment), `tests/test_fuzz_check.py` (stdout
contract; norm() cannot hide a divergence), `tests/test_media_failures.py`
(13 failure-arm tests), truncation tests in `test_import_cli.py`, the
`TestLocCli` block in `test_guards.py`, and `web/test/dom_discipline.mjs`
wired into `npm test`.

Run `/grade-codebase rerun` for a fresh audit of the post-fix tree.
