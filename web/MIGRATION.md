# Moving the cue desk to TypeScript — DONE (2026-08-10)

All nine modules landed and the switchover is complete. Kept as a record of
why it was done this way — a historical document, not a map. The layout table
below is the plan as it stood in August 2026; `web/src/` has grown well past
it since — dozens of modules now, not nine (`ls web/src/*.ts | wc -l` is the
only count worth trusting; an exact number here goes stale in a week). Read
`web/src/` itself for the layout; this file only says why there is a
`web/src/` at all.

(As it stood before the migration: one 1892-line HTML file with ~1400
lines of untyped inline JavaScript, exempt from the LOC check as
"generated" — technically true, practically a dodge. What follows was the
plan; the Outcome section at the bottom is what actually happened.)

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

---

## Outcome

| | before | after |
|---|---|---|
| authored previewer | 2005-line HTML, ~1400 lines inline untyped JS | 221-line template + 361-line stylesheet + 9 typed modules |
| largest authored file | 2005 | 440 (`synth.ts`) |
| type checking | none | `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` |

The generated `castle-cue-desk.html` is still one self-contained file with no
external requests — that constraint never moved, because the published artifact
runs under a strict CSP and the on-device page rules out a CDN too.

**What made it safe:** `test/legacy_effects.mjs` holds the pre-migration effect
code verbatim, and the equivalence harness compares it against the port across
13 effects x 3 parameter sets x 9 timestamps x 21 seeds — 7375 comparisons, all
identical to 1e-12. A type checker cannot catch a transposed digit; that can.

**Verified in the browser after the flip:** scene switching, play/pause, drag
seek, keyboard, Esc blackout, Stop-then-Play relighting the stage, cue ticks,
capacity readout, mute defaulting on. Zero console errors.

**One debugging trap worth remembering:** the canvas reads as entirely black and
`requestAnimationFrame` never fires when the browser pane is not the front tab —
`document.hidden` is true and the browser throttles rAF to zero. This looks
exactly like a broken render. Front the tab (a screenshot does it) before
believing a canvas measurement.
