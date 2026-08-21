# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware
**Audited:** 2026-08-21
**Stack:** Python 3.13/3.14 stdlib tools (studio HTTP server + castle relay, generators, import/DSP, emulator) · TypeScript/esbuild no-framework web app · ESPHome YAML + C++ headers on ESP32-S2 · unittest (613, incl. host-compiled firmware harness + contract/fuzz/chaos suites) · 14 node suites · Playwright e2e (135, incl. real studio+emulator bridge/remote specs)

**Scope note:** Unlike the 2026-08-16 report, UI/UX and accessibility ARE graded this time (the desk was polished in place; no rewrite is planned). The owner's accepted-risk position (local-only studio Origin/Host handling; firmware OTA/HTTP auth — LAN-only porch prop) is honoured and not counted against E. Previous report preserved at `.claude/grade-report-2026-08-16.md` (overall B, 36 items).

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | B+ | 6 |
| B | Backend Quality | B+ | 6 |
| C | Frontend Quality | B+ | 6 |
| D | Testing & Reliability | B+ | 6 |
| E | Security | B+ | 4 |
| F | Dependencies & Tech Currency | B | 4 |
| G | Performance & Scalability | B | 5 |
| H | Documentation & Onboarding | B+ | 5 |
| I | Developer Experience & Tooling | A− | 5 |
| **Overall** | | **B+** | **47** |

**Overall rationale:** nothing below B; Security and Testing both B+ with one real data-safety item left (D1); the remaining lifts are architectural seams (A1), a firmware write that isn't atomic (B1), toolchain agreement (F1/I1) and page delivery (G1). Since 08-16: overall B → B+ (A, B, C, D, E, F, G, H, I all up or held).

**Top 5 highest-leverage fixes:** ~~D1, A1, B1, F1, G1~~ — all five executed 2026-08-21 (25 of 47 items done; see strikethroughs). Re-grade with `/grade-codebase rerun`.

---

## A — Architecture & Design — B+

The layering is real and mostly acyclic: `studio.py` routes, `studio_http.py` bytes, `studio_tracks/_media/_scenes/_jobs` one concern each; `build_paths.py` is a clean sandbox seam every generator honours (`tools/build_paths.py:31-72`); `castle_emu_wire.py` is a byte-level port of `sd_web.h` held to the C by `tests/test_firmware_contract.py:1-45`, which is the best cross-layer contract in the repo; firmware splits (`sd_web_state.h` mailbox, `sd_web_site.h` bytes-out, `sd_web_stream.h` second httpd) follow genuine seams. What holds it at B+: the studio is now a transparent relay for every unclaimed `/api/*` (`tools/studio.py:230-231,260-261,352-353`) and `POST /api/scene` means "fire on castle" or "edit scenes.yaml" depending on query-vs-body (`studio.py:342-348`), so the A1 namespace trap from 08-16 got sharper, not fixed; the effect vocabulary exists in four copies tied by comment (`gen_previewer.py:60-64` says "must match gen_esphome" but gen_esphome keeps its own `EFFECT_IDS`, plus `effects.ts:136`, `castle_effects.h`); two devices.toml resolvers (`hosts.py` "one resolver for every tool" vs `castle_link.castle_hosts` `:69-99`); `api.ts` still imports its wire types from panels (`api.ts:22-23`) and three modules bypass the doorway with raw `fetch` (`track_send.ts:31,58,107`, `track_card.ts:167`); the 2.37 MB generated page is still tracked (55 revisions, pack 12.9 MiB).

#### ~~A1~~ ✓ done 2026-08-21 — Studio and castle share `/api/*` with different semantics, now via a catch-all relay
- **Where:** `tools/studio.py:230-231,260-261,342-353,361-363`; `web/src/api.ts:1-23`; `web/src/device.ts:1-30`
- **What's wrong:** Anything the studio does not own is forwarded to the castle verbatim, and `/api/scene` dispatches on `?s=` vs JSON body. A new studio route that collides with a future firmware route, or a typo'd path, silently becomes a castle call (or a 502).
- **Fix:** Move studio-owned authoring routes under `/studio/...` in `api.ts` (one file) and `studio.py`'s three routers, keep `/api/*` as pure relay, alias old paths for one release; add a test that `/api/<unknown>` 502s and `/studio/<unknown>` 404s.
- **Effort:** M
- **Grade lift:** B+ → A− (removes the one structural ambiguity everything else is built around)

#### A2 — Four copies of the effect vocabulary, synchronised by comment
- **Where:** `tools/gen_previewer.py:60-64`, `tools/gen_esphome.py:54-63,89-92`, `web/src/effects.ts:136`, `firmware/castle_effects.h`
- **What's wrong:** `gen_previewer` claims to match `gen_esphome` which has no `KNOWN_EFFECTS`; validation differs per generator (`SystemExit` vs skip). Adding an effect is a four-file edit with no single test that all four agree on the *name list*.
- **Fix:** One `tools/effect_vocab.py` (names+ids) imported by both generators; `tests/test_generator_parity.py` asserts it equals the names parsed from `effects.ts` and the enum in `castle_effects.h` (same technique as `test_firmware_contract.py`).
- **Effort:** S
- **Grade lift:** B+ → B+ (closes a drift path the parity fuzz only catches indirectly)

#### A3 — Two devices.toml resolvers
- **Where:** `tools/hosts.py:20-45`, `tools/castle_link.py:69-99`
- **What's wrong:** `hosts.py` documents itself as the one resolver; `castle_link` re-parses the file with a different precedence (fallbacks, last-good host), so CLI tools and the studio can disagree about which castle is "the" castle.
- **Fix:** Have `castle_link.castle_hosts()` call a new `hosts.candidates()` that returns the ordered list (env → entry host → fallbacks); `hosts.resolve` becomes `candidates()[0]`.
- **Effort:** S
- **Grade lift:** B+ → B+ (duplication removal)

#### ~~A4~~ ✓ done 2026-08-21 — Dead PSRAM audio path beside the streaming one
- **Where:** `firmware/sd_audio.h:1-24,214-341`, `firmware/castle_sd.yaml:209-225,356-362`
- **What's wrong:** `start_load` loads into PSRAM on a worker task but `take_ready()` is never polled anywhere (grep: only defined), so the "Play SD file" button loads a megabyte and plays nothing; the header comment still says "WHAT THIS IS NOT: streaming" while `sd_web_site.h:9-14` documents that streaming is how scenes play. ~130 lines of dead code in the file nearest the RAM wall.
- **Fix:** Delete `load/load_worker/start_load/take_ready/type_from_name`, point `play_sd` at `set_media_url("http://127.0.0.1:8080/sd/"+path)` like `h_play`, rewrite the header to describe mount + streaming.
- **Effort:** S
- **Grade lift:** B+ → B+ (removes the largest dead block and a misleading design doc)

#### A5 — `api.ts` is not the only doorway and takes its types from panels
- **Where:** `web/src/api.ts:22-23`; `web/src/track_send.ts:31,58,107`; `web/src/track_card.ts:167`
- **What's wrong:** Three raw `fetch` sites talk to studio/castle routes outside `api.ts`/`device.ts`, and the transport layer imports `TrackInfo`/`CodecRow` from the consumers.
- **Fix:** Move the two interfaces to `types.ts` (re-export from old homes); add `api.castleFiles()/castleStatus()/trackBytes()` and `api.cardFile()` wrappers and call them.
- **Effort:** S
- **Grade lift:** B+ → B+

#### A6 — Tracked 2.37 MB generated page (A3 from 08-16, still open)
- **Where:** `previewer/castle-cue-desk.html` (2,374,807 B; 1.87 MB base64 audio; 55 revisions); `tools/check_loc.py:33-37` exempts it as generated
- **What's wrong:** Every `make preview` commits another incompressible ~2 MB; the repo already treats it as a build artefact (`bp.PREVIEW_HTML`).
- **Fix:** gitignore it, have `make studio`/CI build it; if a portable single-file deliverable is wanted, publish it as a release asset.
- **Effort:** S
- **Grade lift:** B+ → B+

---

## B — Backend Quality — B+

Much of the 08-16 list landed and landed well: `/api/tracks` reads `dur/onsets` from the manifest keyed by byte size and writes back on a miss (`tools/studio_tracks.py:128-167`), `send_range` seeks (`studio_http.py:79-83`), bodies are capped (`:22-26,94-105`), `X-Import-Opts` goes through `json_body` (`studio.py:396-397`), ffmpeg encodes beside the target then `os.replace` (`import_track.py:180-202`), per-PID scratch dirs (`:355-368`), the manifest is flock-guarded and atomically replaced (`manifest.py:45-85`), `castle_link` distinguishes Unreachable/Stalled and refuses to replay a stalled PUT (`castle_link.py:160-250`), and the emulator reproduces httpd's 404/405/414 and `recv_wait_timeout` (`castle_emu_http.py:10-25`). Firmware: the mailbox is the right thread model (`sd_web_state.h:19-42`), every long loop yields (`sd_web.h:226,384`, `sd_web_site.h:84`), OTA uses sequential writes with the reason recorded. What stops A−: firmware uploads truncate the target in place, OTA leaks its handle on failure, two JSON builders can emit invalid JSON, the dead load path above, no scene schema validation (B5 still open), and `JobRunner` is not actually "one at a time".

#### ~~B1~~ ✓ done 2026-08-21 — Firmware PUT truncates the existing file before the upload is known good
- **Where:** `firmware/sd_web.h:205-240` (`fopen(path,"wb")` then `unlink` on failure); contrast `tools/import_track.py:180-202`
- **What's wrong:** Re-sending a track over a good copy: a mid-upload WiFi drop deletes the *previous* good file (`unlink(path)` at `:231`). The studio side was fixed for exactly this class in commit 3ccdd8b; the device side was not.
- **Fix:** Write to `path + ".part"`, `fclose`, then `rename(part, path)` (FATFS supports rename; `unlink(path)` first if it exists since FAT rename won't overwrite); on failure unlink `.part` only. Mirror in `castle_emu_http.h_put` and add a contract test.
- **Effort:** S
- **Grade lift:** B+ → A− (last non-atomic write in the pipeline)

#### ~~B2~~ ✓ done 2026-08-21 — `h_ota` never calls `esp_ota_abort` on a failed write
- **Where:** `firmware/sd_web.h:387-391`
- **What's wrong:** `if (!ok || esp_ota_end(ota) != ESP_OK)` short-circuits, so after a short body / bad magic / write error the handle is left open; also `castle_sd::g_quiesce` is only cleared on the error paths, never on a success that then fails to reboot.
- **Fix:** `if (!ok) { esp_ota_abort(ota); ... }` then a separate `esp_ota_end` check; clear `g_quiesce` before returning from every non-reboot path.
- **Effort:** S
- **Grade lift:** B+ → B+ (correctness on the highest-consequence handler)

#### ~~B3~~ ✓ done 2026-08-21 — `h_list` / `h_status` can emit truncated or unescaped JSON
- **Where:** `firmware/sd_web.h:146-165` (`buf[760]`, `missing` unbounded), `:193-197` (`item[200]`, `d_name` up to 255 bytes, no escaping; the emulator documents the quote bug at `castle_emu_http.py:13-16`)
- **What's wrong:** `snprintf` truncation is unchecked, so a long `missing` list or a long/quoted card filename (placed by the Mac, not by PUT) breaks the parse for every client.
- **Fix:** Add a tiny `json_escape(const std::string&)` and use `std::string` building (as `h_list` already does for `out`) rather than fixed `snprintf` buffers; skip entries that fail `safe_name` in `h_list` with a `"skipped":N` field; contract-test both.
- **Effort:** S
- **Grade lift:** B+ → B+

#### ~~B4~~ ✓ done 2026-08-21 — Scene splice still has no schema validation (B5 from 08-16)
- **Where:** `tools/studio_scenes.py:106-120`; vocab in `gen_esphome.py:54-92`
- **What's wrong:** Unknown effect, cue past `length`, missing `audio_file` splice cleanly and fail inside `render_audio`/`gen_esphome`, surfacing as `reason()`'s log tail (better than before thanks to the stop-at-first-failure rebuild, `:53-59`, but still a subprocess message not a field).
- **Fix:** `tools/scene_schema.py: validate(scene) -> list[str]` (effects from the shared vocab of A2, cue `t <= length`, required keys, ranges); call before `_write`, return 400 with the list; reuse in `gen_esphome`.
- **Effort:** M
- **Grade lift:** B+ → A−

#### B5 — `JobRunner` jobs bypass `_lock`; "one import at a time" is only a docstring
- **Where:** `tools/studio_jobs.py:52-73`, `tools/studio.py:73,277-279,288-291` vs `:301-302,412-413`
- **What's wrong:** `/api/import/async` and `/api/stems` spawn children without `_lock`, so an async import, a sync import, and a rebuild can run ffmpeg concurrently; correctness holds (flock'd manifest, per-PID dirs) but the CPU/serialisation story the lock promises does not.
- **Fix:** Either give `JobRunner` a `gate: threading.Lock` it acquires in `_run` (pass `_lock`), or fix the docstrings to say "background jobs are not serialised with sync encodes" — pick and test.
- **Effort:** S
- **Grade lift:** B+ → B+

#### ~~B6~~ ✓ done 2026-08-21 — `track_info` reloads `tracks.json` once per track
- **Where:** `tools/studio_tracks.py:106` (`mf.get` → `load()` per call), `tools/manifest.py:111-112`
- **What's wrong:** N file reads + JSON parses per `/api/tracks`; trivial today (2 tracks), linear at 23.
- **Fix:** `mf.load()` once in the route and pass `meta` into `track_info(p, meta)` (keep the old signature as a wrapper).
- **Effort:** S
- **Grade lift:** B+ → B+

---

## C — Frontend Quality — B+

The typing discipline is still the real thing: `web/tsconfig.json` keeps `strict` + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes`, and across 51 modules/10,719 lines there is no `any`, no `@ts-ignore`, and only 5 `as unknown` (each a browser-API shim, e.g. `track_drop.ts:51`); the 62 `!` assertions are almost all index reads in hot DSP loops (`onsets.ts:46-114`). Theming is done properly — a full light default on bare `:root` (`styles.css:14-32`), a `prefers-color-scheme` block, and `data-theme` overrides in both directions (`styles.css:37-68`) — with `:focus-visible` and `prefers-reduced-motion` handled (`styles.css:452-454`), and the UX work since the last report is verified by tests rather than asserted: `a11y.spec.ts:49-97` (every visible control named, Tab order, focus retention, no sideways overflow at three widths), `mobile.spec.ts:22-108` (44 px floors, 12 px caption floor), `kiosk.spec.ts` (the tablet can no longer operate the castle), toasts stack/dedupe (`device.ts:102-116`). What holds it at B+: the three old code items are all still open (six-flag audition state machine in `main.ts:52-315`, `.scene[data-i]` reach-arounds at `main.ts:182,278`, 80 raw DOM lookups and no `dom.ts`), the castle panel is built from innerHTML strings carrying 31 inline `style="…"` attributes (`device_panel.ts:116-136`), status messages have no live region, and three CSS tokens referenced in markup don't exist.

#### ~~C1~~ ✓ done 2026-08-21 — Toasts and status lines are never announced
- **Where:** `web/src/device.ts:89-116` (`#toasts` host created without `role`/`aria-live`), `web/src/device_chip.ts` masthead line; `previewer/template.html` has no `aria-live` anywhere.
- **What's wrong:** Every "castle not answering", "scene failed — …", "deleted x from the card" is visual-only; a screen-reader user gets the button press and nothing back. The a11y spec checks names and tab order but not announcements.
- **Fix:** Give the toast host `role="status" aria-live="polite"` (errors `role="alert"`), the masthead line `aria-live="polite"`; add one Playwright assertion that the host carries the attribute.
- **Effort:** S
- **Grade lift:** B+ → A− (closes the only a11y class the suite doesn't cover)

#### ~~C2~~ ✓ done 2026-08-21 — The castle panel is an innerHTML string with 31 inline `style=` attributes
- **Where:** `web/src/device_panel.ts:116-136` and throughout (31 `style="…"`), `style_lab.ts` (11 `.style.x =`), `track_rows.ts:52,92`.
- **What's wrong:** Chip and toasts were moved into `panels.css` tokens (ff58890), but the panel — the most-rendered surface in the dock — still hard-codes padding/flex/colour per render; theme and mobile rules can't reach it, and J3-4 remains open.
- **Fix:** Lift the panel markup into `.dp__*` classes in `panels.css`; keep `innerHTML` but with class names only. `castle_panel.spec.ts` + `bridge.spec.ts` catch regressions.
- **Effort:** M
- **Grade lift:** B+ → A− (the styling-discipline gap the judges named twice)

#### C3 — Audition/preview state is still six module-level flags
- **Where:** `web/src/main.ts:52` `audioMode`, `:111` `players`, `:170` `adopting`, `:301-302` `previewScene`/`sceneBeforeAudition`, `:315` `selectedTrack`.
- **What's wrong:** Unchanged from the last report's C1; the rewrite that was supposed to dissolve it didn't happen, and the desk was polished on top of it.
- **Fix:** `desk_mode.ts` with a discriminated union + `transition()`, unit-tested in `web/test/`; `main.ts` keeps one `let mode`.
- **Effort:** M
- **Grade lift:** B+ → A− (turns the least-safe file into a tested one)

#### ~~C4~~ ✓ done 2026-08-21 — Three referenced CSS tokens don't exist, so the fallback hex always wins
- **Where:** `web/src/track_rows.ts:52` (`var(--err,…)`, `var(--warn,…)`), `:92` (`var(--err,…)`), `previewer/panels.css:187` (`var(--bad,…)`); the palette defines `--alarm`, not `--err/--warn/--bad`.
- **What's wrong:** The "import failed" badge and stems error note render a fixed dark-theme red in light mode; the token indirection is decorative.
- **Fix:** Replace with `var(--alarm)`; add `--warn` to `styles.css` or use `--accent`.
- **Effort:** S
- **Grade lift:** B+ → B+ (correctness of the token system; small)

#### ~~C5~~ ✓ done 2026-08-21 — Row buttons, view selector and budget tabs sit under the 44 px floor at 375
- **Where:** `previewer/mobile.css:18` (34 px), `:23` (30 px), `:73,81` (40 px); `mobile.spec.ts:30,68` only asserts scenes/chip/selects/tabs.
- **What's wrong:** Judge B measured row actions at 40 px after the "44 px" commit; the spec doesn't cover `.trk__act`, `.viewsel`, `.budget__tabs`, so the gap is invisible.
- **Fix:** Raise to 44 and extend the spec's selector list.
- **Effort:** S
- **Grade lift:** B+ → B+ (mobile polish)

#### C6 — `main.ts` still drives panels by selector; no shared DOM accessor
- **Where:** `web/src/main.ts:182,278` (`.scene[data-i]`), 80 `getElementById`/`querySelector` calls across modules, `main.ts:324` cast-and-trim inline.
- **Fix:** `panels.selectScene(i)`; leaf `dom.ts` (`el`, `input`, `req`) migrated opportunistically.
- **Effort:** S
- **Grade lift:** B+ → B+ (coupling hygiene)

---

## D — Testing & Reliability — B+

613 Python tests run in 58 s (`make test`, measured), 76% line coverage of `tools/` (`make coverage`, measured: 4124 stmts / 983 missed), plus `tests/test_firmware_cxx.py` host-compiling `tests/cxx/{parity_dump,render_check}.cpp` with `-Wall -Wextra -Werror` against the real `firmware/*.h`, 14 node suites (`web/package.json` `test`), and 131 Playwright tests incl. `bridge.spec.ts`/`castle_fail.spec.ts` that spawn a real studio + emulator. Quality is high: `tests/test_studio_api.py` drives a live server on an ephemeral port, `tests/studio_case.py:92` patches `CASTLE_TRACKS`/`CASTLE_HOST`, `tests/test_guards.py:146` asserts every writer honours the sandbox, `tests/test_gen_fuzz.py:173` seeds its RNG, `web/playwright.config.ts:47` forces one worker on a fixed overridable port. CI gates lint/mypy/unit/LOC/tsc/node/e2e/`esphome config`×4 on every PR (`.github/workflows/ci.yml`); only pip-audit is `continue-on-error`. Still untested: `tools/device.py`, `tools/sd_sync.py`, `tools/hosts.py` at 0% (the on-device OTA write, real WiFi timing and on-board playback are hardware-only and honestly left out), and one test still writes into the real library.

#### ~~D1~~ ✓ done 2026-08-21 — One integration test writes into the real `tracks/` [BE]
- **Where:** `tests/test_import.py:289,305` — `cls.track = ROOT / "tracks" / "_test_integration.mp3"`, scene `audio_file: "tracks/_test_integration.mp3"`
- **What's wrong:** Every other test goes through `studio_case.py`/`test_import_cli.py:33`'s tempdir sandbox; this one converts a click track straight into the user's library (cleaned in `tearDownClass`, but a crash mid-class leaves it, and it errors when `CASTLE_TRACKS` is exported — reproduced: 3 errors with the env set). It is the exception `test_guards.py:146` exists to forbid.
- **Fix:** Render into `cls.tmp` and point `audio_file` at that absolute path (or patch `CASTLE_TRACKS` like the CLI tests do).
- **Effort:** S
- **Grade lift:** B+ → B+ (closes the last sandbox hole; protects real data)

#### ~~D2~~ ✓ done 2026-08-21 — The suite is not hermetic against the operator's own `CASTLE_*` env [BE]
- **Where:** `tests/test_studio_unit.py:58`, `tests/test_castle_emu.py` (`TestSceneSeeding`), `tests/test_studio_api.py` (`test_tracks_lists_files_and_scene_ids`) — read the repo's `scenes/scenes.yaml` via `build_paths`, which honours `CASTLE_SCENES`
- **What's wrong:** `CLAUDE.md:57` tells you to export `CASTLE_HOST`/`CASTLE_TRACKS` for the emulator workflow; run `make test` in that shell and 6 tests fail (measured). The guard is per-TestCase, not suite-wide.
- **Fix:** In `tests/__init__.py`/`helpers.py` clear or pin the three variables at import, then let each case set what it needs.
- **Effort:** S
- **Grade lift:** B+ → B+ (removes a real local-only false-red)

#### ~~D3~~ ✓ done 2026-08-21 — The device-network layer is still at 0% [BE]
- **Where:** `tools/device.py` (66 stmts), `tools/sd_sync.py` (131), `tools/hosts.py` (36) — no test imports them (coverage report)
- **What's wrong:** D2 from the 2026-08-16 report is open. `sd_sync.py`'s manifest diff (what to upload/skip/delete) is pure and is the code run in a hurry on the night.
- **Fix:** Fake-listing tests for the diff; `hosts.resolve()` table/env precedence is three asserts.
- **Effort:** M
- **Grade lift:** B+ → A− (the worst-failure-mode code gets covered)

#### ~~D4~~ ✓ done 2026-08-21 — Coverage is measured but never gated and not in CI [BE]
- **Where:** `Makefile` `coverage:` target; `ci.yml` python job has no coverage step
- **What's wrong:** 76% is now a known number; nothing stops it sliding.
- **Fix:** `coverage report --fail-under=72` in CI, raise as D3 lands.
- **Effort:** S
- **Grade lift:** B+ → B+ (turns information into a ratchet)

#### D5 — The C++ host harness silently skips where no compiler exists [both]
- **Where:** `tests/test_firmware_cxx.py:37-38,48` — `@unittest.skipIf(COMPILER is None, …)`
- **What's wrong:** Ubuntu runners have g++, so CI is fine today, but a runner image change would turn the only firmware-executing tests into a skip with a green tick.
- **Fix:** Make the skip an error when `CI` is set.
- **Effort:** S
- **Grade lift:** B+ → B+

#### D6 — Test output still prints WARNINGs on a green run [BE]
- **Where:** `tests/test_studio_cache.py`/manifest tests — three `WARNING: tracks.json was not valid JSON — moved to …corrupt-…` lines, `FAIL — over 97% of the slot.` (measured in the run log)
- **What's wrong:** D4 of the last report, still open.
- **Fix:** `redirect_stdout` and assert on the text.
- **Effort:** S
- **Grade lift:** B+ → B+

---

## E — Security — B+ (outside the accepted Origin/Host + OTA-auth position)

Input handling is consistently right: `studio_http.py:26,94-119` caps bodies at 512 MiB and turns bad JSON into 400s; every id reaches disk through `safe_id` (`studio.py:124`) or `Path(...).name` (`studio.py:165,190,221,237,311`; `studio_http.py:151` for multipart filenames); `X-Import-Opts` now goes through `json_body` (`studio.py:396`); subprocess calls are argv lists with fixed flags (`studio.py:106-116` `opt_args`, `import_track.py:73,189`), never a shell; the relay only ever dials `castle_hosts()` (`castle_link.py:218`); `firmware/secrets.yaml` is ignored (`.gitignore:9`) with a tracked `.example`; `sd_web.h:79-89` `safe_name` rejects slashes/`..`/dotfiles/quotes/control bytes and `query_param` uses bounded stack buffers (`:111-114`). `npm audit` is clean; pip-audit shows cryptography 49.0.0 PYSEC-2026-3552 (clears with the esphome bump) and starlette (build-toolchain only, ignored by id in `Makefile`). `--lan` is documented as "do that only on a network you control" (`studio.py:23-24`, `README.md:103`). No PII.

#### ~~E1~~ ✓ done 2026-08-21 — `/api/card/<name>` relays the raw suffix, not a filename [BE]
- **Where:** `tools/studio.py:227-228` — `to="/sd/" + path[len("/api/card/"):]`
- **What's wrong:** The one relay that builds a castle path does not `Path(...).name` it, so `/api/card/../api/status` (sent `--path-as-is`) reaches any GET on the castle. Only the configured hosts, GET only — low — but it is the single route off the guard pattern.
- **Fix:** `name = Path(path).name; if not name: 400`.
- **Effort:** S
- **Grade lift:** B+ → A− (makes the guard uniform)

#### E2 — Import values that start with `-` reach argparse as flags [BE]
- **Where:** `tools/studio.py:109-111` (`opt_args`), `tools/import_track.py:250,257-258`
- **What's wrong:** `notes: "--force"` or `start: "-x"` becomes an option token; the worst outcome is a 500 from argparse, not execution, but it is an unvalidated edge.
- **Fix:** Validate `start`/`take` as numbers/`m:ss` server-side and pass `--notes=<v>` with `=`.
- **Effort:** S
- **Grade lift:** B+ → B+

#### E3 — URL import will fetch any http(s) target, including LAN/loopback [BE]
- **Where:** `tools/studio.py:385-387`, `:329` (`/api/probe`)
- **What's wrong:** E3 from the prior report remains: yt-dlp/ffmpeg will happily probe `http://192.168.1.1/…` on the host's behalf. Defence-in-depth only given the accepted position.
- **Fix:** Resolve the host and refuse private/loopback ranges unless `--lan` is off and the caller is 127.0.0.1.
- **Effort:** S
- **Grade lift:** B+ → B+

#### E4 — Upload filename `..` survives `Path().name` [BE]
- **Where:** `tools/studio_http.py:151` → `tools/studio.py:400` (`tmp / (fname or "upload.bin")`)
- **What's wrong:** `Path("..").name == ".."`, so the staging path is `_upload/..` (= `TRACKS`), and `write_bytes` fails with a 500 rather than a 400. No escape, just an ungraceful edge.
- **Fix:** Reject names in `{"", ".", ".."}` in `parse_multipart`.
- **Effort:** S
- **Grade lift:** B+ → B+

---

## F — Dependencies & Tech Currency — B

`requirements.txt` explains why each direct dep is named; `requirements.lock` (112 pins) exists with a `make lock`/`make audit` pair; Dependabot covers pip/npm/actions weekly with assignees (`.github/dependabot.yml`); `npm audit` is clean. But the lock is not what CI installs (`ci.yml:43-44,78` install loose `numpy scipy pyyaml yt-dlp` + `requirements-dev.txt`), the local `.venv` is Python 3.14.6 while `pyproject.toml`, `ci.yml` and `Makefile` `PY_SETUP` all say 3.13 — so "the lock says exactly what was tested" (`Makefile` comment) is only true on 3.14 locally. esphome 2026.7.4 vs 2026.8.0, aioesphomeapi 45.7 vs 45.13.1, TypeScript 5.9.3 vs 7.0.2, esbuild 0.25.12 vs 0.28.2 (`npm outdated`). The esphome hold is at least a documented decision: `firmware/castle.yaml:148-158` records that dram0 has ~20 bytes spare and the next framework bump "will hit the wall again".

#### ~~F1~~ ✓ done 2026-08-21 — The venv, the lock and CI disagree on the interpreter
- **Where:** `.venv/bin/python → python3.14` (3.14.6); `pyproject.toml` `target-version = "py313"`, `python_version = "3.13"`; `Makefile` `PY_SETUP` = python3.13; `ci.yml:37,69,98` "3.13"
- **What's wrong:** `requirements.lock` was frozen from 3.14; CI never installs it. Two toolchains, neither fully exercised by the other.
- **Fix:** Recreate `.venv` with `make setup` (python3.13 is on PATH at `/Library/Frameworks/…/3.13`), refreeze, and `pip install -r requirements.lock` in the python job.
- **Effort:** S
- **Grade lift:** B → B+ (the lock becomes what CI tests)

#### F2 — esphome 2026.7.4 → 2026.8.0 (clears cryptography 49 advisory)
- **Where:** `requirements.txt:10`, `ci.yml:104`, `requirements.lock` cryptography==49.0.0
- **What's wrong:** One known advisory in the venv is fixable by the pin bump; the S2 static-RAM cliff (`castle.yaml:148-158`) is the honest reason it's held, so this is a decision with a rebuild, not neglect.
- **Fix:** Bump on a branch, `make validate` ×4, compile once, check link margin; if dram0 overflows, apply the "next levers" the YAML already lists.
- **Effort:** M
- **Grade lift:** B → B+

#### F3 — esbuild three minors and TypeScript a major behind
- **Where:** `web/package.json` devDependencies (`^0.25.0`, `^5.7.0`)
- **What's wrong:** F2 from the prior report; unchanged. No CVEs, but the gap widens.
- **Fix:** esbuild first (`npm test` + e2e), TS 7 on its own branch.
- **Effort:** S / M
- **Grade lift:** B → B+

#### F4 — `aioesphomeapi~=45.7` six patch releases behind; dependabot PRs not landing
- **Where:** `requirements.txt:12`, `requirements.lock`
- **What's wrong:** Dependabot is configured but the floor hasn't moved since the last report — check the open-PR queue.
- **Fix:** Merge/close the queue; consider monthly for pip.
- **Effort:** S
- **Grade lift:** B → B

---

## G — Performance & Scalability — B

Measured against a sandboxed studio (port 8877, copied tracks): `/api/tracks` 1.23 s cold (manifest fields wiped, 2 tracks) then **19 ms** warm — the 08-16 multi-second wait is gone (`studio_tracks.py:128-147`); `/api/waveform` 0.77 s cold, **1.5 ms** cached (`studio_media.py:133-161`); `/api/status` 0.4 ms with the 1.5 s castle TTL (`castle_link.py:58-62`). Unit suite: 613 tests in 59.4 s (3.3× 08-16's 18 s for 1.5× the tests; the chaos suite's 18 s poll bug was fixed). Firmware constraints are handled with care: second httpd for streaming so the control plane never queues behind a song (`sd_web_stream.h:1-10`), yields per chunk everywhere, card-space cached 60 s (`sd_web.h:119-135`), dram0 at ~20 bytes spare with the next levers written down (`castle.yaml:148-158`). Remaining costs: the page is 2.37 MB served with `Cache-Control: no-store` and no gzip (1.45 MB gzipped; the firmware already serves `.gz`, the studio does not), the waveform cache keys on sensitivity so every knob nudge re-decodes (0.74 s measured), and the frame loop still does full work while stopped.

#### ~~G1~~ ✓ done 2026-08-21 — Studio serves the 2.37 MB page uncompressed and uncacheable
- **Where:** `tools/studio.py:155-158`, `tools/studio_http.py:45-51`; `firmware/sd_web_site.h:130-137` (does it right)
- **What's wrong:** Every load/restart on the LAN path sends 2.37 MB; `gzip` would be 1.45 MB and an ETag on the file mtime would make reloads 304s.
- **Fix:** In `send_bytes`, if `Accept-Encoding` has gzip, send a gzip of the page cached in-process by `(path, mtime)`; add `ETag: "<mtime>-<size>"` and honour `If-None-Match`. Keep `no-store` for API JSON.
- **Effort:** S
- **Grade lift:** B → B+

#### ~~G2~~ ✓ done 2026-08-21 — Waveform cache re-decodes on every sensitivity change
- **Where:** `tools/studio_media.py:151-152,164-193`
- **What's wrong:** The key includes `sensitivity`, but decode + peaks + `envelope` (~70% of the 0.77 s) do not depend on it; only `analyze_full` does.
- **Fix:** Two-level cache: `_DECODED[(path, mtime)] -> (x, stereo, peaks, env)` (bounded, e.g. 8) and the existing `_WAVES` on top; `_waveform` takes the decoded tuple.
- **Effort:** S
- **Grade lift:** B → B+ (turns a 0.74 s nudge into ~0.2 s)

#### ~~G3~~ ✓ done 2026-08-21 — Frame loop runs at full cost when stopped (G2 from 08-16, still open)
- **Where:** `web/src/main.ts:371-390`
- **What's wrong:** `stage.draw/insets.draw/updateMeters/lightChrome/wave.mirror/updateTransport` every rAF regardless of `state.running`, audition, or `document.hidden`.
- **Fix:** Early-out after `step()` when `!state.running && !sceneBeforeAudition && f.flash === 0` unless a `dirty` flag was set by slider/scene change; skip entirely while `document.hidden`.
- **Effort:** S
- **Grade lift:** B → B

#### G4 — 1.87 MB of base64 audio inlined for nine scenes
- **Where:** `tools/gen_previewer.py:270-276`; `previewer/castle-cue-desk.html`
- **What's wrong:** 79% of the page is audio the LAN phone may never play; the "portable single file" property is sound for the file artefact but not for the studio-served case.
- **Fix:** `gen_previewer --lean` (or studio-side strip) that replaces data URIs with `/api/scene-audio/<id>` URLs served by `send_range`; keep inlining for the committed/portable build.
- **Effort:** M
- **Grade lift:** B → B+ (only matters for `--lan`)

#### G5 — Unit suite 59 s for 613 tests
- **Where:** `Makefile:103-104`; heavy files `tests/test_studio_api.py` (real server per class), `tests/test_castle_chaos.py`
- **What's wrong:** 3.3× slower than 08-16 at 1.5× the tests; the pre-commit hook runs lint only, so this is the `make check` floor.
- **Fix:** Run `python -m unittest -v 2>&1 | sort by duration` once, move real-server classes to `setUpClass` servers where they are per-test, and add `make test-fast` excluding chaos/e2e-ish suites for the inner loop.
- **Effort:** S
- **Grade lift:** B → B

---

## H — Documentation & Onboarding — B+

The two biggest prior gaps are closed and accurate: `README.md:92-127` now has "The cue desk" and "Development" (commands verified against `Makefile`), `make help` lists every target including `coverage`/`audit`/`lock` (checked against `.PHONY`), `firmware/secrets.yaml.example` makes the "copy" step real, `make setup` resolves python3.13 on PATH (present on this machine), and the root `CLAUDE.md` (93 lines) captures layout, rules, sandboxing, hardware traps and the accepted-risk position. `PROJECT_NOTES.md` indexes `docs/notes/01-05` with section ranges, `05-decisions-and-roadmap.md` is an ADR-style log, `firmware/pending/README.md` records applied-vs-pending firmware work, `docs/WIRING.md` + generated `castle-wiring.html` cover the rig, and header comments remain exceptional (`castle.yaml:120-160`, `sd_web.h:76-89`, `studio_http.py:94-119`). What's missing is a single truthful route list and the parity contract write-up.

#### ~~H1~~ ✓ done 2026-08-21 — The studio docstring lists 8 of ~22 routes
- **Where:** `tools/studio.py:11-20` vs handlers at `:164-352` (`/api/job/`, `/api/status`, `/api/waveform/`, `/api/stems`, `/api/stem/`, `/api/compare`, `/api/refresh`, `/api/probe`, `/api/import/async`, `/api/server/stop|restart`, PUT, and the relay fallthrough are absent)
- **What's wrong:** This docstring is the only prose "API doc" for the studio; the firmware side has `sd_web.h`'s registration table but nothing maps desk → studio → castle in one place.
- **Fix:** Rewrite the docstring as the full table (method, path, claims-or-relays), or a 40-line `docs/API.md` linked from README/CLAUDE.md.
- **Effort:** S
- **Grade lift:** B+ → A− (one place that tells the truth about the contract)

#### ~~H2~~ ✓ done 2026-08-21 — The parity contract still has no page of its own
- **Where:** `CLAUDE.md:73-75` (two lines); mechanism spans `tools/pulse_dynamics.py`, `web/src/effects.ts`, `firmware/castle_effects.h`, `tests/test_generator_parity.py`, `tests/cxx/parity_dump.cpp`, `web/test/fuzz_parity.mjs`, `firmware_parity.mjs`
- **What's wrong:** H3 from the prior report; now four implementations (incl. the C++ host dump) kept bit-exact by seeded fuzz — the repo's best idea, still undocumented as a whole.
- **Fix:** `docs/PARITY.md`: what, why, how to run, what to do when it fails.
- **Effort:** S
- **Grade lift:** B+ → A−

#### H3 — CLAUDE.md's e2e count is stale
- **Where:** `CLAUDE.md:47` "(108 tests)" vs 131 `test(` in `web/test/e2e/*.spec.ts`
- **What's wrong:** Hard-coded counts rot; it already has.
- **Fix:** Drop the number or say "`npx playwright test --list`".
- **Effort:** S
- **Grade lift:** B+ → B+

#### H4 — `--lan` exposure is stated but not enumerated
- **Where:** `README.md:103`, `tools/studio.py:22-24`
- **What's wrong:** "Do that only on a network you control" is right, but a reader can't tell that a LAN visitor can import/delete tracks, rewrite `scenes.yaml`, and `POST /api/server/stop`. Acceptable for the porch; should be one explicit sentence.
- **Fix:** Add the sentence to README "The cue desk" and the docstring.
- **Effort:** S
- **Grade lift:** B+ → B+

#### H5 — No CONTRIBUTING / hook-install step in the onboarding path
- **Where:** `README.md:123-125` mentions the hook; no `CONTRIBUTING.md`; `make setup` does not run `git config core.hooksPath githooks`
- **What's wrong:** Minor for a one-owner repo; the dev section covers most of it.
- **Fix:** Add the hooksPath line to `make setup`.
- **Effort:** S
- **Grade lift:** B+ → B+

---

## I — Developer Experience & Tooling — A−

The loop is fast and the gates are real: `tsc --noEmit` 1.0 s, esbuild 13 ms, `npm test` 2.5 s, the pre-commit hook 0.35 s, Python unit suite 613 tests/58 s, e2e 135 tests in 18 files on a fixed `CASTLE_E2E_PORT` with `CASTLE_TRACKS`/`CASTLE_SCENES`/`CASTLE_HOST=""` sandboxing (`web/playwright.config.ts:28-40,80`). Three of the four old items are fixed — `Makefile:4` falls back when `.venv` is absent, `tools/studio.py:71` uses `sys.executable`, `make coverage`/`make audit`/`make lock` exist, `pyproject.toml:30-33` replaced 78 noqa lines — `make help` matches the targets, CI has concurrency cancellation and explains its timeouts (`ci.yml:17-31`), and the emulator workflow is in the README (`README.md:106-110`). Left: the hook, `make lint`, the Playwright webServer and the `.command` still hardcode `.venv/bin/*`, `make setup` still doesn't install the hook, and the venv/lock are 3.14 while every config says 3.13.

#### ~~I1~~ ✓ done 2026-08-21 — The local venv is Python 3.14 but pyproject/CI/mypy/setup all say 3.13
- **Where:** `.venv/bin/python` → 3.14.6 (verified); `Makefile:11` `PY_SETUP` resolves `python3.13` (present at `/Library/Frameworks/…/3.13`); `pyproject.toml:6,34`; `ci.yml:36,66`; `requirements.lock` frozen via `make lock` from that venv.
- **What's wrong:** Tests and `make lock` run on an interpreter CI never uses; the lockfile `make audit` checks in CI was produced from a different Python, and mypy type-checks against a `python_version` it isn't running.
- **Fix:** `rm -rf .venv && make setup` (now works since python3.13 is on PATH), re-run `make lock`; add a `setup` guard that prints the venv's version vs `pyproject`.
- **Effort:** S
- **Grade lift:** A− → A (makes local == CI)

#### ~~I2~~ ✓ done 2026-08-21 — Four places still hardcode `.venv/bin/*`, and `setup` still doesn't install the hook
- **Where:** `githooks/pre-commit:6-8`, `Makefile:131-132` (`lint`), `web/playwright.config.ts:80` (`../.venv/bin/python`), `Castle Cue Desk.command:26-36`; `Makefile:48-52` `setup` has no `git config core.hooksPath githooks`.
- **What's wrong:** `PY` has a fallback but `lint` and the hook bypass it; in a worktree `make lint` and the hook fail on the first line. The hook is installed here only because `core.hooksPath` was set by hand (pointing at the *main* checkout's `githooks/`, so a worktree edit to the hook is silently not what runs).
- **Fix:** `lint: @$(PY) -m ruff …; @$(PY) -m mypy …`; hook uses `PY=$(command -v .venv/bin/python || command -v python3)`; Playwright `command` reads `process.env.CASTLE_PY ?? "../.venv/bin/python"`; `setup` runs `git config core.hooksPath githooks`.
- **Effort:** S
- **Grade lift:** A− → A (worktrees are the daily workflow here)

#### I3 — mypy runs without any strictness flags
- **Where:** `pyproject.toml:32-40` — `files` + `ignore_missing_imports` overrides only; no `strict`, `disallow_untyped_defs`, `warn_return_any`, `warn_unused_ignores`.
- **What's wrong:** The comment says it brings tools/tests "up to the same standard" as tsc, but default mypy skips bodies of unannotated functions, so a large part of `tools/` is effectively unchecked.
- **Fix:** Turn on `warn_unused_ignores`, `warn_return_any`, `check_untyped_defs` first (cheap), then `disallow_untyped_defs` per module via overrides.
- **Effort:** M
- **Grade lift:** A− → A− (real gate strength; ungraded surprise risk)

#### ~~I4~~ ✓ done 2026-08-21 — A stray WARNING prints after the unit-suite summary
- **Where:** `.venv/bin/python -m unittest discover -s tests -q` → `OK` followed by `WARNING: could not parse …/no/such.yaml` (a test exercising the missing-file path lets the module logger through).
- **Fix:** `assertLogs`/`self.assertWarns` around that call, or silence the logger in `tests/studio_case.py`.
- **Effort:** S
- **Grade lift:** A− → A− (keeps the one output you read clean; old D4)

#### I5 — 14 `waitForTimeout` sleeps in the e2e suite
- **Where:** `grep -c waitForTimeout web/test/e2e/*.spec.ts` = 14; `bridge.spec.ts` fixes ports 8797/8798 with no collision check (J3-5).
- **What's wrong:** Timed sleeps are where a 2-minute suite grows flaky on a slower CI runner; the two extra fixed ports can collide with a second lane.
- **Fix:** Replace sleeps with `expect.poll`/`waitForFunction` on the observable; derive bridge ports from `CASTLE_E2E_PORT+1/+2`.
- **Effort:** S
- **Grade lift:** A− → A− (flake insurance)
