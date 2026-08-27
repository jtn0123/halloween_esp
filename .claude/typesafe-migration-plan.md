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

A3 progress (2026-08-26, 5 passes): ten modules strict — netguard, check_loc,
build_paths, manifest, hosts, scene_schema, rig_layout, gen_rig, gen_show,
gen_esphome_audio, gen_qr, gen_eink_font (four of those were already clean).
The ratchet lives in pyproject's per-module override block; hosts grew a
_Device TypedDict, manifest an Entry alias (A4 upgrades it to a TypedDict).
TRACK A COMPLETE (2026-08-26, third run, 11 passes). A3: every stays-list
module AND its tests strict (33 modules on the ratchet). A4: all three
shapes — Scene/Zone/Cue/Pulse + ShowDoc behind scene_schema.load_show/
parse_show, Markers behind load_markers, manifest.Entry a TypedDict with
patch(**Unpack[Entry]). A2 follow-up: all five excluded files split on real
seams (import_convert, fuzz_http, test_gen_esphome_main,
test_pulse_dynamics_parity, test_studio_scene_edit) and the format exclude
list is deleted — ruff format now covers every Python file. Deliberately
NOT strict: Phase 2-4 code and its tests (studio*, castle_link/native/
sd_sync, synth/analyze, pulse internals, castle_fuzz body) — annotating
code Track B deletes is wasted work. Note: castle_fuzz sits at 499 lines;
its next growth splits the Fuzzer class.

A3 TOOLS COMPLETE (2026-08-26, second 5-pass run): importers, the emulator
trio, gen_esphome, gen_previewer and gen_wiring_diagram are all strict — the
last after its overdue split (wiring_svg.py holds the drawing kit; output
verified byte-identical). gen_esphome's pulse-contract re-exports are now
explicit in __all__. A4 shape 1 done: manifest.Entry is a TypedDict and
patch() takes Unpack[Entry]. Remaining in Track A: the tests/ modules into
strict (~286 errors — batch by file), A4 shapes 2–3 (scenes.yaml, markers),
and the five files still on the ruff-format exclude list.

### Track B — off-season (post-Halloween)

B1 progress (2026-08-27, started early at the user's direction, 4 passes):
core/ (castle-core crate, zero deps) holds the full per-pixel render path —
noise primitives, all 13 effects, overlays, strike gates — proven BIT-EXACT
against the host-compiled firmware header by tests/test_castle_core.py
(the C++ there builds with -ffp-contract=off: clang on arm64 otherwise
fuses a*b+c into fma, which the FPU-less S2 never does, so the un-fused
build is the device-faithful proxy and exact bits replace tolerances).
The WASM face is built and checked: 8 KB, allocator-free, loads in node,
fbm-only paths bit-exact.

B2 pass 1 (2026-08-27): core/src/pulse.rs — tempo_factor/tempo_decay/
round3/is_accent/gate_mul/gate_note/drift_base/thin_pulses — held
digit-for-digit (f64, no tolerances) against tools/pulse_dynamics.py by
tests/test_pulse_rust.py over a seeded corpus via the pulse_dump line
protocol; thin_pulses compared by surviving-index identity so both
tie-breaks are pinned. section_gates' YAML walking stays Python (plumbing,
not arithmetic).

B2 passes 2-3 + B4 passes 1-2 (2026-08-27, second Track-B run):
core/src/pulse_expand.rs carries the whole pulse-to-cue merge (zone
round-robin, decisive pan, boost, takeover, drift, velocity masks —
including every .get default), pinned by a 300-stream random-presence
corpus AND by the live cross-language fuzz: tools/fuzz_check.py now
computes a third "rust" answer per case and asserts it equals the
Python's inside make check. Nothing retired yet — the fuzz-harness
shrink waits for post-Halloween with the desk swap. B4 started early:
core/src/bridge.rs + the `castle` bin speak sd_web.h's HTTP on a bare
TcpStream (zero deps) — status/health/scene/play/stop/volume/show/
blackout/files/bootlog all round-trip castle_emu in
tests/test_bridge_rust.py. Host discovery (devices.toml, fallback
walks) stays in tools/hosts.py until the sd_sync verbs port. NOT done, deliberately: the desk swap (wiring
the wasm into the previewer in place of effects.ts) stays post-Halloween —
it changes what the live show previews with, and the sine-effect
wasm-libm-vs-JS-Math deltas need the swap harness's tolerance model.

B4 passes 3-6 (2026-08-27, third Track-B run): the `castle` CLI now
carries sd_sync's whole card-and-firmware surface — `put` (with --to
site|scenes) held to the byte count AND the v5.42 CRC32 the firmware
answers with (crc32 implemented in-crate, bitwise, zero-dep), `rm`,
`purge` (files only, directories survive — "clear the music, not the
card"), and `ota` (0xE9 magic check, stop-audio-first per the standing
rule, tolerant of the reboot race, status poll as the verdict;
CASTLE_OTA_WAIT_S bounds the poll for tests). Host discovery ported
too: core/src/hosts.rs reads devices.toml through a TOML-subset parser
and reproduces hosts.py's candidates() — tests/test_bridge_rust.py
holds the two together combo for combo against tomllib, and the CLI
probes multi-candidate walks so a dead lease falls through to the
living fallback with no --host at all. 21 emulator round-trip tests.
The reply parsing is deliberately NOT a JSON parser: the firmware
prints fixed snprintf templates (json.dumps spacing tolerated for the
emulator). Still Python: sd_sync's repo-glob conveniences (scenes/site
source discovery, lean-page rewrite — they need gen_previewer) and the
size-skip optimization; those retire with the studio port, not before.

B3 passes 1-4 (2026-08-27, fourth Track-B run): the synth pipeline's
foundations, all gated bit-exact with a new probing discipline. Pass 1:
core/src/rng.rs is numpy's default_rng draw-for-draw — SeedSequence's
4-word pool and PCG64 XSL-RR (the spike caught a transposed multiplier
digit and, bigger, that Generator.uniform's `low + range*d` is compiled
to an fma on arm64 wheels but not baseline x86 — so the test PROBES the
installed numpy and tells the Rust dump which form to match; exact on
every platform, tolerant on none). Pass 2: synth.rs — pipe/piano/box,
whole buffers bit-equal (np.interp is fused inside, np.linspace is
i*delta endpoint-forced). Pass 3: pieces.rs — organ, descent, waltz,
musicbox, toll, drone and _place, buffers AND light markers exact.
Pass 4: filters.rs — scipy butter (N=1/2 lowpass, N=2 bandpass with a
hand-derived zpk2sos pairing for that fixed shape) and sosfilt. The
deep finding: numpy's complex kernels carry compiler-placed fmas that
DIFFER WITHIN one wheel — the ufunc multiply is fused where np.poly's
convolve loop is naive; division is Smith-with-reciprocal fused at all
three products; csqrt's hypot is sqrt(fma(x,x,y*y)) — all pinned by
enumeration, all behind per-kernel mode flags that
tests/synth_probes.py (split at the 500-line cap) measures from the
host's wheels at test time. Still Python: the rng-driven atmosphere
voices (wind, heartbeat, creak, shriek, whispers, thunder — they need
_sweep_lp's blockwise sweeps next), reverb (fftconvolve = pocketfft,
deliberately deferred), limit, and render_audio's scene mixing.

B3 passes 5-11 (2026-08-27, closing the synth path): the atmosphere
voices (core/src/atmos.rs — wind/heartbeat/creak/shriek/whispers/
thunder; the find: numpy's array**2 is x*x where **1.6 is libm pow),
then two PORTABILITY fixes that improved the Python itself before each
port. limit left np.convolve (each window went to the BLAS dot — vendor
summation order, so renders differed across machines) for cumsum
averages in the new tools/synth_master.py (split at the cap), matched
by core/src/master.rs with the full tail chain: loop crossfade, end
fade, normalise, int16 quantise. apply_reverb left pocketfft the same
way: a defined-order radix-2 FFT (separate re/im arrays so no complex
kernels fuse; one twiddle table strided per stage — exact, since
halving the angle and doubling the index round identically) lives in
synth_master and core/src/fft.rs, 3e-16 from fftconvolve and bit-equal
cross-language. core/src/scene.rs then renders WHOLE scenes: crc32-
seeded dice through all twelve voices in score order, takes, gains,
tails, reverb on the same dice, limiter, normalise — and markers with
CPython's round(v,3) (= Rust {:.3}: decimal ties-to-even both).
Closing gate: five scenes straight out of scenes/scenes.yaml render
byte-identical — f64 buffer, marker dict, and the int16 PCM write_wav
produces. Still Python in the render path: lame encoding (external),
the imported-track path (ffmpeg decode + analyze's onset bands — FFT-
framed, next season), and render_audio's orchestration itself, which
retires only when the tool calls the crate (a swap, so post-Halloween).

B3 passes 12-15 (2026-08-27, sixth Track-B run — the ears): analyze.py
left scipy's kernels first, like limit and the hall before it — its
STFT rebuilt on synth_master's defined-order FFT (with scipy's exact
framing, periodic hann, and the t grid matched BITWISE: frame centres
shifted back, not i*HOP/sr), np.convolve smoothing replaced by an
explicit FIR, signal.medfilt by an explicit zero-padded middle element
— behavioral identity proven hit-for-hit on a ten-way corpus before
committing. core/src/onsets.rs then ports the whole detector, which
meant pinning numpy's REDUCTION orders: np.sum is pairwise (8
accumulators per 128-block, recursive halving above), np.std rides it,
.sum(axis=0) adds rows sequentially, and complex abs is numpy's own
scaled hypot with a fused inner term (ax*sqrt(fma(r,r,1)) — none of
libm hypot, naive, or plain-scaled matched). envelope(), analyze_full's
beatless merge and annotate_pan followed (round(pan,2) = {:.2}, same
ties-to-even story), and core/src/media.rs closes analyze_file: both
languages run the identical ffmpeg command, so the samples agree by
construction. Gates: tests/test_onsets_rust.py — band dictionaries
equal hit for hit across bursts, waltz, heartbeat, drones at three
sensitivities, stereo pans, and two real WAV files end to end. With
this, EVERYTHING the importer computes and everything the renderer
produces exists once, in castle-core, bit-exact. Left in Python: the
orchestration shells (render_audio, import_track, studio) — they retire
with the B5/post-Halloween swaps, not with more math.

B5 (2026-08-27, seventh Track-B run, 7 passes): the studio server,
whole. NOT axum — the plan's row predates the bridge proving std-only
HTTP pleasant: the Rust studio is a fifth castle-core face (bin
`studio`, zero deps still), spawning the same venv tools the Python
spawns, exactly as intended. Pass 1: transport (keep-alive, Range,
ETag/304, CSP; no gzip by choice), CPython-shaped JSON (jsonio.rs:
json.dumps separators/escapes, indent-2 manifest form), the tracks read
side, deletion, the alias table — tracks.json byte-identical from both
languages, same flock (a two-line extern; std's File::lock is 1.89).
Pass 2: waveform BYTE-equal (the crate's own decode/onsets), stems
reads. Pass 3: scenes — validation stays Python behind a new
tools/scene_check.py shim so the 400 strings have one home; splice/
remove/rebuild ported; both sides render byte-identical artifacts.
Pass 4: jobs + imports (multipart, refresh, async polled to the same
end; netguard; reason()'s heuristics; the find: fs::read("/dev/urandom")
reads to EOF — read_exact or hang). Pass 5: the relay with castle_link's
TTL caches on typed bridge faults, emulator round-trips; probe; compare
via tools/compare_encodes.py; stop/restart for real (execv +
SO_REUSEADDR + FD_CLOEXEC). Pass 6: publish through sd_sync — which
REFUSES host:port castles, a latent break in the emulator chain's
auto-publish (flagged as follow-up; parity holds on the shared refusal).
Pass 7: CASTLE_STUDIO_CMD swaps the e2e webServer — ALL 148 BROWSER
TESTS PASS against the Rust studio unchanged, the loop's stop condition.
Parity harness: tests/studio_rust_case.py + five suites drive both
servers over HTTP on twin sandboxes. studio*.py stays the default
through Halloween; retiring it (and the desk swap) is the off-season
flip. Deliberately not ported: the aioesphomeapi native leg (flash
build; the esphome-native-api crate swap owns it) and gzip.

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
