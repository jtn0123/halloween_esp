# Parity — four implementations, one show

The cue desk's central claim is that the browser predicts the porch. The
arithmetic that makes a show — where a beat lands, which zone it strikes,
what colour the candle is at t=4317 s — is implemented more than once, on
purpose, and every copy is held bit-exact (or float32-exact) to the others
by seeded fuzz. This page is the contract as a whole; the headers of the
individual checks say how each one works.

## What is kept identical

| Layer | Copies | Checked by |
|---|---|---|
| Pulse dynamics (tempo, accents, pan, section gates) | `tools/pulse_dynamics.py` (both Python generators) · `web/src/track_lights.ts` | `tests/test_stream_dynamics.py`, `web/test/track_lights_logic.mjs`, `web/test/fuzz_parity.mjs` + `tools/fuzz_check.py` |
| Pulse → cue merge (zone routing, round-robin, velocity rounding) | `tools/gen_esphome.py` · `tools/gen_previewer.py` | `tests/test_generator_parity.py`, `tests/test_gen_fuzz.py` |
| Effect maths (colour per pixel per frame) | `firmware/castle_effects.h` (C++, float32) · `web/src/effects.ts` (TS, double) | `web/test/firmware_parity.mjs` reading `tests/cxx/parity_dump.cpp` (host-compiled); `web/test/effects_equivalence.ts` |
| Rig geometry (which pixel is where, what `core` means) | `tools/rig_layout.py` → `firmware/generated/rig.h` · `web/src/rig.ts` | `web/test/rig_parity.mjs`, `tests/test_rig_layout.py` |
| Castle wire protocol (`/api/*` on the device) | `firmware/sd_web.h` · `tools/castle_emu_wire.py` | `tests/test_firmware_contract.py` parses the C |

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
cd web && npm test               # builds dist/, then runs every *_parity.mjs
cd web && node test/firmware_parity.mjs          # C++ vs TS effects, default seed
cd web && PARITY_SEED=9 PARITY_CASES=20000 node test/firmware_parity.mjs
cd web && FUZZ_SEED=123 FUZZ_CASES=500 node test/fuzz_parity.mjs   # TS vs both Python generators
cd web && node test/rig_parity.mjs
```

Seeds are fixed by default so a red run reproduces; the env knobs are for
going hunting. `test_firmware_cxx` and `firmware_parity.mjs` need a host
`clang++`/`g++` and SKIP (loudly) without one — a green run on a machine with
no compiler has not checked the firmware layer.

## When it fails

1. Read the first mismatch: every checker prints the case (seed, index,
   inputs) and both answers. `firmware_parity.mjs` also names the layer
   (`hashf`/`vnoise`/`fbm` probe lines vs effect lines) so a lattice-hash
   drift is told apart from a palette-mix drift.
2. Decide which side is RIGHT. Usually the one you did not just edit. The
   rule in CLAUDE.md is "change both or neither": a new effect, a changed
   decay curve or a moved pan threshold is one commit touching every copy.
3. Re-run with the printed seed pinned (`PARITY_SEED=…`, `FUZZ_SEED=…`) until
   green, then run the default seeds again.
4. Float32 slack is already accounted for — the tolerances in
   `firmware_parity.mjs` grow exactly as float32 phase error does. Do not
   widen them to make a run pass; a failure past them is a real drift.
5. If the firmware header changed its hash or a primitive's rounding, the
   TS port (`effects.ts`, `hashi/hash3` in the checker) must change the same
   way, and `legacy_effects.mjs` in `effects_equivalence.ts` is re-pinned
   only with a note in its header saying why.

Never skip or loosen a parity test to get a green run.
