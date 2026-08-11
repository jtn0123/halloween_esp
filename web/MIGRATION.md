# Moving the cue desk to TypeScript

The previewer works, but it is one 1892-line HTML file with ~1400 lines of
untyped inline JavaScript. The LOC check exempts it as *generated* — which is
technically true and practically a dodge, because its source is that inline
script. This is the plan for fixing that properly.

## Why bother

Two concrete failure modes this project has already hit, both of which a type
checker catches for free:

- **Contract drift.** The effect vocabulary exists in three places:
  `firmware/castle_effects.h`, `EFFECT_IDS` in `tools/gen_esphome.py`, and the
  `EFFECTS` object in the previewer. They must agree. `EffectName` in
  `src/types.ts` is the copy a compiler can check.
- **Shape mistakes.** The strike cue grew `targets`, `color` and `decay` over
  several rounds. Each addition meant touching two generators and the browser,
  and a missed one produced a cue that silently did nothing rather than an
  error.

## Target layout — every file under 500 lines

| Module | Responsibility | Approx |
|---|---|---|
| `types.ts` | shared contracts (**done**) | 110 |
| `effects.ts` | the effect vocabulary; port of `castle_effects.h` | 130 |
| `stage.ts` | canvas: castle, apertures, jewels, sky wash | 260 |
| `transport.ts` | clock, play/pause, seek, cue firing, mute | 240 |
| `synth.ts` | live WebAudio synth (organ, wind, thunder…) | 300 |
| `tracks.ts` | Tracks panel, studio API, in-browser onset detection | 280 |
| `panels.ts` | scene grid, cue sheet, channel strips, sliders | 200 |
| `main.ts` | wiring only | 80 |

Splits follow responsibility, not the line count — `stage.ts` is one file
because canvas drawing is one job, and would be worse cut in half.

## Build

`esbuild` bundles `src/main.ts` to a single IIFE. `tools/gen_previewer.py`
keeps its current role — splicing the generated scene data and base64 audio —
but injects the built bundle instead of finding hand-written script.

The output must stay **one self-contained file with no external requests**:
the published artifact runs under a strict CSP, and the medium-term goal of
serving a cut-down version off the device means no CDN either.

    cd web && npm run build      # -> dist/bundle.js
    make preview                 # splices bundle + scene data -> previewer HTML

## Order of work

1. `types.ts` — **done**, `npx tsc --noEmit` passes under `strict` plus
   `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`
2. `effects.ts` — smallest real logic, easiest to verify against the firmware
3. `stage.ts` + `transport.ts` — the interesting half
4. `synth.ts` — bulky but self-contained
5. `tracks.ts`, `panels.ts`, `main.ts`
6. Switch `gen_previewer.py` to inject the bundle; delete the inline script

Each step ends with the previewer still working in the browser. The old inline
script stays authoritative until step 6 flips over in one commit, so there is
never a half-migrated page.

## Also on the list

`tools/synth.py` is 395 lines and climbing — the next scene will push it over.
Natural seam: `voices.py` (pipe, piano, box, wind, thunder…) and `pieces.py`
(organ, waltz, descent, and the room/limiter). Python is typed with annotations
throughout already; it just needs splitting.
