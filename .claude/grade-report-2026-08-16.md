# Codebase Grade Report

**Project:** halloween_esp — castle cue desk + ESPHome firmware
**Audited:** 2026-08-16
**Stack:** Python 3.13 tools (stdlib HTTP studio server, generators, DSP/analysis) · TypeScript/esbuild no-framework web app · ESPHome YAML + C++ headers on ESP32-S2 · unittest (397) + node engine suites + Playwright e2e (8 specs) + cross-language seeded fuzz parity

**Scope note:** UI/UX, visual design, styling and accessibility were excluded at
the user's request — a UI rewrite is planned, so those findings would not be
actionable. Category C therefore grades **frontend code** only: module
structure, typing discipline, state handling, and correctness. Items about
layout, theming, widget affordances or a11y were dropped rather than counted
against the grade.

**Prior report:** a 2026-08-14 audit exists at the main checkout's
`.claude/grade-report.md` with 21 items marked done. This is an independent
re-audit against the current tree — every finding below was verified against
today's code, and IDs are freshly numbered.

## Summary

| ID | Category | Grade | Items |
|----|----------|-------|-------|
| A | Architecture & Design | A− | 4 |
| B | Backend Quality | B | 5 |
| C | Frontend Quality (code only) | B+ | 4 |
| D | Testing & Reliability | B | 4 |
| E | Security | C+ *(B+ excl. accepted risk)* | 2 open + 2 accepted |
| F | Dependencies & Tech Currency | B | 3 |
| G | Performance & Scalability | B− | 4 |
| H | Documentation & Onboarding | C+ | 4 |
| I | Developer Experience & Tooling | B | 4 |
| **Overall** | | **B** | **36** |

**Top 5 highest-leverage fixes:** B1, H1, I1, C1, F2

*(E1 and E2 were the original top two. Both were permanently accepted as risk on
2026-08-16 — local device, private network — and are struck through below. Do
not re-raise them in future audits.)*

The engineering culture here is well above the grade. Comments explain *why* and
routinely cite the regression that motivated them (`tools/studio_http.py:88-93`,
`tools/check_loc.py:38-49`, `web/src/main.ts:75-81`); the 500-line cap is
enforced by a check that also warns before you hit it; a seeded cross-language
fuzz keeps three implementations of the cue engine in agreement. Zero `any`,
zero `@ts-ignore`, zero `eslint-disable` across 7,775 lines of TypeScript under
a maximally strict tsconfig is genuinely rare.

The grade is held down by three things that are not about craft: two
unauthenticated remote-code paths (one in the studio server, one in the
firmware), a measured 2-second stall on the most-called endpoint, and a README
that documents the firmware while omitting the cue desk — which is the majority
of the codebase and the thing you actually run.

---

## A — Architecture & Design — A−

The Python layering is acyclic and the seams are real: `studio.py` owns what
endpoints *mean*, `studio_http.py` owns bytes on the socket, `studio_tracks.py`
owns what a track is on disk, and none of them import back up. `web/src/main.ts`
is an honest composition root that stays wiring-only by policy, with the reason
written at the top. One file (`scenes/scenes.yaml`) is the source of truth and
everything else is generated from it, so the previewer and the firmware cannot
drift. The 500-line cap is enforced by `tools/check_loc.py`, which also names
files nearing it so splits happen on a chosen seam.

Held back by a route namespace shared by two different machines, a type
dependency that points the wrong way, and a megabyte of generated output tracked
in git.

#### A1 — `/api/*` means different things on two backends that serve the same bundle
- **Where:** `tools/studio.py:194-296` vs `firmware/sd_web.h:261-337,488`
- **What's wrong:** `POST /api/scene` splices YAML into `scenes.yaml` on the studio, but queues a scene for playback on the castle. `/api/files/`, `/api/tracks`, `/api/status` overlap similarly. The same JS bundle is served by both, and the only thing keeping a call site honest is the `{"studio": true}` probe in `/api/status`. A call site that forgets the probe authors when it meant to play.
- **Fix:** Namespace the studio's authoring routes under `/studio/...` and leave `/api/...` to device semantics. `web/src/api.ts` is already the single doorway for studio calls (device endpoints deliberately live in `device.ts`), so the TS side is a one-file change: rewrite the ~15 paths in `api.ts`. Mirror the prefix in `studio.py`'s three `_get`/`_post`/`_delete` routers, keeping the old paths as aliases for one release so a stale cached bundle doesn't break.
- **Effort:** M
- **Grade lift:** A− → A (removes the sharpest cross-backend trap in the repo)

#### A2 — The API client takes its response types from the panels that consume them
- **Where:** `web/src/api.ts:22-23`
- **What's wrong:** `api.ts` — described in its own header as "the typed doorway to the studio server" — imports `CodecRow` from `codec_ab.ts` and `TrackInfo` from `tracks.ts`. The transport layer depends on the presentation layer for its own wire contract. Both are `import type`, so nothing breaks at runtime, but the dependency direction is inverted on paper and renaming or splitting a panel churns the API client.
- **Fix:** Move `TrackInfo` and `CodecRow` into `web/src/types.ts` (already the leaf everything imports). Re-export them from `tracks.ts`/`codec_ab.ts` so existing call sites are untouched. Two moved interfaces, two added re-export lines.
- **Effort:** S
- **Grade lift:** A− → A− (hygiene, but it makes the "one doorway" claim structurally true)

#### A3 — 1.1 MB of generated output is tracked, and rewritten on every `make preview`
- **Where:** `previewer/castle-cue-desk.html` (1,109,830 bytes, 42 revisions in history); generated by `tools/gen_previewer.py`
- **What's wrong:** The file is base64'd audio (~816 KB) plus the inlined bundle and CSS. It is exempted from the LOC cap as generated, but it is still committed, so every regeneration writes a fresh incompressible megabyte into history. The pack is 11.1 MiB, and roughly all of the growth is this one file.
- **Fix:** Decide which of the two things it is. If it is a build artifact, gitignore it and have `make preview` produce it (CI already regenerates audio from scratch, so nothing depends on it being committed). If it is a shipped portable deliverable — the "single static HTML you can hand to someone" property the previewer was designed for — keep it tracked but regenerate it only on release rather than on every preview, and add a banner comment at the top saying so.
- **Effort:** S
- **Grade lift:** A− → A− (stops unbounded history growth; the decision matters more than the size)

#### A4 — Six files sit within 25 lines of the 500-line cap
- **Where:** `previewer/styles.css` (500), `firmware/sd_web.h` (496), `web/src/stems_view.ts` (489), `firmware/castle.yaml` (488), `web/src/waveform.ts` (477), `web/src/tracks.ts` (462)
- **What's wrong:** `styles.css` passes only because the check is `>` and not `>=`. The next feature in any of these forces a split under time pressure instead of at a chosen seam — which is the exact failure the cap exists to prevent. `check_loc.py` already warns at 450, so the signal is being produced and not acted on.
- **Fix:** Split the two that are about to be touched. `stems_view.ts` divides cleanly into the analysis-fetch/state half and the drawing half. `sd_web.h` splits along the same seam `studio.py` already used: route meaning vs. HTTP mechanics (`reply_json`, `name_from_uri`, the range/upload plumbing) into `sd_web_http.h`. Leave the rest; the warning band will catch them.
- **Effort:** M
- **Grade lift:** A− → A− (prevention — no behaviour change)

---

## B — Backend Quality — B

The error boundary is properly built: `_guarded` turns `BadRequest` into a 400,
swallows `BrokenPipeError`, and converts anything else into a 500 with a
traceback on the console instead of a dead socket (`tools/studio_http.py:102-114`).
Scene writes are atomic with a `.bak` alongside (`studio.py:386-389`), every
subprocess has a timeout with the reasoning for the ceiling written down
(`studio.py:80-89`), job and codec-compare retention are both bounded
(`studio_jobs.py:68-71`, `studio_media.py:110-111`), and the traversal guard is
documented at the site with an explanation of why a redundant second check would
read as protection it isn't providing (`studio.py:169-179`).

The drag is one hot endpoint that does far too much work, and a few unguarded
edges around it.

#### B1 — `/api/tracks` re-decodes every track through ffmpeg on every call
- **Where:** `tools/studio.py:134-139` → `tools/studio_tracks.py:87-115` (the `ana.load_audio(p)` + `ana.analyze(x)` pair at lines 107-114)
- **What's wrong:** `track_info` fully decodes and onset-analyses each track to report duration and onset counts. With 5 tracks in the library this endpoint measured **1.68 s, 2.29 s and 2.66 s** across three consecutive calls on a warm server. It is called on page load, after every import, after every delete, and on the final poll of every background job — and the numbers it computes are already cached in `tracks.json` by the importer.
- **Fix:** Read `dur` and `onsets` from the manifest (`manifest.py`) instead of recomputing. `track_info` already reads the manifest first for `source`/`title`/`opts`. Fall back to decoding only when the manifest lacks the keys — that also fixes it for tracks imported before the fields existed — and write them back when you do. Add a test asserting `/api/tracks` performs no decode when the manifest is complete.
- **Effort:** M
- **Grade lift:** B → B+ (removes the only measured multi-second wait in the app)

#### B2 — `send_range` reads the whole file into RAM to serve a byte range
- **Where:** `tools/studio_http.py:46-55` — `data = p.read_bytes()` before any range arithmetic
- **What's wrong:** The docstring explains that ranges exist so the browser doesn't have to pull 3 MB before seeking to 1:30 — but the server pulls the whole file into memory anyway to answer, then slices. On a `ThreadingHTTPServer` several concurrent audition requests each hold a full copy.
- **Fix:** `p.stat().st_size` for `total`, then open the file, `seek(lo)`, and `read(hi - lo + 1)`. Roughly six lines, and the existing range tests cover the arithmetic unchanged.
- **Effort:** S
- **Grade lift:** B → B+ (makes the range path actually do what its docstring promises)

#### B3 — The three-generator rebuild is duplicated verbatim in two routes
- **Where:** `tools/studio.py:290-292` (`/api/rebuild`) and `tools/studio.py:391-393` (`do_scene`)
- **What's wrong:** The same three `run([PY, ...])` calls for `render_audio.py`, `gen_esphome.py`, `gen_previewer.py`, with the same `ok1 and ok2 and ok3` reduction and the same `[-4000:]` log tail, written out twice. A fourth generator, or a change to the log budget, has to be made in both places or the two paths silently diverge.
- **Fix:** Extract `def rebuild() -> tuple[bool, str]` next to `run()`, returning the combined ok and the trimmed log. Both call sites become one line. No behaviour change.
- **Effort:** S
- **Grade lift:** B → B (removes the largest duplication in the server)

#### B4 — `X-Import-Opts` is parsed with a raw `json.loads`
- **Where:** `tools/studio.py:321`
- **What's wrong:** Every other body in the server goes through `json_body`, which exists precisely so malformed input becomes a 400 with a sentence instead of a traceback (`studio_http.py:88-100`). The multipart upload path bypasses it: a bad `X-Import-Opts` header raises `JSONDecodeError`, hits the generic handler in `_guarded`, and the browser is told the *upload* failed with a 500. It also isn't checked for being a dict, so a bare `[1,2]` reaches `req.get` and raises `AttributeError`.
- **Fix:** Route it through the same helper: `req = self.json_body((self.headers.get("X-Import-Opts") or "{}").encode())`. That gets both the 400 and the dict check for free. Add one test posting a multipart upload with a junk header and asserting 400.
- **Effort:** S
- **Grade lift:** B → B (closes the last unguarded parse in the server)

#### B5 — Scene YAML is shape-checked but not schema-validated
- **Where:** `tools/studio.py:342-365`
- **What's wrong:** `do_scene` verifies the block parses, is a one-element list, and that the id matches — which is genuinely the important half, and it prevented the corruption class it was written for. But nothing checks the *contents*: an unknown effect name, a cue past the scene length, a missing `audio_file`, or a negative volume all splice cleanly into `scenes.yaml` and fail later inside `render_audio.py` or `gen_esphome.py`, where the error surfaces as a subprocess log tail rather than a pointed message. `gen_esphome.py` already knows the effect vocabulary (`KNOWN_EFFECTS`).
- **Fix:** Add `validate_scene(dict) -> list[str]` in a shared module (`tools/manifest.py` is the wrong home — make it `tools/scene_schema.py`), checking effect names against the shared `KNOWN_EFFECTS` set, cue times within `length`, required keys present, and numeric ranges. Call it in `do_scene` before the splice and return the messages as a 400. Reuse it in `gen_esphome.py` so the CLI path gets the same errors.
- **Effort:** M
- **Grade lift:** B → B+ (moves a whole class of failure from "subprocess log" to "the field that's wrong")

---

## C — Frontend Quality (code only) — B+

*Scoped per the request: structure, typing, state and correctness. Styling,
layout, widget design and a11y were not assessed.*

`web/tsconfig.json` runs `strict` plus `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`, `noImplicitOverride`, `noFallthroughCasesInSwitch`,
`noUnusedLocals` and `noUnusedParameters` — and across 36 modules and 7,775
lines there are **zero** occurrences of `any`, `as any`, `@ts-ignore`,
`@ts-expect-error` or `eslint-disable`. The strictness is real rather than
configured-and-suppressed, which is the distinction that usually decides this
grade. `api.ts` gives every server call one typed doorway with a per-kind
timeout and an explicit convention (application failures returned, transport
failures thrown) written into the header. The nullable `players` holder in
`main.ts:75-83` is a small masterclass: the TDZ hazard is named, the reason the
previous spelling was unsafe is recorded, and the fix makes the compiler enforce
it.

What's left is state that has outgrown its representation, and a couple of
modules pressing the size cap.

#### C1 — The audition/preview state in `main.ts` is six interacting module-level flags
- **Where:** `web/src/main.ts:46` (`audioMode`), `:83` (`players`), `:116` (`adopting`), `:211-212` (`previewScene`, `sceneBeforeAudition`), `:225` (`selectedTrack`)
- **What's wrong:** These encode a real state machine — stopped / playing a scene / auditioning a clip / previewing a row / A-B'ing codecs, with "what to restore when the audition ends" carried in `sceneBeforeAudition`. It is spread across six mutable bindings whose legal combinations exist only in the reader's head. The `onAudition` callback (`:249-267`) already has to check `sceneBeforeAudition` for both null-ness and truthiness to decide whether this is a start or an adopt, and the comment above `adopting` documents a feedback loop that the flag exists to break. This is the file most likely to grow a bug during the UI rewrite.
- **Fix:** Model it explicitly: `type DeskMode = {kind:"scene"} | {kind:"audition", restore:Scene, preview:Scene} | {kind:"rowPreview"} | {kind:"codec"}` in a small `desk_mode.ts` with a `transition(mode, event)` function, unit-tested the way `show.ts` is. `main.ts` keeps one `let mode: DeskMode`. This also gives the rewrite a tested contract to build against rather than six flags to re-derive.
- **Effort:** M
- **Grade lift:** B+ → A− (turns the least-safe part of the frontend into the tested part)

#### C2 — `main.ts` drives other modules' DOM by selector instead of by method
- **Where:** `web/src/main.ts:122` (`document.querySelector('.scene[data-i="..."]')?.click()`), `:189-190` (same pattern in the key handler)
- **What's wrong:** `Panels` owns the scene buttons and their markup, but `main.ts` reaches past it to synthesise a click — so the `data-i` attribute and the `.scene` class are now a load-bearing contract between two files with nothing declaring it. `panels.renderScenes` already receives the selection callback, so the plumbing to do this properly exists.
- **Fix:** Add `panels.selectScene(i: number)` that does the click (or, better, invokes the same callback directly) and call that from both sites. The device-adoption path in `deviceBridge` keeps working unchanged.
- **Effort:** S
- **Grade lift:** B+ → B+ (removes the last cross-module DOM reach-around)

#### C3 — 58 raw DOM lookups with no shared typed accessor
- **Where:** `getElementById` in 9 modules (28 calls; `main.ts` 8, `import_opts.ts` 7, `transport.ts` 4), `querySelector` in 6 modules (30 calls; `device_panel.ts` 13, `zone_designer.ts` 6)
- **What's wrong:** Each call site re-does the same cast-and-null-check dance, e.g. `(document.getElementById(id) as HTMLInputElement | null)?.value.trim() ?? ""` inline at `main.ts:234`. `noUncheckedIndexedAccess` and strict null checks mean none of these can silently explode — the compiler forces a guard — so this is verbosity and inconsistency rather than a correctness hole. But the same expression appears in several shapes across the codebase.
- **Fix:** One leaf `dom.ts` exporting `input(id): HTMLInputElement | null`, `el<T>(id): T | null` and `req<T>(id): T` (throws with the id in the message, for the handful that are genuinely required at boot). Migrate opportunistically during the UI rewrite rather than in one sweep.
- **Effort:** S
- **Grade lift:** B+ → B+ (consistency; low urgency given the rewrite)

#### C4 — Three modules are within 40 lines of the cap
- **Where:** `web/src/stems_view.ts` (489), `web/src/waveform.ts` (477), `web/src/tracks.ts` (462)
- **What's wrong:** Same issue as A4, called out separately because these three are the ones the UI rewrite will touch first, and a split forced mid-rewrite is a split made badly.
- **Fix:** Split `stems_view.ts` before the rewrite starts, along the fetch/state vs. draw seam. Defer the other two — the rewrite may dissolve them anyway.
- **Effort:** S
- **Grade lift:** B+ → B+ (prevention, timed to the rewrite)

---

## D — Testing & Reliability — B

397 Python tests pass in 17.9 s. The quality is high: `tests/test_studio_api.py`
drives a **real** server on an ephemeral port rather than mocking the handler,
`tests/studio_case.py` sandboxes both `CASTLE_TRACKS` and `CASTLE_SCENES` so a
test run cannot touch the real show, `test_generator_parity.py` runs a seeded
cross-language fuzz through the real generators to keep the Python, TypeScript
and firmware cue engines in agreement, and CI gates lint, types, unit tests, the
LOC cap, the node engine suites, Playwright, and `esphome config` on all four
buildable variants.

What's missing is knowing what *isn't* covered, and two bodies of code that
aren't.

#### D1 — No coverage measurement anywhere [both]
- **Where:** absent from `Makefile:85-98`, `pyproject.toml`, `.github/workflows/ci.yml`; `.coverage` appears in `.gitignore` but nothing produces it
- **What's wrong:** 397 tests is a count, not a coverage figure. With no measurement, the honest answer to "is the new stems pipeline tested as well as the synth is?" is nobody knows — and the gaps in D2/D3 below were found by reading imports, which is not a method that scales.
- **Fix:** Add `coverage` to `requirements-dev.txt` and a `make coverage` target running `coverage run -m unittest discover -s tests -q && coverage report --include='tools/*'`. Print the total in CI without gating on it at first; once you know the real number, set a floor slightly below it so it can only go up.
- **Effort:** S
- **Grade lift:** B → B+ (makes every other testing decision informed rather than guessed)

#### D2 — The device-network layer has no tests [BE]
- **Where:** `tools/device.py` (110 lines), `tools/sd_sync.py` (199 lines), `tools/hosts.py` (65 lines) — none appear in any `tests/*.py` import
- **What's wrong:** ~370 lines of code that talks to the castle over the network and syncs files to its SD card, with no test at any level. This is also the code most likely to be run in a hurry on Halloween night, and the code whose failure mode is a castle that needs physical access to recover.
- **Fix:** `sd_sync.py`'s manifest-diff logic (what to upload, what to skip, what to delete) is pure and testable without a device — start there with a fake listing. For `device.py`, test the request-building and response-parsing against recorded fixtures; leave the socket itself untested. Aim for the decisions, not the I/O.
- **Effort:** M
- **Grade lift:** B → B+ (covers the code with the worst failure mode)

#### D3 — 3,036 lines of firmware C++ are validated only by config parse [both]
- **Where:** `firmware/*.h`, notably `sd_web.h` (496 lines, including the OTA handler at `:340-400`), `sd_audio.h` (341), `castle_eink.h` (327), `castle_effects.h` (210)
- **What's wrong:** CI runs `esphome config` on four variants, which catches a missing id or a bad package merge — and the workflow comment is honest that this is the intended trade. But it executes none of the C++. The OTA handler writes to a flash partition and reboots the device; a mistake there is the "physical access to recover" scenario the OTA path exists to avoid. `castle_effects.h` is partly covered indirectly by the fuzz parity suite, which is the right idea applied to one file.
- **Fix:** Extend the parity approach: compile the pure functions in `castle_effects.h` and the range/name-parsing helpers in `sd_web.h` (`name_from_uri`) as a host binary in CI and assert against the same fixtures the Python side uses. That's a `g++` step and a small test main, no ESP toolchain required. The OTA write path itself stays untested — that's reasonable — but its *input validation* (`:349-350`, the implausible-size check) should not be.
- **Effort:** M
- **Grade lift:** B → B+ (covers the highest-consequence code that is cheaply coverable)

#### D4 — Test output is noisy enough to train you to ignore it [both]
- **Where:** run `make test` — the tail includes `FAIL — over 97% of the slot.`, `WARNING: tracks.json was not valid JSON — moved to tracks.json.corrupt-…` (×3), and `WARNING: could not parse …/no/such.yaml` after the `OK` line
- **What's wrong:** These come from tests deliberately exercising failure paths, so they are correct — but a run that prints `FAIL` and three `WARNING`s and then says `OK` teaches you not to read the output. The `no/such.yaml` warning prints *after* the summary, so it looks like a post-run error.
- **Fix:** Capture stdout/stderr in the tests that exercise those paths (`contextlib.redirect_stdout` into an `io.StringIO`, which several tests already import) and assert on the captured text instead of letting it through. That converts noise into an actual assertion.
- **Effort:** S
- **Grade lift:** B → B (restores signal to the one output you read every time)

---

## E — Security — C+ as written, B+ excluding accepted risk

> **Two of the four items below (E1, E2) were permanently accepted as risk by the
> owner on 2026-08-16:** this is a local device on a private home network, and the
> threat model the findings assume does not apply. They are struck through and
> must not be re-raised. The C+ describes the code as it stands; **B+** describes
> the posture the owner actually chose, and is the fairer number to carry forward.


The guards that exist are well built and well explained. Path traversal is
stopped by `Path(...).name` on every serving route, with a comment explaining why
a redundant parent check would misrepresent where the protection lives
(`studio.py:169-179`); `safe_id` is its write-side twin (`studio.py:92-100`).
There is **no** `shell=True`, `os.system`, `eval` or `exec` anywhere in `tools/`
— every subprocess takes a list. YAML is always `safe_load`. Import URLs are
scheme-allowlisted to http(s). `npm audit` reports 0 vulnerabilities. The
`--lan` flag is opt-in and prints a blunt warning.

The grade is set by two paths that let someone else run code, both verified
against the running server and the firmware source.

#### ~~E1~~ — ACCEPTED RISK 2026-08-16, will not fix — The studio server validates neither `Origin` nor `Host`
> **Owner's decision (2026-08-16): permanently accepted, do not re-raise.** This
> is a local device on a private home network. The threat model that makes this
> item matter — a hostile page in another tab, or an untrusted party on the LAN —
> is not the one this project operates under. Recorded here so future audits skip
> it rather than rediscovering it.

- **Where:** `tools/studio.py:103-296` (no header check in any route); `tools/studio_http.py` (none in the transport layer either) — confirmed by grep across both files
- **What's wrong:** Verified against the live server: `curl -H "Host: attacker.example" http://127.0.0.1:8765/api/tracks` returns **200**, and a cross-origin `multipart/form-data` POST to `/api/import` reaches the handler (it returned 400 for an empty body — a content complaint, not a rejection). `multipart/form-data` is a CORS-simple content type, so no preflight protects it, and `/api/rebuild`, `/api/server/stop` and `/api/server/restart` take a POST with no body at all. The consequence: any website the user has open can silently drive this server — importing from a URL of its choosing (which runs yt-dlp and ffmpeg), triggering rebuilds, deleting tracks, or stopping the server. With no `Host` check, DNS rebinding also defeats the 127.0.0.1 bind, and `--lan` removes even that.
- **Fix:** One check in `JsonHandler`, before dispatch: reject any request whose `Host` header is not `127.0.0.1:<port>`, `localhost:<port>`, or (under `--lan`) the LAN IP; and for every state-changing method (POST/DELETE), reject when `Origin` is present and is not the server's own origin. Both are a handful of lines in `studio_http.py` and apply to all routes at once. Add two tests — a bad `Host` and a foreign `Origin` — to `test_studio_api.py`, which already drives a real server.
- **Effort:** S
- **Grade lift:** C+ → B (closes the drive-by path to local code execution)

#### ~~E2~~ — ACCEPTED RISK 2026-08-16, will not fix — The firmware's OTA and file endpoints have no authentication
> **Owner's decision (2026-08-16): permanently accepted, do not re-raise.** Same
> reasoning as E1 — private home network, single operator, and the affected
> variant is the experimental SD build. Recorded here so future audits skip it.

- **Where:** `firmware/sd_web.h:488` (`reg("/api/ota", HTTP_PUT, h_ota)`), handler at `:340-400`; file upload/delete at `:261-285`; no `Authorization`, `httpd_req_get_hdr` or auth helper appears anywhere in `firmware/*.h`
- **What's wrong:** `PUT /api/ota` accepts a firmware image over plain HTTP and writes it straight into the inactive OTA slot, then reboots — the header comment says so plainly ("any browser or curl can deliver a .bin"). The only validation is a size plausibility check at `:349-350`. `/api/files/` allows upload and delete on the SD card. Anyone on the WiFi can replace the castle's firmware. Note the ESPHome OTA component in `castle.yaml:168-173` *is* password-protected — this is a second, parallel update path that isn't. This is the SD variant, documented as experimental, which is the mitigating factor; it is not the flash build.
- **Fix:** Add a shared-secret check at the top of `h_ota` and the file handlers: read an `X-Castle-Key` header via `httpd_req_get_hdr` and compare against a `!secret` substituted into the build, rejecting with 401 otherwise. Roughly 15 lines plus a substitution. Cheaper alternative if you'd rather not carry the secret: gate registration of `/api/ota` and the file routes behind a build flag that is off by default, so the experimental variant has to be deliberately armed.
- **Effort:** S
- **Grade lift:** C+ → B (removes unauthenticated firmware replacement over the LAN)

#### E3 — `/api/import` will fetch any http(s) URL on the host's behalf
- **Where:** `tools/studio.py:202-203, 306-310` (scheme allowlist only), then `tools/import_track.py` via yt-dlp
- **What's wrong:** The scheme check stops `file://` and `gopher://`, which is the important half. What remains is that the server will fetch arbitrary internal addresses — `http://192.168.1.1/…`, `http://169.254.169.254/…` — and surface the result in the import log. On a single-user Mac this is close to theoretical; it becomes real under `--lan`, where the requester need not be the operator. It is also the natural companion to E1: a drive-by page picks the URL.
- **Fix:** After E1 is in place this is largely mooted for the local case. If you want it closed properly, resolve the hostname before handing it to yt-dlp and reject loopback, link-local, and RFC1918 targets unless an explicit `--allow-private` flag is passed.
- **Effort:** S
- **Grade lift:** C+ → C+ (defence in depth; do it after E1)

#### E4 — No Python dependency lockfile
- **Where:** `requirements.txt` (`~=` ranges for numpy, scipy, PyYAML, segno, aioesphomeapi, Pillow), `requirements-dev.txt`; no `uv.lock`, `poetry.lock` or `requirements.lock`
- **What's wrong:** `esphome` is pinned exactly, with a comment explaining that CI validates against that version — the reasoning is right and applied to one dependency. Everything else floats within a compatible range, so `make setup` today and `make setup` next month install different transitive trees, and a compromised or broken point release lands without a diff to review. The npm half has `package-lock.json`; the Python half has nothing equivalent.
- **Fix:** `pip freeze > requirements.lock` after a known-good `make setup`, install from it in CI, and keep `requirements.txt` as the human-readable direct-dependency list. Dependabot is already configured for pip (`.github/dependabot.yml`) and will raise the updates as reviewable PRs.
- **Effort:** S
- **Grade lift:** C+ → C+ (supply-chain reviewability, matching what npm already has)

---

## F — Dependencies & Tech Currency — B

`requirements.txt` is a genuinely good manifest: it lists direct dependencies
only, explains *why* each is named rather than inherited (aioesphomeapi and
Pillow used to arrive transitively through esphome and would have broken
silently), and pins esphome to the version CI validates against.
`.github/dependabot.yml` covers pip, npm and github-actions weekly with
minor/patch grouping. `npm audit` is clean, including dev dependencies. Nothing
is abandoned or EOL.

#### F1 — `make setup` names a Python interpreter that doesn't exist on this machine
- **Where:** `Makefile:29` — `/opt/homebrew/bin/python3.13 -m venv .venv`
- **What's wrong:** Homebrew's python3.13 is gone; `/opt/homebrew/bin/` has `python3.14`. The working 3.13 is the python.org framework build at `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`. `make setup` therefore fails outright on a clean checkout of this machine — verified this session, where the venv had to be sourced from the main checkout instead. It also hardcodes an Apple-Silicon-Homebrew path, so it cannot work on CI or any other machine.
- **Fix:** Try candidates in order and fail with a useful message: `PY313 := $(shell command -v python3.13 || echo /Library/Frameworks/Python.framework/Versions/3.13/bin/python3)`, with a guard that errors telling the user to install 3.13 if neither resolves. CI already uses `actions/setup-python`, so this only affects local setup.
- **Effort:** S
- **Grade lift:** B → B+ (the documented first step currently doesn't run)

#### F2 — TypeScript is a major version behind; esbuild three minors
- **Where:** `web/package.json` devDependencies — typescript 5.9.3 (latest 7.0.2), esbuild 0.25.12 (latest 0.28.2)
- **What's wrong:** Not urgent — no CVEs, and the code compiles clean under the strictest settings 5.9 offers. But TS 7 is a major with real changes, and the gap widens the longer it sits. Doing it *before* the UI rewrite means the rewrite is written against the compiler you'll keep; doing it after means touching every new file twice.
- **Fix:** Bump esbuild first (low risk, `npm test` and the e2e suite are the check). Then TypeScript on its own branch: `npx tsc --noEmit` will list everything the new checker objects to, and with zero suppressions in the codebase the output is trustworthy. Dependabot will otherwise raise these itself.
- **Effort:** S
- **Grade lift:** B → B+ (best done before the rewrite, not during)

#### F3 — Dependabot has no reviewer and CI is the only gate
- **Where:** `.github/dependabot.yml`
- **What's wrong:** Weekly grouped PRs with a limit of 5 per ecosystem, on a single-developer repo, with no assignee or reviewer configured. The likely outcome is a queue of open PRs nobody looks at, which is the same as not having it — plus noise.
- **Fix:** Add `assignees: [jtn0123]` to each ecosystem, and consider `interval: monthly` for pip given how stable the set is. Keep npm weekly.
- **Effort:** S
- **Grade lift:** B → B (makes the automation land somewhere)

---

## G — Performance & Scalability — B−

This is a single-user local tool, so most of what would be a scalability finding
elsewhere is correctly a non-issue here — and the code says so where it matters
(jobs serialised under one lock with the reason written down). Graded on what
the operator actually waits for. One endpoint is measurably slow, and the frame
loop does full work regardless of whether anything is moving.

#### G1 — See B1: `/api/tracks` costs 1.7–2.7 s per call
- **Where:** `tools/studio_tracks.py:107-114`
- **What's wrong:** Cross-listed because it is the only measured user-visible wait in the app, and it grows linearly with library size — at 23 tracks (the main checkout's library) this endpoint would take roughly 8-12 s.
- **Fix:** As B1 — read the cached values from the manifest.
- **Effort:** M
- **Grade lift:** B− → B (the single biggest latency win available)

#### G2 — The frame loop runs at full cost when nothing is playing
- **Where:** `web/src/main.ts:279-296`
- **What's wrong:** `frame()` unconditionally calls `stage.draw`, `insets.draw`, `panels.updateMeters`, `wave.mirror` and `panels.updateTransport` every animation frame, then re-schedules — whether the transport is running, the tab is idle, or the scene is blacked out. On a laptop that's a constant GPU/CPU floor for a stopped desk; on the porch tablet in kiosk mode it's battery.
- **Fix:** Keep the rAF loop (the flicker is time-driven and must keep moving while playing), but early-out to a cheap path when `!state.running` and no audition is active — redraw once on state change rather than continuously. Guard with `document.hidden` too.
- **Effort:** S
- **Grade lift:** B− → B (removes the idle cost entirely)

#### G3 — The served page is ~1.1 MB before a single track loads
- **Where:** `previewer/castle-cue-desk.html` (1,109,830 bytes) — ~816 KB base64 audio + 188 KB unminified bundle + inlined CSS; `web/package.json` build script has no `--minify`
- **What's wrong:** Every page load and every `/api/server/restart` re-transfers it. Over localhost this is fast; over `--lan` to a phone it is the difference between instant and a visible wait, which is exactly the use case `--lan` exists for. The audio is inlined deliberately (so the previewer plays exactly what ships, and stays portable as one file) — that trade is sound and worth keeping.
- **Fix:** Add `--minify` to the esbuild build (bundle drops roughly 40-50%; source maps are not needed for a tool you debug from source). Leave the audio inlined. If the phone case gets more use, add a `?lean=1` mode that fetches the audio from `/api/track/` instead of inlining it.
- **Effort:** S
- **Grade lift:** B− → B− (meaningful only for the LAN/phone path)

#### G4 — `send_range` holds a full file copy per concurrent request
- **Where:** `tools/studio_http.py:55`
- **What's wrong:** Cross-listed from B2 for the memory dimension: `ThreadingHTTPServer` plus whole-file reads means N concurrent auditions hold N full copies. With a 4-minute import at ~3 MB it's bounded and small, so this is real but minor.
- **Fix:** As B2 — seek and read the slice.
- **Effort:** S
- **Grade lift:** B− → B− (bounded by track size; fix comes free with B2)

---

## H — Documentation & Onboarding — C+

The *inline* documentation is the best thing in this repo and would carry an A on
its own. Comments consistently explain why rather than what, and cite the
specific regression that motivated the code: `studio_http.py:88-93` records that
`json.loads` used to kill the socket and how the browser mis-reported it;
`check_loc.py:38-49` explains why `git ls-files` alone measured nothing;
`main.ts:75-81` names the esbuild hoisting behaviour that made the previous
spelling unsafe. `PROJECT_NOTES.md` is 61 KB of real design record, and the
README's hardware section explains the non-obvious pin choices (why not D5/D6/D10)
better than most commercial documentation.

The grade is set by what a new reader is told about the project they'd actually
run. The README describes a firmware project. The cue desk — 7,775 lines of
TypeScript, 4,796 of Python, the studio server, the entire thing this session
started and drove — appears in it once, as a box in an ASCII diagram.

#### H1 — The README omits the browser half of the project
- **Where:** `README.md:76-88` ("Getting started"), which lists only `make setup`, `audio`, `validate`, `build`, `upload`
- **What's wrong:** `make studio` — the command that starts the app — is not in the README. Neither is `make preview`, `make track`, `make test`, `make check`, or `make e2e`. There is no mention of `web/`, TypeScript, the studio server, the track library, the stems pipeline, or the fact that a browser is involved at all. `make help` knows all of this; the README doesn't. Someone handed this repo would build firmware and never discover the tool that is most of the code.
- **Fix:** Add a "The cue desk" section after "How it fits together": what it is, `make studio` → http://127.0.0.1:8765, what the Tracks panel does, and the `CASTLE_TRACKS`/`CASTLE_SCENES` sandbox variables. Then a "Development" section with `make test` / `make check` / `make e2e` and what each gates. The content already exists in `make help` and the module docstrings — this is assembly, not authorship.
- **Effort:** S
- **Grade lift:** C+ → B (the single largest gap between what the repo is and what it says it is)

#### H2 — The documented first step doesn't run
- **Where:** `README.md:78` (`make setup`) → `Makefile:29`
- **What's wrong:** As F1 — the interpreter path is stale, so the first command in "Getting started" fails on this machine. The README also says "Copy `firmware/secrets.yaml` and set real WiFi credentials" but there is no template to copy from (`secrets.yaml` is gitignored and no `.example` exists).
- **Fix:** Fix the Makefile per F1, and add a tracked `firmware/secrets.yaml.example` with placeholder keys so "copy" is a real instruction.
- **Effort:** S
- **Grade lift:** C+ → B− (an onboarding path that works end to end)

#### H3 — Nothing explains the parity contract, which is the project's best idea
- **Where:** the mechanism spans `tools/pulse_dynamics.py`, `tools/gen_esphome.py`, `web/src/track_lights.ts`, `firmware/castle_effects.h`, `tests/test_generator_parity.py`, `web/test/fuzz_parity.mjs`; `docs/` contains only `ROADMAP.md`
- **What's wrong:** Three independent implementations of the cue engine are kept in agreement by a seeded cross-language fuzz. That's the most interesting engineering in the repo and the thing most likely to be accidentally broken by someone who doesn't know it exists — including the UI rewrite, which will touch `track_lights.ts`. The rounding rules are documented at their call sites, but nothing describes the contract as a whole.
- **Fix:** `docs/PARITY.md`: which three implementations, why three, what the fuzz actually asserts, how to run it, and what to do when it fails. One page. Link it from the README and from the top of `test_generator_parity.py`.
- **Effort:** S
- **Grade lift:** C+ → B− (protects the highest-value invariant through the rewrite)

#### H4 — No dev-loop or contribution doc
- **Where:** absent — no `CONTRIBUTING.md`, no dev section in the README
- **What's wrong:** Non-obvious things a second person (or you in six months) would need: the 500-line cap and that `check_loc.py` enforces it, that the pre-commit hook must be installed by hand, that `.venv/bin/*` paths mean a git worktree needs its own venv, that `scenes.yaml` is the source of truth and generated files must never be hand-edited, that e2e needs `npx playwright install chromium`. All true today, none written down.
- **Fix:** A `docs/DEVELOPING.md` covering exactly that list, linked from the README. The worktree note in particular cost this session real time.
- **Effort:** S
- **Grade lift:** C+ → B− (turns tribal knowledge into a page)

---

## I — Developer Experience & Tooling — B

The gates are real and well chosen. `pyproject.toml` selects ruff rule families
deliberately and explains the omissions — style-only packs were left out on the
grounds that they'd bury the signal the linter exists to surface (silent
subprocess failures, mutable defaults, dead noqa). mypy runs over `tools` and
`tests` with per-module ignores only where stubs genuinely don't ship. CI has
three jobs covering Python, web and ESPHome, and its comments explain the
trade-offs (why a full firmware compile is deliberately absent, why stdout must
not be piped away). `make help` is genuinely useful. `check_loc.py` warns before
the cap, not just at it.

The friction is in installation and repeatability rather than configuration.

#### I1 — Every tool path is hardcoded to `.venv/bin/*`, which breaks in a git worktree
- **Where:** `Makefile:1-2`, `githooks/pre-commit:5-8`, `tools/studio.py:66` (`PY = str(ROOT / ".venv" / "bin" / "python")`), `web/playwright.config.ts` (webServer command)
- **What's wrong:** `.venv/` is gitignored, so a `git worktree add` produces a tree where every make target, the pre-commit hook, the studio server's subprocess launcher, and the Playwright webServer all point at a path that doesn't exist. Verified this session: starting the app in a worktree required symlinking `.venv` and `web/node_modules` from the main checkout first. Given `.claude/worktrees/` is in active use, this is a recurring tax.
- **Fix:** Resolve the interpreter once, with a fallback: in the Makefile, `PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)`; in `studio.py`, prefer `sys.executable` over the constructed path (the server is already running under the right interpreter, so this is strictly more correct). Add a `make setup-worktree` that creates the symlinks if you'd rather share one venv.
- **Effort:** S
- **Grade lift:** B → B+ (removes a per-worktree manual step)

#### I2 — The pre-commit hook exists but nothing installs it
- **Where:** `githooks/pre-commit` — the install command is a comment on line 3 (`git config core.hooksPath githooks`); `make setup` (`Makefile:28-32`) doesn't run it
- **What's wrong:** A hook that must be installed by hand, documented only inside itself, is a hook that is not installed. It also fails immediately in a worktree per I1, since its first line is `.venv/bin/ruff`.
- **Fix:** Add `git config core.hooksPath githooks` to the `setup` target and mention it in the README's development section. Fix the interpreter paths per I1 so it survives a worktree.
- **Effort:** S
- **Grade lift:** B → B+ (makes the fast gates actually gate)

#### I3 — No coverage in the local loop or CI
- **Where:** as D1
- **What's wrong:** Cross-listed for the tooling dimension — there is no `make coverage`, so the question can't even be asked locally without ad-hoc commands.
- **Fix:** As D1.
- **Effort:** S
- **Grade lift:** B → B (see D1)

#### I4 — 51 `noqa: E402` comments, all the same shape
- **Where:** every file in `tests/` (`tests/test_import.py` has 8, `tests/studio_case.py` 5, `tests/test_studio_api.py` 4)
- **What's wrong:** The pattern is `sys.path.insert(0, ROOT/"tools")` followed by imports that must come after it, each needing its own suppression. It is legitimate and unavoidable given `tools/` is a directory of scripts rather than a package — but it's 51 lines of noise, and it means a genuinely misplaced import in a test would blend in. The remaining suppressions in `tools/` are three, each with a written reason (`F401 (re-export)`, `E731`, `DTZ005`) — that's the standard the tests aren't meeting.
- **Fix:** One line in `pyproject.toml`: `[tool.ruff.lint.per-file-ignores]` with `"tests/*" = ["E402"]` and a comment explaining the `sys.path` bootstrap. Then delete all 51. Alternatively add a `tests/conftest`-equivalent — but with unittest rather than pytest, the per-file-ignore is the honest fix.
- **Effort:** S
- **Grade lift:** B → B (restores the "every suppression has a reason" standard to the test suite)

---

## Verification notes

Findings in this report were checked against the running system rather than
inferred, where checkable:

- `make test` — 397 tests, `OK`, 17.9 s
- `/api/tracks` latency — 3 consecutive `curl` calls against the live server: 2.29 s, 1.68 s, 2.66 s (5-track library)
- `Host: attacker.example` → HTTP 200; cross-origin multipart POST to `/api/import` → reaches handler (400 on empty body)
- `npm audit` → 0 vulnerabilities; `npm outdated` → esbuild and typescript as listed
- `grep` for `any` / `@ts-ignore` / `eslint-disable` across `web/src/` → 0 hits
- `grep` for `shell=True` / `os.system` / `eval(` / `exec(` / `yaml.load(` across `tools/` → 0 hits
- `tools/check_loc.py --list` for the cap figures
- `git log --oneline -- previewer/castle-cue-desk.html | wc -l` → 42; `git count-objects -vH` → 11.10 MiB
- `make setup`'s interpreter path — confirmed absent on this machine
