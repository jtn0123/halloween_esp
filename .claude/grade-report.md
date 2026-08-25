# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware
**Audited:** 2026-08-23
**Stack:** Python 3.13 stdlib tools (studio server, relay, generators, DSP, emulator) · TypeScript/esbuild no-framework web app · ESPHome YAML + C++ headers on ESP32-S2 · 791 unittest · 136 Playwright e2e · 14 node suites

**Scope note:** UI/UX weighted heavily this pass at the owner's request — C and G were sampled in a live browser (studio on :8765, real timings below), not just read. The accepted-risk position (studio Origin/Host; firmware OTA/`/api/files` auth — LAN-only porch prop, one operator) is honoured and never counted against E. Previous reports: `.claude/grade-report-2026-08-16.md` (B, 36 items), and the 08-21 run this file replaces (B+, 47 items, 25 executed).

**File-size note:** this report is a tracked `.md` and so lives under the repo's own 500-line cap. Items are terse by necessity.

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | A− | 6 |
| B | Backend Quality | B+ | 5 |
| C | Frontend Quality | B | 6 |
| D | Testing & Reliability | A− | 5 |
| E | Security | B+ | 4 |
| F | Dependencies & Tech Currency | B | 5 |
| G | Performance & Scalability | B− | 6 |
| H | Documentation & Onboarding | A− | 5 |
| I | Developer Experience & Tooling | A− | 5 |
| **Overall** | | **B+** | **47** |

**Overall rationale:** the engineering discipline here is genuinely unusual — a firmware↔emulator contract test that parses the C, frame-exact browser/firmware parity, a real accessible-name audit in e2e, zero TODO markers in 20k lines, atomic writes on a microcontroller. Nothing is below B−. What holds it at B+ rather than A− is that two hard ceilings are now close enough to touch (G1, G2: ~2 more scenes before dram0 runs out, a page that grows 1.2 MB per song) and the authoring pipeline has no publish stage, so the desk and the board can silently disagree about what the show *is* (A1) — the bug debugged on 08-22.

**Top 5 highest-leverage fixes:** A1, G1, C1, G2, B1

**Execution pass, 2026-08-23:** all 47 items addressed — 41 done, two (A2/G2) resolved as a documented 12-scene ceiling with a CI alarm (the flat-RAM cue format stays future work), G6 done in code pending a bench watchdog check, I5 partial (.editorconfig; whole-tree reformat deliberately declined), F2 a false finding (requirements.lock already pins the dev tools). Firmware v5.42 is compiled but NOT flashed — the castle was offline; see firmware/pending/README.md for the one OTA that finishes G1/B1/C6.

---

## A — Architecture & Design — A−

The seams are real and enforced, not aspirational: `build_paths.py` is a sandbox boundary every generator honours; `castle_emu_wire.py` is a byte-level port of `sd_web.h` held to the C by `tests/test_firmware_contract.py`; the studio splits cleanly (`studio_http` bytes, `studio_tracks/_media/_scenes/_jobs` one concern each); firmware splits follow genuine seams (`sd_web_state.h` mailbox, `sd_web_stream.h` second httpd). The 500-line cap has forced honest splits rather than cosmetic ones (`firmware/castle_audio.yaml` came out of `castle.yaml` on the audio seam this week). What holds it below A: the authoring pipeline stops at the Mac with no publish stage (A1), scenes are a compile-time construct with a measurable RAM price (A2), eight files are now pressed against the line cap (A3), and the device modules bypass their own API doorway (A4).

**In plain terms:** the pieces are well separated and there are automated checks that stop two halves of the system from drifting apart. The gap is the last mile — nothing carries a change from your laptop out to the castle, so the two can quietly disagree.

#### A1 — The scene pipeline has no publish stage
- **Status:** ✓ done 2026-08-23 — rebuild() now publishes (studio_publish.py), plus POST /studio/publish and `make publish`
- **Where:** `tools/studio_scenes.py:46-72` (`rebuild()`), `tools/sd_sync.py:76-92`
- **What's wrong:** `rebuild()` runs render_audio → gen_esphome → gen_previewer and returns. Nothing pushes the rendered track, the page, or the firmware. Adding "The Ballad of the Witches' Road" on 08-22 produced three correct local artifacts and left the castle on a nine-scene image; `/api/scene` answered `unknown scene` and `/sd/scenes/10_*.mp3` was a 404, with no signal anywhere that this had happened.
- **Fix:** Add a fourth step, or a `POST /studio/publish`, that calls `sd_sync.cmd_scenes` + `cmd_site` when a castle answers, and reports what still needs a firmware build. Return the publish result in the rebuild body so the desk can show it.
- **Effort:** M
- **Grade lift:** A− → A (closes the one gap where the system silently forks)

#### A2 — Scenes are compile-time only, and dram0 is nearly out
- **Status:** ◐ interim 2026-08-23 — ceiling decided and documented (12 scenes, scenes/scenes.yaml header; weekly CI compile alarms at 92% dram0). The card-loaded cue format stays future work
- **Where:** `firmware/generated/scenes.yaml` (one script per scene), `firmware/castle.yaml` sdkconfig notes
- **What's wrong:** Every scene is a generated ESPHome script, so a new one needs a rebuild + OTA and costs static RAM. The tenth scene took dram0 from ~82.7% to **87.91% (151,236/172,032 — 20,796 B left)** and idle heap from 62 KB → 53 KB. At ~9 KB per song scene that is two more scenes, then the wall.
- **Fix:** Decide the ceiling deliberately. Either cap song-scenes and document it in `scenes/scenes.yaml`, or move cue timelines to a card-loaded binary the firmware interprets (one interpreter, N scenes, flat RAM). Measure with `make sd-build` before committing to either.
- **Effort:** L
- **Grade lift:** A− → A (removes a hard ceiling from the product's main axis of growth)

#### A3 — Eight files are within 20 lines of the cap
- **Status:** ✓ done 2026-08-23 — sd_web.h → sd_web_util.h (helpers), gen_esphome.py → gen_esphome_audio.py (audio emitters); both under 455 lines
- **Where:** `tools/gen_esphome.py` (499/500), `firmware/sd_web.h` (498), `tests/test_gen_esphome.py` (493), `tools/studio.py` (492), `tests/test_studio_api.py` (489), `.claude/grade-report-2026-08-16.md` (484), `docs/castle-wiring.html` (483), `tools/castle_fuzz.py` (481)
- **What's wrong:** `gen_esphome.py` is one line from the wall. The cap is a good rule, but at 499 the next feature must be a refactor first, and the pressure lands on the file that generates the firmware — the riskiest place to be rushed.
- **Fix:** Split `gen_esphome.py` on its existing seam (cue-script emission vs audio/manifest emission → `gen_esphome_audio.py`). Same for `sd_web.h` (handlers vs server setup). Do it while nothing is urgent.
- **Effort:** M
- **Grade lift:** A− → A− (removes forced-refactor-under-pressure risk; no structural change)

#### A4 — The device modules bypass the `api.ts` doorway
- **Status:** ✓ done 2026-08-23 — five raw fetches now behind api.ts castle helpers (castleProbe/castleAction/castleGet/castleBootlog/castlePut)
- **Where:** `web/src/device.ts:74,158`; `web/src/device_panel.ts:153,368,385`
- **What's wrong:** `api.ts` exists to be the one typed door to the wire (its header says so), but five raw `fetch()` calls in the two device modules skip it — no shared timeout, no shared error shape, response types cast at each site.
- **Fix:** Move the five calls behind `api.ts` helpers (`status()`, `files()`, `bootlog()`, `putCard()`, `act()`), reusing its `AbortSignal.timeout(QUICK)` convention.
- **Effort:** S
- **Grade lift:** A− → A− (consistency; kills five bespoke error paths)

#### A5 — The lean-page rewrite lives only in the studio
- **Status:** ✓ done 2026-08-23 — sd_sync site pushes the lean rewrite + /site/<sid>.mp3 tracks; lean() grew route/suffix params
- **Where:** `tools/gen_previewer.py:214-242`, `firmware/sd_web_site.h`
- **What's wrong:** `lean()` replaces each inlined audio data-URI with a `/studio/scene-audio/<id>` link, and only the studio serves it. The firmware serves `previewer/castle-cue-desk.html` verbatim, so the device — the slowest client on the worst network — is the only one that gets the fat page. See G1.
- **Fix:** Have `sd_sync.cmd_site` push the lean rewrite plus the per-scene mp3s to `/sd/site/audio/`, and point the rewrite at that path so the firmware's existing `/site/*` handler serves them.
- **Effort:** M
- **Grade lift:** A− → A− (removes an asymmetry where the weakest client gets the heaviest artefact)

#### A6 — The 3.3 MB generated page is tracked in git
- **Status:** ✓ done 2026-08-23 — decision recorded in CLAUDE.md (kept tracked, budgeted by tests/test_gen_previewer.py)
- **Where:** `previewer/castle-cue-desk.html`, `.gitignore`
- **What's wrong:** A fully generated artefact, now 3,310,543 bytes, is committed on every preview rebuild. It is exempt from the line cap but not from history; the repo carries a new multi-MB blob per scene edit.
- **Fix:** Either gitignore it and make `make preview` a required step (documented in README), or keep tracking it and accept the growth deliberately — but note the decision in `CLAUDE.md`, since it is currently implicit.
- **Effort:** S
- **Grade lift:** A− → A− (repo hygiene; no runtime effect)

---

## B — Backend Quality — B+

Both backends are careful. The studio is a small explicit router (27 routes, `docs/API.md` is accurate), writes scenes.yaml through temp+rename (`studio_scenes.py:86-91`), serialises encode jobs behind a lock with a documented timeout rationale, and binds `127.0.0.1` unless `--lan` is passed. The firmware web layer is better than most hobby C: `safe_name()` rejects traversal, dotfiles, control bytes and quotes; `write_body()` writes `.part` then renames with the FAT no-overwrite caveat spelled out; `vTaskDelay(1)` per chunk is there because the watchdog reset the board three times before someone worked out why. What holds it at B+ is that status reports a build-time truth as if it were a card truth (B1) and the card is only half-visible (B2).

**In plain terms:** the server code is solid and defensive. The weak spot is the status it reports back — the one screen you'd trust when something's wrong is the one that can't see the whole problem.

#### B1 — `missing` is generated at build time, so it cannot report an unknown scene
- **Status:** ✓ done 2026-08-23 — /api/status carries `scenes` (build ids, from g_scene_ids); emulator + docs updated
- **Where:** `firmware/generated/audio_sd.yaml` (`manifest_check`), `tools/gen_esphome.py`
- **What's wrong:** The manifest is a generated list of `stat()` calls, one per scene *the build knows about*. A nine-scene firmware reports `missing:""` while the card is missing the tenth scene entirely — which is exactly what `/api/status` said all through the 08-22 debug. The field reads as "the card has everything" and means "the scenes I was compiled with are here".
- **Fix:** Add `scenes` (the id list) to `/api/status` so the desk can diff against `scenes.yaml`, and rename the field's meaning in `docs/API.md`. Optionally have `manifest_check` also `opendir("/sd/scenes")` and report files present that it does not recognise.
- **Effort:** S
- **Grade lift:** B+ → A− (turns the most-trusted diagnostic from misleading into authoritative)

#### B2 — `/api/files` lists only the card root
- **Status:** ✓ done 2026-08-23 — /api/files?d=<subdir>, validated by safe_subpath; sd_sync uses it to skip unchanged files
- **Where:** `firmware/sd_web.h:224-240` (`opendir("/sd")`)
- **What's wrong:** The listing is hardcoded to the root, so the desk cannot see `/sd/scenes/` — the directory that actually holds the show. `device_panel.ts:205` works around it by inferring scene presence from `st.missing`, which is the field B1 shows is unreliable.
- **Fix:** Accept `?d=<subdir>` (validated with the existing `safe_subpath`) and have the panel list `/sd/scenes` directly.
- **Effort:** S
- **Grade lift:** B+ → A− (the desk can finally see what it is managing)

#### B3 — No free-space check before an upload
- **Status:** ✓ done 2026-08-23 — write_body refuses 507 before the first byte (64 KB slack)
- **Where:** `firmware/sd_web.h:267-300` (`write_body`)
- **What's wrong:** `write_body` streams `content_len` bytes with no comparison against free space. `sd_space_kb()` already exists and is cached (`:151-160`). On a full card the write fails partway; cleanup is graceful (the `.part` is unlinked) but the operator gets "short write" rather than "not enough room".
- **Fix:** Compare `req->content_len` against `sd_space_kb()` before `fopen` and return `507 Insufficient Storage` with the numbers.
- **Effort:** S
- **Grade lift:** B+ → B+ (better failure message; no new capability)

#### B4 — The studio relays every unclaimed `/api/*` path to the castle
- **Status:** ✓ done 2026-08-23 — castle_link.forward() gates on KNOWN_API; unknown /api/* is a 404 naming the known routes
- **Where:** `tools/studio.py:230-231,260-261,352-353`
- **What's wrong:** Carried over from the 08-21 report (A1 there, partially addressed by moving authoring routes to `/studio/*`). A typo'd or future-firmware path still becomes a castle call or a 502 rather than a 404, so a client bug is indistinguishable from a castle outage.
- **Fix:** Keep the relay but gate it on a known-route allowlist derived from `docs/API.md`; unknown `/api/*` → 404 with the allowlist in the body.
- **Effort:** S
- **Grade lift:** B+ → B+ (a client typo stops looking like an outage)

#### B5 — Uploads are acknowledged by byte count, never verified
- **Status:** ✓ done 2026-08-23 — write_body returns crc32 (esp_rom_crc32_le); sd_sync compares (zlib) and fails loudly. Verify once on real hardware — ROM CRC vs zlib parity is asserted only emulator-side
- **Where:** `firmware/sd_web.h:267-300`, `tools/sd_sync.py:50-58`
- **What's wrong:** The device replies with `bytes` written and `sd_sync.upload` compares it to the length sent. That catches truncation but not corruption, and nothing re-reads the file. A silently bad SD sector produces a scene track that `stat()`s fine and plays as noise — which is a live hypothesis in `docs/ISSUE-scene-start-audio.md`.
- **Fix:** Have `h_put` accumulate a cheap checksum (CRC32 over the written bytes) and return it; `sd_sync.upload` compares against a local CRC of the same buffer.
- **Effort:** M
- **Grade lift:** B+ → B+ (removes a suspect from an open audio bug)

---

## C — Frontend Quality — B

Sampled live, not just read. The strengths are real: `tsconfig.json` runs `strict` plus `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noUnusedLocals/Parameters` — stricter than most production apps; `a11y.spec.ts` is a genuine accessible-name audit over every visible control, not an axe-core smoke test; `mobile.spec.ts` enforces a 44px tap floor, a 12px caption floor and no horizontal overflow at three widths; `aria-pressed` is used on 16 toggles; the castle-went-down state is thoughtfully designed (chip dims, controls that would lie are disabled, "last seen 20:47" replaces the now-playing line). What pulls it to B is a single defect class repeated across the device UI: **the poll re-renders by replacing `innerHTML`, so anything the user is holding onto is destroyed** (C1, C2) — plus a blank-screen failure mode the owner actually hit (C3).

**In plain terms:** the desk is well built and unusually thoughtful about phones and screen readers. But the castle widget rebuilds itself from scratch every 15 seconds, which yanks the volume slider out from under your finger and drops your place in the panel — and if the castle is off when you open the page, you get an empty box that never says it's looking.

#### C1 — The 15-second poll replaces the chip mid-interaction
- **Status:** ✓ done 2026-08-23 — chip render parks while pointer/focus is on it, flushes on release; e2e covers the held slider
- **Where:** `web/src/device.ts:368` (`chip.innerHTML = chipHtml(...)`), called from `refresh()` at `:395`, scheduled at `:445` (`POLL_MS = 15000`)
- **What's wrong:** `render()` rewrites the whole chip, including the `#devVol` range input (`:295,313`). There is no `activeElement`, drag or `:active` guard anywhere in the file. Drag the volume slider and a poll landing mid-drag replaces the element under the pointer: the drag dies and the value snaps back to whatever the castle last reported. Keyboard focus lands on `<body>`.
- **Fix:** Skip `render()` while `chip.contains(document.activeElement)` or a pointer is down on the chip, and re-render on `pointerup`/`blur`. Better: update the three volatile nodes (`#devNow`, `#devVol`, version) in place instead of rewriting the subtree.
- **Effort:** S
- **Grade lift:** B → B+ (fixes the most-touched control on the device UI)

#### C2 — The castle panel re-renders wholesale, losing scroll and focus
- **Status:** ✓ done 2026-08-23 — panel skips typing-focus rebuilds, memoises the payload, restores scroll/focus/details/boot log
- **Where:** `web/src/device_panel.ts:186-200` (`render()` → `this.body.innerHTML = …`), driven by `device.ts:365,425` and every poll
- **What's wrong:** Same pattern, bigger surface. Open the panel, scroll into the boot log or the card's file list, and the next poll rebuilds the DOM: scroll position resets, any focused button is gone, and an open `<details>` collapses. Verified by inspection — no `activeElement`, `scrollTop` or `focus()` appears in `device_panel.ts` or `device_tests.ts`.
- **Fix:** Same guard as C1 for focus, plus save/restore `scrollTop` on `this.body` and the open state of any `<details>` around the innerHTML swap. A `data-key` diff on the file list would be better still.
- **Effort:** M
- **Grade lift:** B → B+ (the panel becomes usable while it updates)

#### C3 — A castle absent at page load renders an empty box, silently
- **Status:** ✓ done 2026-08-23 — 'looking for the castle' + Retry after three misses, only when the studio names a configured host (new `castle` status field); e2e covers it
- **Where:** `web/src/device.ts:448-456` (`probe().then(...)`, `RETRY_MS = 5000`)
- **What's wrong:** If the first probe fails, `firstContact()` never runs and the chip keeps its initial markup — literally `<div id="deviceChip"></div>`, confirmed in the browser this session. The retry loop runs every 5 s forever with no visible sign. The rich down-state UX at `:410-427` only exists for a castle that was seen once. This is exactly the "the local website isn't loading the device" report.
- **Fix:** Render a "looking for the castle…" placeholder before the first probe, and after ~3 failed retries show which host was tried and a Retry button. The information is already in `hosts.py`/`devices.toml`.
- **Effort:** S
- **Grade lift:** B → B+ (converts a blank box into a diagnosable state)

#### C4 — The panel is a modal-ish overlay with no dialog semantics
- **Status:** ✓ done 2026-08-23 — role=dialog, aria-modal, Escape closes, focus in on open and back to the opener on close
- **Where:** `web/src/device_panel.ts:176-185` (`toggle()` sets `display`), `previewer/panels.css`
- **What's wrong:** No `role="dialog"`, no `aria-modal`, no focus move into the panel on open, no Escape-to-close, no focus return to `#devMore` on close — grep for `Escape`, `role="dialog"` and `focus()` in `device_panel.ts`/`device.ts` returns nothing. A keyboard user opening the panel is left where they were, tabbing through the desk behind it.
- **Fix:** `role="dialog"` + `aria-labelledby` on the header, focus the close button on open, return focus to the opener on close, and close on Escape. `a11y.spec.ts` already has the harness to assert it.
- **Effort:** S
- **Grade lift:** B → B+ (the one a11y gap the existing audit does not cover)

#### C5 — The a11y audit skips the zone designer
- **Status:** ✓ done 2026-08-23 — zone designer controls carry aria-labels from row+column; the audit exclusion is deleted
- **Where:** `web/test/e2e/a11y.spec.ts:38-39` (`if (el.closest("#zoneDesigner")) continue;`), `web/src/zone_designer.ts`
- **What's wrong:** The exclusion is honest and commented ("names its controls by column header only") but it means a whole grid of controls is known-unnamed and permanently unchecked. The comment defers to "owned by the light pipeline", which is not a reason it cannot have labels.
- **Fix:** Give each grid cell an `aria-label` built from its row and column headers (`"tower left · center effect"`), then delete the exclusion so the audit covers it.
- **Effort:** S
- **Grade lift:** B → B (closes the last named a11y hole)

#### C6 — Nothing in the desk shows that the castle's firmware is behind
- **Status:** ✓ done 2026-08-23 — panel warns 'N scenes newer than the firmware', picker dims .scene--stale tiles; e2e covers both
- **Where:** `web/src/device_panel.ts:203-230` (`healthMeta`, version line)
- **What's wrong:** The panel shows the firmware version but has nothing to compare it against, so a board running a scene list older than `scenes.yaml` looks perfectly healthy. The only way to discover the drift is to press a scene button and read `unknown scene` in a toast.
- **Fix:** Depends on B1. Once `/api/status` returns the board's scene ids, show "2 scenes newer than the firmware — rebuild and OTA" in the health row, and grey those scenes in the picker.
- **Effort:** M
- **Grade lift:** B → B+ (the failure the owner hit becomes visible before it bites)

---

## D — Testing & Reliability — A−

This is the strongest category and it is not close. 791 Python tests, 136 Playwright e2e, 14 node suites, all green (`make check` exit 0, full e2e 143 passed in 2.0m). The quality is what earns the grade, not the count: `test_firmware_contract.py` parses `sd_web.h` and fails when `castle_emu_wire.py` drifts from the C; `firmware_parity.mjs` holds the browser's effects to the firmware's frame-exact with 22,944 checks; `test_castle_chaos.py` and the fuzz suites (45,733 assertions over 3,000 rounds) cover the protocol; `studio_case.py` and `playwright.config.ts` sandbox `CASTLE_TRACKS`/`CASTLE_SCENES`/`CASTLE_HOST` so no test can touch the real library. Zero skipped tests. What keeps it off A: the coverage ratchet is slack (D1) and the publish gap shipped without a single test noticing (D2).

**In plain terms:** the testing here is better than most professional teams manage — including tests that read the firmware's C source and fail if a simulator drifts from it. The gap is that nothing tests the step between "it works on my laptop" and "it works on the castle".

#### D1 — The coverage floor is 72% against a 791-test suite `[BE]`
- **Status:** ✓ done 2026-08-23 — measured 83%; COVERAGE_MIN raised 72 → 82 with the raise-on-beat rule in the comment
- **Where:** `Makefile:120-128` (`COVERAGE_MIN := 72`), `.github/workflows/ci.yml`
- **What's wrong:** With this many tests the real number is almost certainly well above the gate, so the ratchet is not ratcheting — it permits a large regression before it complains.
- **Fix:** Run `make coverage`, set `COVERAGE_MIN` to the measured value minus 1, and note in the Makefile comment that it is raised whenever it is beaten. The comment already says "raise it as coverage lands"; act on it.
- **Effort:** S
- **Grade lift:** A− → A (the gate starts protecting the work that has been done)

#### D2 — No test covers the desk→castle publish path `[both]`
- **Status:** ✓ done 2026-08-23 — tests/test_studio_publish.py drives publish against the emulator, stale scene reported
- **Where:** `tests/test_studio_api.py`, `tools/studio_scenes.py:46-72`, `tests/test_sd_sync.py`
- **What's wrong:** `rebuild()` is tested for its three local steps and `sd_sync` is tested in isolation, but nothing asserts that a scene added through `/studio/scene` ends up reachable on a castle. The emulator (`castle_emu.py`) makes that assertion cheap and it was never written — which is why A1 shipped silently.
- **Fix:** An e2e or studio-case test: add a scene against a sandboxed `CASTLE_SCENES`, point `CASTLE_HOST` at the emulator, publish, then assert `/sd/scenes/NN_<id>.mp3` exists on the emulator and that a scene id unknown to it is reported rather than swallowed.
- **Effort:** M
- **Grade lift:** A− → A (covers the one seam with a known production failure)

#### D3 — No regression guard on page weight `[FE]`
- **Status:** ✓ done 2026-08-23 — TestPageWeight budgets the built page at 3.6 MB with the raise-it-deliberately message
- **Where:** `web/test/e2e/lean_page.spec.ts`, `tools/gen_previewer.py`
- **What's wrong:** `lean_page.spec.ts` asserts scene audio is linked rather than inlined for the studio, but nothing asserts a ceiling on the built page. It grew 2.13 MB → 3.31 MB when one scene was added, with no test reacting.
- **Fix:** Assert `previewer/castle-cue-desk.html` is under a stated byte budget in `tests/test_gen_previewer.py`, with the number and its reason in the failure message so raising it is a deliberate act.
- **Effort:** S
- **Grade lift:** A− → A− (makes G1's growth visible the moment it happens)

#### D4 — Render performance is asserted only as "does it stop" `[FE]`
- **Status:** ✓ done 2026-08-23 — __castleDraws.ms per-paint durations; e2e asserts p95 paint < 16 ms while a scene runs
- **Where:** `web/test/e2e/idle_loop.spec.ts:30-43`
- **What's wrong:** The idle test is good — it proves the dirty-flag loop stops painting after Stop (≤4 frames) — but there is no assertion about frame rate *while* a scene runs. I could not measure it in this session either: the Browser pane reports `visibilityState: "hidden"`, so `requestAnimationFrame` is throttled to zero and any headless fps number would be a lie.
- **Fix:** Instrument `__castleDraws` with a per-frame duration histogram and assert the 95th percentile paint stays under budget, which does not depend on rAF cadence and so survives a hidden tab.
- **Effort:** M
- **Grade lift:** A− → A− (adds the half of render perf that is currently unmeasured)

#### D5 — The firmware is validated in CI but never compiled `[BE]`
- **Status:** ✓ done 2026-08-23 — weekly scheduled CI job compiles castle_sd.yaml and fails over 92% dram0
- **Where:** `.github/workflows/ci.yml:1-9` (documented decision)
- **What's wrong:** The choice is reasoned and written down — ~10 min per variant, and `esphome config` catches most of it. But it cannot catch the failure mode that is now closest: a build that links locally at 87.9% dram0 and does not on a slightly different toolchain. RAM regressions are invisible to `config`.
- **Fix:** Keep skipping the compile on PRs, but add a weekly scheduled job that compiles `castle_sd.yaml` and fails if DIRAM exceeds a stated percentage. That turns A2's ceiling into an alarm instead of a surprise.
- **Effort:** M
- **Grade lift:** A− → A− (guards the resource the project is closest to exhausting)

---

## E — Security — B+

Graded on what is in scope: the owner's accepted-risk position (no Origin/Host validation on the studio, no auth on the SD build's `PUT /api/ota` and `/api/files/`) is a deliberate, documented decision for a LAN-only decoration with one operator, and is not counted here. Everything else checks out and several things are done properly: `safe_name()` and `safe_subpath()` reject traversal, dotfiles, control bytes, quotes and backslashes, and `h_sd_get` decodes *before* validating (the correct order); every card filename reaching the DOM goes through `esc()` and every one reaching a URL through `encodeURIComponent` (`device_panel.ts:62,80,212,319,330`); the studio binds `127.0.0.1` unless `--lan` is passed explicitly; `firmware/secrets.yaml` is a symlink outside the repo, gitignored, with only `.example` tracked.

**Honest count:** I found four material items, not five. Padding this list would mean inventing findings, which is worse than a short list — the remaining surface really is clean.

**In plain terms:** the parts that handle untrusted input — filenames, URLs, uploads — are written defensively and correctly. The open items are supply-chain freshness and one missing guardrail, not holes.

#### E1 — Eight advisories in the pinned build toolchain
- **Status:** ✓ done 2026-08-23 — .pip-audit-ignore: one line per id with reason + review date; Makefile reads it
- **Where:** `requirements.lock` (`starlette 0.52.1` ×7, `cryptography 49.0.0` ×1)
- **What's wrong:** `pip-audit` reports PYSEC-2026-161/248/249/2280/2281 against starlette and PYSEC-2026-3552 against cryptography. These arrive transitively through the pinned `esphome==2026.7.4`; `CLAUDE.md` records that they are ignored by id and that cryptography clears on the next esphome bump. That position is sound, but it is a standing exception with no expiry.
- **Fix:** Put the ignore list in a checked-in `pip-audit` config with a one-line reason and a review date per id, so `make audit` shows *why* each is ignored rather than relying on memory.
- **Effort:** S
- **Grade lift:** B+ → A− (an audited exception instead of an unwritten one)

#### E2 — Nothing stops `firmware/secrets.yaml` from being committed
- **Status:** ✓ done 2026-08-23 — pre-commit refuses a staged secrets.yaml and any staged hunk containing the PSK
- **Where:** `.gitignore:9`, `githooks/pre-commit`
- **What's wrong:** The WiFi PSK is protected by a gitignore line alone. `git add -f`, a tooling change that follows the symlink, or a future `.gitignore` edit would commit it, and the pre-commit hook (ruff, mypy, check_loc) would not notice.
- **Fix:** Add a line to `githooks/pre-commit` that fails if any staged path matches `secrets.yaml` or if a staged file contains the PSK.
- **Effort:** S
- **Grade lift:** B+ → B+ (defence in depth on the repo's only real secret)

#### E3 — Uploads have no size ceiling
- **Status:** ✓ done 2026-08-23 — 507 free-space precondition + 413 ceiling (8 MB) on /api/site/
- **Where:** `firmware/sd_web.h:277` (`remaining = req->content_len`)
- **What's wrong:** Any LAN client can PUT until the card fills; there is no cap and no free-space precondition. Not an auth question (that is accepted risk) — a resource question. Same root as B3.
- **Fix:** As B3, plus a hard maximum for `/api/site/` (a desk page has a known plausible size) so a mistake cannot consume 31 GB.
- **Effort:** S
- **Grade lift:** B+ → B+ (bounds an unbounded resource)

#### E4 — No CSP on either served page
- **Status:** ✓ done 2026-08-23 — one CSP from the firmware (set_csp), the emulator and the studio's HTML responses
- **Where:** `firmware/sd_web_site.h` (`h_site`, `h_root`), `tools/studio_http.py`
- **What's wrong:** Neither the desk nor `/remote` sends a Content-Security-Policy. Escaping is currently correct everywhere I checked, so this is depth rather than an active hole — but `safe_name()` permits `<` and `>`, so a filename is one missed `esc()` away from executing.
- **Fix:** Send `Content-Security-Policy: default-src 'self'; script-src 'unsafe-inline' 'self'` (the page is deliberately one self-contained file, so inline scripts must stay allowed) from both servers.
- **Effort:** S
- **Grade lift:** B+ → B+ (a second line behind the escaping)

---

## F — Dependencies & Tech Currency — B

Runtime dependencies are explicit and current: `numpy~=2.5`, `scipy~=1.18`, `PyYAML~=6.0`, `segno~=1.6`, `aioesphomeapi~=45.7`, `Pillow~=12.3`, with `esphome==2026.7.4` pinned to the version CI validates against — and the file explains *why* aioesphomeapi and Pillow are listed directly (they used to arrive transitively and an esphome release could have dropped them). `requirements.lock` is frozen from the real venv by `make lock`. The npm side is on esbuild 0.28 and TypeScript 7 with `npm outdated` clean. What holds it at B is the eight standing advisories (E1) and the absence of any process for noticing when the pins age.

**In plain terms:** nothing here is stale or abandoned, and the pinning is done for stated reasons rather than by accident. What's missing is a routine that tells you when it's time to move.

#### F1 — The esphome pin is the ceiling on eight advisories, with no bump trigger
- **Status:** ✓ done 2026-08-23 — the weekly CI job prints newer esphome versions next to the dram0 gate
- **Where:** `requirements.txt:9`, `requirements.lock`
- **What's wrong:** `esphome==2026.7.4` is what drags in the vulnerable starlette and cryptography. `CLAUDE.md` says cryptography "clears with the next esphome bump", but nothing schedules or watches for that release.
- **Fix:** Add a `make audit` note (or the scheduled CI job from D5) that checks for a newer esphome and reports whether it moves the transitive pins.
- **Effort:** S
- **Grade lift:** B → B+ (the bump happens when it can, not when someone remembers)

#### F2 — No lockfile for the Python dev tools
- **Status:** ✗ no change needed 2026-08-23 — FALSE FINDING: requirements.lock already pins ruff/mypy/coverage/pip-audit (lines 25/49/60/90) and CI installs from the lock
- **Where:** `requirements-dev.txt` (`ruff~=0.16`, `mypy~=2.3`, `coverage~=7.15`, `pip-audit~=2.10`)
- **What's wrong:** The dev gates float on compatible-release specifiers while runtime deps are locked. A ruff or mypy minor release can fail CI on a commit that changed nothing — the exact class of surprise `requirements.lock` exists to prevent.
- **Fix:** Extend `make lock` to emit `requirements-dev.lock` and have CI install from it.
- **Effort:** S
- **Grade lift:** B → B+ (CI stops failing for reasons unrelated to the commit)

#### F3 — TypeScript 7 and esbuild 0.28 are pinned only by caret
- **Status:** ✓ done 2026-08-23 — esbuild 0.28.2 and typescript 7.0.2 pinned exactly in package.json
- **Where:** `web/package.json` (`"typescript": "^7.0.2"`, `"esbuild": "^0.28.2"`)
- **What's wrong:** `package-lock.json` pins the installed tree and CI uses `npm ci`, so builds are reproducible — but a caret on a compiler means `npm i` on a fresh machine can pick up a minor with different inference and a different bundle.
- **Fix:** Pin both exactly, and bump deliberately (the repo already did exactly this for the 0.28/TS7 move — commit `6dc0601` gated it on the full suite).
- **Effort:** S
- **Grade lift:** B → B (matches the discipline already applied to Python)

#### F4 — Node version is stated only in CI
- **Status:** ✓ done 2026-08-23 — .nvmrc (22) + engines.node in package.json
- **Where:** `.github/workflows/ci.yml` (`node-version: "22"`), no `.nvmrc` or `engines`
- **What's wrong:** CI builds on Node 22; a contributor's machine builds on whatever is installed. The bundler and Playwright both care.
- **Fix:** Add `.nvmrc` with `22` and an `engines.node` field to `web/package.json`.
- **Effort:** S
- **Grade lift:** B → B (removes an unstated environment assumption)

#### F5 — The Python version floor is implicit
- **Status:** ✓ done 2026-08-23 — pyproject [project] requires-python >=3.13; `make pycheck` fails an old interpreter loudly
- **Where:** `Makefile` (python3.13 venv, falls back to `python3`), `pyproject.toml`
- **What's wrong:** `make setup` builds a 3.13 venv and CI uses 3.13, but the fallback to bare `python3` means an older interpreter can silently be used, and `pyproject.toml` does not declare `requires-python`.
- **Fix:** Add `requires-python = ">=3.13"` and have the Makefile fallback fail loudly on an older interpreter rather than proceeding.
- **Effort:** S
- **Grade lift:** B → B (turns a silent mismatch into an error)

---

## G — Performance & Scalability — B−

Measured, not assumed. The desk itself is genuinely fast: served lean by the studio it transfers **92 KB** (373 KB decoded), DOMContentLoaded **247 ms**, load **340 ms**, 710 DOM nodes, 4–5 MB JS heap; the minified application bundle is **152 KB**; and the dirty-flag frame loop paints **zero frames when idle**, which is exactly right for a page left open on a phone all evening. The grade is not about the desk — it is about two ceilings the project is now close to. The device-served page is **3,310,543 bytes, of which 2,937,568 (89%) is base64-inlined scene audio**, growing ~1.2 MB per song scene, with no lean path in the firmware. And dram0 sits at **87.91% with 20,796 bytes free** after the tenth scene, with playing heap down to **25 KB** against a documented ~20 KB failure floor.

**In plain terms:** the desk loads fast and doesn't waste battery when nothing's happening — that part is done well. The problem is growth: every song you add makes the castle's own web page about a megabyte heavier and eats ~9 KB of the memory it has 20 KB left of. Two more songs and you hit a wall in both directions at once.

#### G1 — The device serves a 3.3 MB page; 89% of it is inlined audio
- **Status:** ✓ done 2026-08-23 — the device now gets the lean page + on-demand audio via `sd_sync site` / publish (~150 KB gz first paint instead of 3.3 MB). Needs one `make publish` once the castle is back on
- **Where:** `previewer/castle-cue-desk.html` (script[0] = 3,085,609 chars of `CASTLE_GEN`; script[1] = 152,139 chars of app), `tools/gen_previewer.py:214-242`, `firmware/sd_web_site.h`
- **What's wrong:** Ten scenes are embedded as base64 data-URIs, which inflates already-compressed mp3 by ~33% and gzips to almost nothing (the pushed pair is 2,163 KB gzipped / 3,232 KB plain). Every load of the castle's own desk pulls all of it, over porch WiFi, before the page is usable — and none of it is needed until a scene is auditioned. The studio already solves this with `lean()`; the firmware never got it. See A5.
- **Fix:** Push the lean rewrite plus per-scene mp3s to `/sd/site/audio/` in `sd_sync.cmd_site` and serve those through the existing `/site/*` handler. Expected: ~150 KB first paint instead of 3.3 MB.
- **Effort:** M
- **Grade lift:** B− → B+ (the largest single UX win available, and it removes the per-scene growth)

#### G2 — dram0 is at 87.9% and each new scene costs ~9 KB
- **Status:** ◐ interim 2026-08-23 — same decision as A2: documented 12-scene budget + CI alarm; the flat-RAM cue format is the next step if the show outgrows it
- **Where:** `make sd-build` output (DIRAM 151,236/172,032), `firmware/generated/scenes.yaml`
- **What's wrong:** Same root as A2, stated as a number: 20,796 bytes of headroom, ~9 KB per song scene, so two more scenes. Idle heap fell 62 → 53 KB and playing heap to 25 KB, against the ~20 KB floor `device_panel.ts:145` itself warns about. The next scene is likely to make audio worse before it makes the build fail.
- **Fix:** Per A2 — either a documented cap or a card-loaded cue format. In the meantime add the DIRAM assertion from D5 so the ceiling announces itself.
- **Effort:** L
- **Grade lift:** B− → B (converts an invisible wall into a measured budget)

#### G3 — The lean page eagerly downloads every scene's audio at load
- **Status:** ✓ done 2026-08-23 — lean-page audio elements are preload=none (fetch on first play); data: URIs unaffected
- **Where:** `tools/gen_previewer.py:229` (`lean()` rewrites to `<audio src=…>`), observed: 10 requests totalling ~2.2 MB immediately after load
- **What's wrong:** The lean rewrite fixes the *parse* cost but not the *transfer* cost — the browser still fetches all ten tracks on load (the two song scenes alone are 802 KB and 774 KB). On localhost this is invisible; from the castle it is the same megabytes as G1, merely later.
- **Fix:** Emit `preload="none"` on the rewritten audio elements and let the first play fetch. The studio already honours Range requests, so seeking still works.
- **Effort:** S
- **Grade lift:** B− → B (one attribute; removes ~2.2 MB of speculative transfer)

#### G4 — The chip and panel re-render on a timer regardless of change
- **Status:** ✓ done 2026-08-23 — chip markup memo + panel payload memo: an unchanged poll re-renders nothing
- **Where:** `web/src/device.ts:368,445`; `web/src/device_panel.ts:186-200`
- **What's wrong:** Every 15 s the chip's subtree is rebuilt and the panel's body re-parsed from a string, whether or not `/api/status` returned anything new. On a kiosk phone left open all night that is ~5,760 needless parses, and it is the mechanism behind C1/C2.
- **Fix:** Hash the status payload and skip the render when it is unchanged; combined with C1's in-place updates this makes the steady state free.
- **Effort:** S
- **Grade lift:** B− → B (removes the only recurring cost on an idle page)

#### G5 — The scene picker holds every scene's audio element alive
- **Status:** ✓ done 2026-08-23 — RenderedAudio builds elements on first audition and releases all but the playing one
- **Where:** `web/src/audio.ts` (`RenderedAudio`), `web/src/main.ts:52`
- **What's wrong:** `RenderedAudio` is constructed with the whole `GEN.audio` map at startup, so all ten decoded/held sources persist for the session rather than being created per audition. Heap measured 4–5 MB, so this is not urgent today — but it scales with the same per-scene factor as G1 and G2.
- **Fix:** Construct the element lazily on first audition of a scene and release it when another scene is loaded.
- **Effort:** M
- **Grade lift:** B− → B− (pre-empts the third per-scene growth axis)

#### G6 — Card capacity is polled but upload throughput is not bounded
- **Status:** ◐ done-in-code 2026-08-23 — watchdog fed every 4th chunk (one tick / 32 KB, ~4× throughput), mirrored in the emulator. MUST be verified on the bench before show night (RUNBOOK 'After changing firmware')
- **Where:** `tools/sd_sync.py:50-58` (`timeout=600`), `firmware/sd_web.h:288` (`vTaskDelay(1)` per 8 KB chunk)
- **What's wrong:** The `vTaskDelay(1)` per chunk is the right fix for the watchdog, but it also caps throughput at roughly one tick per 8 KB. The 2,350 KB scene upload takes long enough that the 600 s client timeout is doing real work, and a full ten-scene push is minutes.
- **Fix:** Measure whether a larger chunk (32 KB) with the same one-tick delay keeps the watchdog happy — a 4× throughput win if it does. `tests/test_castle_chaos.py` is the place to prove the watchdog is still fed.
- **Effort:** M
- **Grade lift:** B− → B− (makes the publish step from A1 fast enough to run often)

---

## H — Documentation & Onboarding — A−

Unusually good, and unusually honest. `CLAUDE.md` is a working orientation document that explicitly overrides a conflicting global config, names the traps that bite (RMT symbol budget, the static-RAM cliff, ring-is-RGB-not-RGBW, stop-audio-before-OTA) and records the accepted-risk position so it stops being re-litigated. `docs/PARITY.md` documents the whole cross-copy contract; `docs/API.md` is an accurate route table; `HARDWARE_FINDINGS.md` §8 records the audio bring-up as evidence — what was ruled out and how. Open problems live in `docs/ISSUE-*.md` with what has already been eliminated, which is the format that actually saves time later. `firmware/pending/README.md` tracks written-but-unflashed patches and currently, correctly, says "Nothing pending". Inline comments explain *why* (`sd_web.h:283-287` on the watchdog; `device.ts:410-419` on which controls would lie). What keeps it off A is that the docs describe the system as designed rather than as operated (H1, H2).

**In plain terms:** the written material is better than most commercial projects — it explains reasoning, not just steps, and it keeps a record of what was already tried. What's missing is the operator's view: what to do on show night, and what the day-to-day workflow actually is once a song is added.

#### H1 — No runbook for the workflow the owner actually performs
- **Status:** ✓ done 2026-08-23 — docs/RUNBOOK.md: add-a-song end to end, will-not-play checklist, show night, OTA
- **Where:** `README.md`, `CLAUDE.md`, `docs/`
- **What's wrong:** "I imported a song, now what?" is not answered anywhere. The full chain — import → scene → rebuild → `sd_sync scenes` → `sd-build` → OTA → confirm — exists only as scattered `make` targets, which is precisely why A1 went unnoticed.
- **Fix:** Add `docs/RUNBOOK.md`: adding a song end to end, pushing to the castle, verifying it took, and what to check when a scene will not play (`/api/status`, `/sd/scenes/`, `unknown scene`).
- **Effort:** S
- **Grade lift:** A− → A (documents the path with the known failure on it)

#### H2 — The `missing` field's meaning is not documented
- **Status:** ✓ done 2026-08-23 — docs/API.md states what `missing` can and cannot see, next to the new `scenes` field
- **Where:** `docs/API.md:53`
- **What's wrong:** The table lists `/api/status` without saying that `missing` covers only scenes the running build knows about. Anyone reading the doc would trust it the way it was trusted on 08-22.
- **Fix:** One line in the route table, removed again when B1 lands.
- **Effort:** S
- **Grade lift:** A− → A− (stops the doc endorsing a misleading field)

#### H3 — `CONTRIBUTING.md` is 19 lines
- **Status:** ✓ done 2026-08-23 — CONTRIBUTING gains the e2e one-command note and the pointer at CLAUDE.md's reasoning
- **Where:** `CONTRIBUTING.md`
- **What's wrong:** The real rules — 500-line cap, ruff/mypy/tsc clean, `make check` green, never skip a test, bump the firmware version, commit-message voice — live in `CLAUDE.md`, which reads as agent configuration. A human contributor would not find them.
- **Fix:** Have `CONTRIBUTING.md` state the gates and link `CLAUDE.md` for the reasoning.
- **Effort:** S
- **Grade lift:** A− → A− (the rules become findable by people, not just agents)

#### H4 — `docs/WIRING.md` is 411 lines against a 500-line cap
- **Status:** ✓ done 2026-08-23 — WIRING.md split on its seams → WIRING-POWER-AUDIO.md + WIRING-BUILD.md, index up top
- **Where:** `docs/WIRING.md`
- **What's wrong:** The single largest doc, approaching the cap that applies to prose too. The next hardware change forces a split under time pressure, on the document you read with a soldering iron in hand.
- **Fix:** Split now on the obvious seam — power and grounds vs data lines and strips vs audio — with an index at the top.
- **Effort:** S
- **Grade lift:** A− → A− (same forced-refactor risk as A3, on the doc that matters at 2am)

#### H5 — Two grade reports, no index
- **Status:** ✓ done 2026-08-23 — the 08-16 report opens with an ARCHIVED banner naming the current file
- **Where:** `.claude/grade-report.md`, `.claude/grade-report-2026-08-16.md`
- **What's wrong:** Both are tracked and both count against the line cap (08-16 is at 484). Nothing says which is current or that IDs differ between them.
- **Fix:** Keep the dated archive but add a one-line header to each pointing at the current file; drop the oldest when a third lands.
- **Effort:** S
- **Grade lift:** A− → A− (housekeeping)

---

## I — Developer Experience & Tooling — A−

The local loop is the best-tooled part of the project. `make help` is real, the targets compose sensibly (`check` = CI, `check-all`, `e2e`, `coverage`/`audit` non-gating), and `make check` runs green end to end. The pre-commit hook runs the fast gates (ruff, mypy, `check_loc`) and defers the slow ones, with a comment explaining the venv fallback because a worktree without its own `.venv` once broke it. CI mirrors local exactly, cancels superseded PR runs, and every timeout carries a written rationale tied to an actual failure. `tools/castle_emu.py` plus the sandbox env vars mean the entire desk→studio→castle chain runs with no hardware — genuinely rare. What holds it off A is that the hook does not check TypeScript (I1) and the e2e suite needs manual setup steps (I3).

**In plain terms:** the day-to-day tooling is excellent — one command runs everything CI runs, and you can exercise the whole system without plugging in the castle. The main gap is that a TypeScript mistake can be committed and only caught later.

#### I1 — The pre-commit hook does not typecheck TypeScript
- **Status:** ✓ done 2026-08-23 — pre-commit runs tsc --noEmit when web/node_modules exists
- **Where:** `githooks/pre-commit` (ruff, mypy, `check_loc.py` only)
- **What's wrong:** Python gets both lint and types at commit time; `web/` gets neither. With a tsconfig this strict, `tsc --noEmit` is the highest-value check in the repo and it runs only in `make check`/CI.
- **Fix:** Add `cd web && npx tsc --noEmit` to the hook, guarded on `node_modules` existing so a Python-only clone still commits.
- **Effort:** S
- **Grade lift:** A− → A (closes the asymmetry between the two languages' gates)

#### I2 — `make check` does not run the e2e suite
- **Status:** ✓ done 2026-08-23 — `make check` prints that the browser suite did not run and what covers it
- **Where:** `Makefile` (`check` vs `check-all`, `e2e`)
- **What's wrong:** Reasonable — e2e takes ~2 minutes — but it means the documented "green before handing work back" gate excludes the 136 tests that cover the UI, the category the owner cares most about.
- **Fix:** Keep `check` fast, but have it print the e2e count it did not run and the exact command, so skipping is a visible choice.
- **Effort:** S
- **Grade lift:** A− → A− (the gap in the gate becomes explicit)

#### I3 — The e2e suite has undocumented prerequisites
- **Status:** ✓ done 2026-08-23 — `make e2e` builds the page, checks deps with a one-line fix, and installs chromium itself
- **Where:** `CLAUDE.md` (notes `make preview` and `npx playwright install chromium`), `web/playwright.config.ts`
- **What's wrong:** The prerequisites are recorded in the agent notes, not in a `make` target. `make e2e` on a fresh clone fails on a missing built page or a missing browser, with Playwright's error rather than the project's.
- **Fix:** Have the `e2e` target depend on `preview` and check for the chromium install, failing with the one-line fix if absent.
- **Effort:** S
- **Grade lift:** A− → A− (turns two tribal steps into one command)

#### I4 — No `make` target for the publish chain
- **Status:** ✓ done 2026-08-23 — `make publish` and `make ota` (which stops audio first)
- **Where:** `Makefile`, `tools/sd_sync.py`
- **What's wrong:** `sd_sync` subcommands (`scenes`, `site`, `tones`, `ota`) are only reachable as raw `.venv/bin/python tools/sd_sync.py <ip> <cmd>` invocations. Everything else in the project has a target; the step that talks to the castle does not — the same step A1 shows is easy to forget.
- **Fix:** Add `make publish` (scenes + site, host from `devices.toml`) and `make ota`, both depending on `generate`.
- **Effort:** S
- **Grade lift:** A− → A− (the forgotten step becomes as easy as the others)

#### I5 — No formatter on either language
- **Status:** ◐ partial 2026-08-23 — .editorconfig adopted; the mechanical whole-tree reformat (ruff format touches all 89 py files; prettier likewise) was deliberately declined mid-branch — it would bury this change-set and press eight files sitting within 25 lines of the cap. Revisit as its own commit on a quiet main
- **Where:** `pyproject.toml` (ruff lint only, no `[tool.ruff.format]`), `web/` (no prettier), no `.editorconfig`
- **What's wrong:** Lint rules are curated on purpose — style-only packs are excluded so the signal stays visible, and the comment says so. But that reasoning argues *for* a formatter, not against one: with none, whitespace and wrapping choices are hand-maintained across 86 Python and 78 TypeScript files, and formatting drift shows up in diffs as if it were change.
- **Fix:** Add `ruff format` (it shares the existing config and `line-length = 88`) and a minimal prettier for `web/src` + `previewer/*.css`; run both in the pre-commit hook alongside I1, and format the tree once in a single commit.
- **Effort:** S
- **Grade lift:** A− → A− (diffs stop carrying formatting noise)
