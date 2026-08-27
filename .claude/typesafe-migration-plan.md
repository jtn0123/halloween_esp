# Type-safe migration plan (Rust + TypeScript)

Goal: the vast majority of hand-written logic in Rust or TS before the polish/debug
phase. Python stays only where its ecosystem is the point (mypy --strict keeps it
honest). Drafted 2026-08-26.

## Phase 1 — TypeScript now (pre-Halloween safe)

| Files | Move | Effort |
|---|---|---|
| `web/test/*.mjs` (16 files, ~3.0k lines) | → `.ts` under existing tsc `--noEmit` gate | S |
| `tools/gen_previewer.py` inline JS snippets (if any) | fold into `web/src/` | S |

## Phase 2 — `castle-core` Rust crate (off-season, biggest payoff)

One crate, three faces: WASM (desk), CLI (Python generators call it), native later.
Each row DELETES a parity pair from docs/PARITY.md.

| Today (duplicated) | Becomes |
|---|---|
| `web/src/effects.ts` ↔ `firmware/castle_effects.h` (+ shared int hash) | `core/effects.rs` |
| `tools/pulse_dynamics.py` + `tools/pulse_expand.py` ↔ `web/src/track_lights.ts` | `core/pulse.rs` |
| `tools/castle_fuzz.py` + `tools/fuzz_check.py` + `web/test/fuzz_parity.mjs` | shrink to one Rust-vs-WASM self-check |

## Phase 3 — Rust DSP (optional, after Phase 2 proves the toolchain)

| Today | Becomes | Note |
|---|---|---|
| `tools/synth.py` ↔ previewer Web Audio graph | `core/synth.rs` → WASM + offline render | desk previews the literal device samples |
| `tools/analyze.py` (3-band spectral flux) | `core/onsets.rs` | single impl today — perf/rigor only, do last |

## Phase 4 — eventually portable (verified 2026-08-26: all heavy deps are subprocesses)

demucs, lame, ffmpeg, yt-dlp are invoked via subprocess — a Rust binary can spawn
them identically. So these are portable when the payoff justifies it, in this order:

| Today | Becomes | Why / unlock |
|---|---|---|
| `tools/castle_native.py` (aioesphomeapi) | `esphome-native-api` crate | direct crate swap; verify it covers buttons + media_player + text sensors first |
| `tools/castle_link.py` + `hosts.py` + `sd_sync.py` | one Rust device-bridge CLI | typed wire protocol for the flakiest surface (the S2's network) |
| `tools/studio*.py` (~2.5k lines: server, routes, jobs, publish, media) | axum server spawning the same venv tools | biggest chunk; do last, after the bridge proves the crates |
| `tools/render_audio.py` mixing | joins `core/synth.rs` | falls out of Phase 3 naturally |

## Stays Python (mypy --strict; low payoff or contract-bound)

`castle_emu*` (the firmware contract test parses the C against this Python by
design — porting it means rewriting that harness), `gen_esphome/gen_previewer`
emission (YAML/HTML templating, ESPHome-semantics-bound, low payoff),
`import_track/import_fetch` + `stems.py` (thin subprocess wrappers; portable in
principle, nothing to gain), `netguard`, `check_loc`, wiring/QR/eink generators.

## Stays C++ (thin, shrinking)

`firmware/sd_web.h` + ESPHome glue — runs inside esp_http_server; linking Rust into
the ESPHome build is not worth the toolchain entanglement. `castle_effects.h` body
shrinks to a shim over `castle-core` if/when native linking ever happens.

## Python ratchet — hardening what stays (runs alongside the phases)

Current baseline: ruff (curated bug-focused ruleset) + mypy at the "cheap half of
strictness" (check_untyped_defs / warn_return_any / warn_unused_ignores); no
formatter enforced. The responsible ratchet, in order:

1. **Formatter, now (safe pre-Halloween).** `ruff format` — black's formatting
   without a new dependency, since ruff is already installed. One mechanical
   reformat commit, then `ruff format --check` added to `make lint` and the
   pre-commit hook. Do NOT add black itself: two formatters fight.
2. **Strict mypy, per module, stays-list first.** Add `disallow_untyped_defs`
   (then full `strict = true`) via [[tool.mypy.overrides]] one module at a time —
   starting with the code that stays Python forever (castle_emu*, gen_*,
   importers, utilities + their tests). Deliberately SKIP the Phase 2–4 code
   (studio, bridge, synth/analyze): annotating code slated for Rust deletion is
   wasted work. End state: global strict = true once the port empties the
   exception list.
3. **Typed boundaries, not new deps.** scenes.yaml / tracks.json shapes become
   TypedDicts (scene_schema.py already validates; give the loaders typed returns
   so dict[str, Any] stops spreading). No pydantic — stdlib-flavored repo stays
   that way.
4. **Every ratchet lands gated.** Each step ships in the same commit that turns
   it on in `make lint`/CI — never a warnings-allowed transition period.

## LOC forecast (measured 2026-08-26, all phases done)

Moves to Rust: ~4.8k Python app code (P2 914 / P3 1,097 / P4 2,779) + ~6.5k of the
Python tests that cover it + castle_effects.h 269 + tests/cxx 543 + ~0.5–1k TS
(effects.ts/track_lights.ts become WASM bindings). Moves to TS: 3.0k .mjs.
Remains: Python ~4.8k app (emulator 950, generators/rig/schema 1,973, importers
902, utilities 944) + ~4.8k of their tests, all mypy --strict; C++ ~1.9k ESPHome
glue; YAML ~4.3k (config/data, not code). Net: ~70% of hand-written code in
Rust/TS, and 100% of the rest under a type checker.

## Loop-sized execution chunks

Each chunk is a loop: a fixed unit of iteration, an objective gate every pass, a
stop condition. Nothing lands unless `make check` (and the chunk's own gate) is
green. One commit per completed iteration keeps every loop pass revertable.

### Track A — safe now (pre-Halloween)

| Loop | Iteration unit | Gate per pass | Stops when |
|---|---|---|---|
| A1 mjs→TS | 1–2 files from `web/test/*.mjs` | tsc --noEmit + npm test | 0 .mjs left |
| A2 ruff format | one-shot (not a loop) | make check after reformat + gate wired into lint/pre-commit | done in 1 |
| A3 mypy strict | 1 module from the stays-list into strict overrides | mypy green, no new ignores | stays-list fully strict |
| A4 typed boundaries | 1 data shape (scenes.yaml → tracks.json → markers) as TypedDict + typed loader | mypy green, Any count down | 3 shapes done |

A1 outcome (2026-08-26): COMPLETE in 4 passes. All 16 test files converted; tests
now import src directly (typed against the real modules, dist shims gone) and are
esbuild-bundled per-test with --platform=node. One .mjs remains BY DESIGN:
web/test/legacy_effects.mjs, the verbatim pre-migration reference fixture, typed
via the legacy_effects.d.mts sidecar — converting it would destroy its purpose.
tsconfig now covers test/*.ts (lib ES2022, @types/node).

A2 outcome (2026-08-26): applied; six files are excluded in [tool.ruff.format]
because formatting pushed them past the 500-line cap (castle_fuzz, import_track,
gen_wiring_diagram, test_gen_esphome, test_generator_parity, test_studio_api).
Follow-up loop: split each on a real seam, format it, drop it from the exclude
list — the list only shrinks.

A3 order (easy → hard): netguard, check_loc, build_paths, manifest, hosts,
scene_schema, rig_layout, gen_rig, gen_show, gen_esphome_audio, importers,
gen_wiring/qr/eink, castle_emu_wire, castle_emu, castle_emu_http, gen_esphome,
gen_previewer — then their tests.

### Track B — off-season (post-Halloween)

| Loop | Iteration unit | Gate per pass | Stops when |
|---|---|---|---|
| B1 core/effects | scaffold → int hash → 1 effect family per pass → WASM face → desk swap | Rust tests + frame-exact vs TS/C++ + page ≤4MB budget | old parity suite retired |
| B2 core/pulse | dynamics fns → expand → fuzz harness swap | digit-for-digit vs pulse_expand.py on seeded fuzz | track_lights.ts is a WASM shim |
| B3 core/synth+onsets | 1 synth voice per pass, then analyze bands | rendered mp3s byte-compare vs Python render | render_audio calls the crate |
| B4 bridge CLI | 1 desk verb per pass (status, scene, stop, volume) then sd_sync | emulator round-trip (castle_emu) green | castle_link/native/sd_sync retired |
| B5 studio→axum | 1 route group per pass (tracks, scenes, media, jobs, publish, relay) | existing e2e suite (140 tests) against the new server | studio*.py retired |

Loop rules: never convert a file and refactor it in the same pass; if a pass
can't reach green, revert the pass (not the loop) and log why; B-loops each
start with a 30-min spike check (WASM size, crate coverage) before pass 1.

## End state

Hand-written logic: TS ~18k lines, Rust ~2–3k (the kernels), Python ~15k (typed
glue), C++ <2k (shims). Every duplicated-math parity pair replaced by one Rust
implementation. No firmware rewrite; ESPHome stays.
