# Parity — five implementations, one show

The cue desk's central claim is that the browser predicts the porch. The
arithmetic that makes a show — where a beat lands, which zone it strikes,
what colour the candle is at t=4317 s — is implemented more than once, on
purpose, and every copy is held bit-exact (or float32-exact) to the others
by seeded fuzz. This page is the contract as a whole; the headers of the
individual checks say how each one works.

## What is kept identical

| Layer | Copies | Checked by |
|---|---|---|
| Pulse dynamics (tempo, accents, pan, section gates) | `tools/pulse_dynamics.py` (both Python generators) · `web/src/track_lights.ts` · `core/src/pulse.rs` + `pulse_expand.rs` | `tests/test_stream_dynamics.py`, `web/test/track_lights_logic.ts`, `web/test/fuzz_parity.ts` + `tools/fuzz_check.py`; `tests/test_pulse_rust.py` (seeded corpus through the `pulse_dump` bin, digit-for-digit, `thin_pulses` compared by identity) |
| Pulse → cue merge (zone routing, round-robin, velocity rounding) | `tools/gen_esphome.py` · `tools/gen_previewer.py` | `tests/test_generator_parity.py`, `tests/test_gen_fuzz.py` |
| Effect maths (colour per pixel per frame) | `firmware/castle_effects.h` (C++, float32) · `web/src/effects.ts` (TS, double) · `core/src/effects.rs` (Rust, f32) | `web/test/firmware_parity.ts` reading `tests/cxx/parity_dump.cpp` (host-compiled); `web/test/effects_equivalence.ts`; `tests/test_castle_core.py` compares the crate's `parity_dump` bin against the same host-compiled C++, bit for bit |
| Rig geometry (which pixel is where, what `core` means) | `tools/rig_layout.py` → `firmware/generated/rig.h` · `web/src/rig.ts` | `web/test/rig_parity.ts`, `tests/test_rig_layout.py` |
| Castle wire protocol (`/api/*` on the device, and the stream server's port) | `firmware/sd_web.h` + `sd_web_stream.h` · `tools/castle_emu_wire.py` · `core/src/bridge.rs` (the `castle` bin, a client of the same wire) | `tests/test_firmware_contract.py` (routes, error strings, status and health keys, the stream port every loopback URL must spell) and `tests/test_firmware_names.py` (the byte rules) parse the C; `tests/test_bridge_rust.py` runs every verb against `castle_emu` |
| The castle's HTTP **handlers**, run rather than read (`sd_web.h` + `sd_web_util/state/site/remote/ota/stream.h`, over `sd_audio.h`, `castle_health.h`, `boot_log.h`) | `firmware/` itself · `tools/castle_emu*.py` | `tests/cxx/web_check.cpp` compiles the real headers against a fake ESP-IDF (`tests/cxx/shim/` — `httpd_uri_match_wildcard`, `httpd_query_key_value` and the error table ported from IDF 5.5.5, the card redirected to a temp directory with FatFs's path rules) and answers requests on a pipe. `tests/test_firmware_web_cxx.py` (routes, every other verb, the served pages, the JSON replies, the validators) and `tests/test_firmware_web_card.py` (PUT/DELETE, the crc, the sidecar, 413/507/short write, the OTA window per board, the no-card 503s) put ~380 identical requests to the C and to `castle_emu` over identical cards and compare status, body, content type and headers; `tests/test_firmware_web_storm.py` fires ~2000 seeded `fuzz_corpus` names at both — and both cards must match afterwards — then runs `safe_name`/`url_decode`/`json_escape` in C (`web_check --rules`) against `castle_emu_wire`'s port, byte for byte |
| The studio's whole HTTP surface (both tables in `docs/API.md`) | `core/src/bin/studio.rs` + `core/src/studio*.rs` — ONE copy since docs/RETIREMENT.md, where `tools/studio.py` + `studio_*.py` used to be the second | Nothing compares two servers any more, so three gates stand in its place: `tests/test_studio_golden.py` replays the Python studio's recorded answers (`tests/golden/*.json`) against the binary; the black-box suites `tests/test_studio_reads_rs.py`, `test_studio_writes_rs.py`, `test_studio_media_rs.py`, `test_studio_import_rs.py` and `test_studio_relay_rs.py` drive it over a seeded sandbox and state what each answer IS; and `make e2e` runs the browser suite against the built binary (`CASTLE_STUDIO_CMD` pins another). The crate's own `#[test]`s carry what has no HTTP face — `studio_progress`/`studio_jobs` (a job's phases and its wire shape), `studio_media`/`studio_wave` (the decode caches), `studio_proc`, `studio_relay`, `http_parse`/`http_resp` |
| `tracks.json` (the provenance manifest, and its flock/atomic-rename protocol) | `tools/manifest.py` · `core/src/manifest.rs` | `tests/test_import*.py` drive the Python's own writes; `tests/test_studio_reads_rs.py` reads back what the studio wrote — the cached duration and onsets, the provenance it must not clobber, and the `level_*` entries that are not onsets. The byte-for-byte comparison of two servers' leftover manifests went with the second server (docs/RETIREMENT.md) |
| Import URL policy (which hosts yt-dlp may be handed) | `tools/netguard.py` · `core/src/netguard.rs` (the `netguard_dump` bin, `core/src/bin/netguard_dump.rs` — a URL corpus and a DNS table on stdin, one verdict per line out) | `tests/test_netguard_rust.py` drives both over the corpus `tests/test_netguard.py` holds the Python to, DNS mocked from one table on both sides, and compares the **refusal sentences**, not just the verdicts — the desk shows the string |
| Scene validation (what a scene block may say, and the sentences a refusal shows) | `tools/scene_schema.py` (what `gen_esphome.py` runs before it emits) · `core/src/scene_schema.rs` + `scene_cues.rs`, over the crate's own YAML subset parser `core/src/yaml*.rs` (what the studio runs before it writes) | `tests/test_scene_schema_rust.py` drives both over one corpus — the whole show, the golden refusals, and the shapes `tests/test_scene_schema.py` holds the Python to — through the `scene_dump` bin, comparing the SENTENCES, not just the verdicts; and `tests/golden/scene_errors.json` freezes what the desk shows |
| Scene render (synth voices, onset detection, reverb, master chain) | `core/` (castle-core `scene_render` — the production renderer since the B3 swap) · `tools/synth*.py` + `tools/analyze.py` behind `render_audio.render_scene_py` (the reference) | `tests/test_scene_render_rust.py` (byte-equal WAV + markers, canonical crc pin), plus the per-layer gates `test_synth_rust`, `test_master_rust`, `test_onsets_rust` |

## Why

A divergence here is the worst bug the project can have, because it is
invisible: both sides run, neither errors, and the preview quietly stops
being a preview. Nothing structural forces two languages to agree, so the
only defence is a check that throws the same seeded cases at every copy and
compares the digits. The firmware copy is the hard one — it is float32 on
an S2 with no serial console — so `parity_dump.cpp` compiles the real
header with the host compiler and prints what the device would compute.

The wire row underneath it was, until 2026-09-06, the one place where the
two copies were only ever compared by READING one of them: the emulator was
held to `sd_web.h` by parsing the C, which catches a renamed route or a
changed error string and cannot catch a handler that decides differently.
`web_check.cpp` closed that by running the handlers. It found five
divergences on its first pass — every queued reply's JSON spacing, a CSP
header missing from `/site/`'s refusals, an IDF error table a major version
stale, `GET //` served instead of refused, and `GET /sd/<file>/` served
instead of refused — none of which any parse could have seen, and every one
of which the emulator had been telling the desk about for months.

The scene-render row has one extra wrinkle: the Python's digits depend on
which numpy/scipy wheel is installed, so `tests/synth_probes.py` measures the
wheel's arithmetic (a six-character profile — multiply form, poly form,
divide, csqrt, sosfilt, interp; the macOS arm64 reference wheel is `101211`)
and the crate follows the measured forms. Linux manylinux wheels answer
different forms (unfused interp, gcc's complex multiply, glibc's csqrt) and
the probes cover them; `Modes::CANONICAL` pins the `101211` arithmetic so the
published render is the same bytes on every machine regardless of the local
wheel — held by the crc pin in `tests/test_scene_render_rust.py`, verified on
macOS arm64, Linux aarch64 and Linux x86_64.

## When one side is going away: the goldens

`tools/studio.py` is retired (docs/RETIREMENT.md). Its row above was the only
one whose *reference* was scheduled for deletion, and a live-twin comparison
cannot outlive the twin. So the evidence was written down while the
Python studio was still trusted: `tools/gen_golden.py` boots it over a
throwaway sandbox and records the deterministic surface — the castle-less read
routes, the 404 and 502 shapes, the `/studio/tracks` field shape, and the whole
corpus of `POST /studio/scene` refusals — into `tests/golden/`.
`tests/test_studio_golden.py` replays the same script against the Rust studio
alone and diffs; it neither imports nor launches the Python one, so nothing
about it changes on the day that file is deleted.

The splice refusals are the part worth the trouble. Every string the desk shows
next to a bad scene block used to come from ONE implementation — `scene_schema`
behind `studio_scenes.check()`, which the Rust studio reached by piping through
`tools/scene_check.py`. That delegation went with the Python server
(docs/RETIREMENT.md phase 2): the validator is native now, the goldens are what
it had to reproduce, and it does — sentence for sentence, ceiling refusal
included — with ONE entry regenerated on purpose. `yaml_unparseable`'s tail was
PyYAML's own prose about a stream that ended mid-flow; a parser that is not
PyYAML writes its own, and the diff in that file is the record of the day
byte-parity for that route ended. The row above it in the table is what keeps
the two rule sets together now that nothing compares them at run time.

Adding a case is: edit `tests/golden_corpus.py`, run
`.venv/bin/python tools/gen_golden.py`, read the diff, commit both. Regenerate
deliberately and never to make a failure go away —
`gen_golden.py --check` fails when the committed files are stale, and only
values that are the same on every machine belong in the corpus (which is why
the track listing is recorded as its shape, not its onset counts).

## How to run each check

```bash
make check                       # everything below except the browser suite
.venv/bin/python -m unittest tests.test_generator_parity tests.test_stream_dynamics \
                              tests.test_gen_fuzz tests.test_firmware_cxx -q
.venv/bin/python -m unittest tests.test_firmware_web_cxx tests.test_firmware_web_card \
                              tests.test_firmware_web_storm -q   # the C handlers vs the emulator
CASTLE_STORM_SEED=99 CASTLE_STORM_CASES=8000 \
  .venv/bin/python -m unittest tests.test_firmware_web_storm -q  # go hunting
.venv/bin/python -m unittest tests.test_scene_render_rust tests.test_synth_rust \
                              tests.test_master_rust tests.test_onsets_rust \
                              tests.test_pulse_rust tests.test_bridge_rust -q
.venv/bin/python -m unittest discover -s tests -p 'test_studio*_rs.py' -q
.venv/bin/python -m unittest tests.test_studio_golden -q   # Rust vs the goldens
.venv/bin/python tools/gen_golden.py --check               # are the goldens current?
.venv/bin/python -m unittest tests.test_netguard tests.test_netguard_rust \
                              tests.test_scene_schema tests.test_scene_schema_rust -q
cd web && npm test               # bundles test/*.ts into dist/, then runs them
```

The browser checkers are TypeScript under `web/test/`, and node does not run
them from there — `npm test` bundles each one to `dist/<name>.mjs` with esbuild
first. So to re-run ONE of them with a seed pinned, build once and then call
the bundle:

```bash
cd web && npm test                              # or just: builds every dist/*.mjs
cd web && node dist/firmware_parity.mjs         # C++ vs TS effects, default seed
cd web && PARITY_SEED=9 PARITY_CASES=20000 node dist/firmware_parity.mjs
cd web && FUZZ_SEED=123 FUZZ_CASES=500 node dist/fuzz_parity.mjs   # TS vs both Python generators
cd web && node dist/rig_parity.mjs
```

The env knobs are read at run time, so a rebuild is only needed when a source
file changed. Seeds are fixed by default so a red run reproduces; the knobs are
for going hunting. `test_firmware_cxx`, the three `test_firmware_web_*`
suites and `firmware_parity` need a host `clang++`/`g++` and SKIP (loudly)
without one — a green run on a machine with no compiler has not checked the
firmware layer, and the same is true of every Rust row without cargo.

## When it fails

1. Read the first mismatch: every checker prints the case (seed, index,
   inputs) and both answers. `firmware_parity.ts` also names the layer
   (`hashf`/`vnoise`/`fbm` probe lines vs effect lines) so a lattice-hash
   drift is told apart from a palette-mix drift.
2. Decide which side is RIGHT. Usually the one you did not just edit. The
   rule in CLAUDE.md is "change both or neither": a new effect, a changed
   decay curve or a moved pan threshold is one commit touching every copy.
3. Re-run with the printed seed pinned (`PARITY_SEED=…`, `FUZZ_SEED=…`) until
   green, then run the default seeds again.
4. Float32 slack is already accounted for — the tolerances in
   `firmware_parity.ts` grow exactly as float32 phase error does. Do not
   widen them to make a run pass; a failure past them is a real drift.
5. If the firmware header changed its hash or a primitive's rounding, the
   TS port (`effects.ts`, `hashi/hash3` in the checker) must change the same
   way, and `legacy_effects.mjs` in `effects_equivalence.ts` is re-pinned
   only with a note in its header saying why.
6. On x86_64 the render gates can fail before any digit is compared, with
   `np.sin does not agree with libm on this host` from
   `tests/synth_probes.assert_libm_transcendentals` — numpy is dispatching a
   vector math kernel (AVX-512 is the known case), which computes sin/exp/
   log/pow to a different last ulp than CPython's and Rust's plain libm calls
   and moves pocketfft, the reverb's transform, with it. The remedy is
   numpy's own switch: export `NPY_DISABLE_CPU_FEATURES` **before** numpy is
   imported, naming this wheel's dispatch targets — the failure message
   computes that list for the installed wheel rather than quoting one, since
   numpy renames the targets between releases and a name outside the list is
   an ImportWarning that disables nothing. CI's Linux x86_64 job exports
   exactly that (`ci.yml:101`).

Never skip or loosen a parity test to get a green run.
