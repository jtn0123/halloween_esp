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
| Castle wire protocol (`/api/*` on the device) | `firmware/sd_web.h` · `tools/castle_emu_wire.py` · `core/src/bridge.rs` (the `castle` bin, a client of the same wire) | `tests/test_firmware_contract.py` parses the C; `tests/test_bridge_rust.py` runs every verb against `castle_emu` |
| The studio's whole HTTP surface (both tables in `docs/API.md`) | `tools/studio.py` + `studio_*.py` (production) · `core/src/bin/studio.rs` + `core/src/studio*.rs` | `tests/studio_rust_case.py` runs the two servers over twin sandboxes; `tests/test_studio_rust.py` (reads, aliases, relay failures), `test_studio_scenes_rust.py`, `test_studio_media_rust.py`, `test_studio_import_rust.py`, `test_studio_relay_rust.py` compare bodies — and the rebuilt audio and scenes.yaml — byte for byte |
| `tracks.json` (the provenance manifest, and its flock/atomic-rename protocol) | `tools/manifest.py` · `core/src/manifest.rs` | `tests/studio_rust_case.py` — the two servers' leftover `tracks.json` files are compared byte for byte after the live-analysis and write-back paths run in both |
| Import URL policy (which hosts yt-dlp may be handed) | `tools/netguard.py` · `core/src/netguard.rs` (the `netguard_dump` bin, `core/src/bin/netguard_dump.rs` — a URL corpus and a DNS table on stdin, one verdict per line out) | `tests/test_netguard_rust.py` drives both over the corpus `tests/test_netguard.py` holds the Python to, DNS mocked from one table on both sides, and compares the **refusal sentences**, not just the verdicts — the desk shows the string |
| Scene render (synth voices, onset detection, reverb, master chain) | `core/` (castle-core `scene_render` — the production renderer since the B3 swap) · `tools/synth*.py` + `tools/analyze.py` behind `render_audio.render_scene_py` (the reference) | `tests/test_scene_render_rust.py` (byte-equal WAV + markers, canonical crc pin), plus the per-layer gates `test_synth_rust`, `test_master_rust`, `test_onsets_rust` |

## Why

A divergence here is the worst bug the project can have, because it is
invisible: both sides run, neither errors, and the preview quietly stops
being a preview. Nothing structural forces two languages to agree, so the
only defence is a check that throws the same seeded cases at every copy and
compares the digits. The firmware copy is the hard one — it is float32 on
an S2 with no serial console — so `parity_dump.cpp` compiles the real
header with the host compiler and prints what the device would compute.

## How to run each check

```bash
make check                       # everything below except the browser suite
.venv/bin/python -m unittest tests.test_generator_parity tests.test_stream_dynamics \
                              tests.test_gen_fuzz tests.test_firmware_cxx -q
.venv/bin/python -m unittest tests.test_scene_render_rust tests.test_synth_rust \
                              tests.test_master_rust tests.test_onsets_rust \
                              tests.test_pulse_rust tests.test_bridge_rust -q
.venv/bin/python -m unittest discover -s tests -p 'test_studio*_rust.py' -q
.venv/bin/python -m unittest tests.test_netguard tests.test_netguard_rust -q
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
for going hunting. `test_firmware_cxx` and `firmware_parity` need a host
`clang++`/`g++` and SKIP (loudly) without one — a green run on a machine with
no compiler has not checked the firmware layer, and the same is true of every
Rust row without cargo.

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

Never skip or loosen a parity test to get a green run.
