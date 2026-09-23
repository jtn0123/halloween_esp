# Castle lighting improvement — agent handoff

Prepared September 18, 2026. This is the starting document for the next agent.

## Objective and current stopping point

Justin wants imported song light shows to be visibly more interesting, with
more variation and choreography comparable to older authored shows. Continue
with software simulation and synchronized before/after previews. Hardware
acceptance comes later and Justin intends to perform that test himself.

The original pipeline problem has a local implementation fix. A pulse-clarity
experiment was then tested, but Justin said **“I don't see that many changes
tbh.”** Do not treat better timing metrics as acceptance of that experiment.
Three stronger choreography candidates were subsequently added to the preview.
Justin has not yet selected or approved any of them as the default.

The latest request was to produce this handoff. No further implementation or
hardware action is implied by the handoff itself.

## User constraints and working style

- Keep the physical castle undisturbed. No device playback, uploads, settings
  changes, firmware flashing, or physical-device requests during continued
  silent preview work. Justin was watching a movie when he imposed this scope.
- Use silent, local simulation; do not start local song audio either.
- Show **all three fixtures together on each side**: left tower, right tower,
  and door. Before and candidate must share a song clock and scrub position.
- The inline chat visual failed to open for Justin. A normal local HTTP link
  was supplied instead; continue providing a working browser link.
- “Just discuss” means discussion only. Later requests authorized local
  experiments and preview work; they did not authorize promoting a candidate
  or deploying it.
- Explain visible effects in plain language. Counts, coverage and finite-pixel
  checks establish software behavior, not enjoyable choreography.

## Workspace and preservation

Repository: this one (`halloween_esp`), from its root.
At handoff inspection: branch `main`, HEAD `c1bd533`. Recheck before editing;
the initial investigation was against the older commit `f5f12ad`.

The workspace has substantial **uncommitted changes** and untracked source
files from this work. There was no commit, push, PR, or deployment in this
lighting workflow. Preserve the dirty tree; do not reset it or discard files.
Read `CLAUDE.md` and any currently applicable `AGENTS.md` before continuing.
The repository has a 500-line file limit and uses `.venv/bin/python`.

**Important for transfer to another checkout or machine:** Git alone will not
carry the working tree, untracked source, or ignored `.radio-data` artifacts.
The handoff document is not a backup. The next agent needs this same workspace
or a deliberate copy of the pending source changes and relevant local assets.
In particular, do not delete `.radio-data/comparison/`: it contains the only
current copies of the option 2–4 generation scripts and latest comparison page.

## Live preview

Open on this Mac:

<http://127.0.0.1:8894/all-three-preview.html?v=options>

It shows the current **locally prepared rich show** on the left and the selected
candidate on the right. “Current” is not a fresh reading of the installed
hardware show. Both sides show all three fixtures. Controls: Song, Passage,
Compare with, Play silently, and a shared scrubber. Initial candidate is option 2.

At handoff inspection, PID `56711` was listening on `127.0.0.1:8894` as a static
Python server. PIDs and service availability are transient; verify before use.
This server only serves the comparison directory and has no device bridge.
The URL is local to the Mac, not reachable from another machine as localhost.

If it is no longer running, from the repository root:

```sh
python3 -m http.server 8894 --bind 127.0.0.1 \
  --directory demo/castle-radio/.radio-data/comparison
```

Check the port before starting another process. Do not stop an unfamiliar
process to reuse a port. Other user services may use 8871, 8765 or 8766.

## What has been implemented

### 1. Original blandness cause and local integration fix

Older authored scenes execute rich cue files on the castle. The newer Radio
import path had been uploading audio without companion cues and relying on
streamed whole-zone solid-color updates, capped at four updates per second
across the entire castle. It discarded much of the analyzed timing and spatial
information, including decay and pixel masks.

`rich_show.py` now creates a native `.cue` file plus a `.show.json` decoded
from that exact binary. It reuses the existing rich desk recipe and pulse
expansion rather than the reduced streaming representation. Split imports keep
vocals on the door and left/right backing on the corresponding tower.

New import preparation and explicit Sync integrate the companions. Sync verifies
transfers and sends the cue file last. This is local source implementation;
no physical Sync was performed. Legacy reduced streaming remains a fallback
when native cues are unavailable.

The real Radio webpage now offers **Prepared castle show → Simulate lights
silently**. It loads the decoded preview and checks its cue CRC. Routing/style
experiments stay separate from the prepared output. Removing/restoring local
imports includes the generated companions.

### 2. Prepared-preview semantics correction

The desk renderer accumulates overlapping flashes. The firmware card reader
replaces the flash or attack target with the incoming encoded intensity.
It also does not attenuate cue input in the same way as desk soft-mode gestures.

`cue-playback.js` implements the card-specific cue application for prepared
shows. It is used by the actual Radio preview and offline simulations. The
shared pixel renderer still renders the pixels. Authored desk interactions
retain their original behavior. The device-site builder includes the new script.
Do not revert this correction or use desk `fireCues` for prepared-card A/B tests.

### 3. Four experimental choices

| Option | Visible behavior | Status / tradeoff |
| --- | --- | --- |
| 1: Pulse clarity | Filters competing same-fixture hits and shortens dense fades | Tested, but Justin found the visible improvement too subtle; removes substantial detail |
| 2: Alternating towers | Amber left / violet right alternation; door retains selected vocal hits | Stronger pulses and dimmer background; experimental |
| 3: Tower → door response | Alternating towers; green door answers every third selected backing accent after 144 ms | Intentionally replaces vocal-only door routing |
| 4: Full-castle accents | Alternation plus amber strikes across all fixtures every fourth selected backing accent; occasional door responses | Strongest coordinated design experiment; not user-approved |

Options 2–4 all set background levels to 0.12 and use selected pulse intensities
of 0.5–0.85. Therefore their differences are not isolated choreography changes;
contrast and palette choices also change. Strongest-first accent selection
requires at least 220 ms between backing accents and 180 ms between vocal
accents. These are **detected onsets, not musical beat/bar detection**.
There is no new verse/chorus detector in these candidates.

Option 1 uses a 64 ms same-fixture exclusion, gap-adaptive attack/decay below
400 ms, a 0.72 decay floor, and protection for attacks >= 160 ms. It does not
alter the default rich-show generator. Options 2–4 likewise only exist as
separate candidates and do not replace baseline files or the catalog.

## File map

Paths below are relative to the repository unless otherwise noted.

| File(s) | Purpose |
| --- | --- |
| `demo/castle-radio/rich_show.py` | Native rich preparation, binary-derived preview, metadata/CRC validation |
| `demo/castle-radio/cue-playback.js` | Card-specific cue application for the preview and simulator |
| `demo/castle-radio/preview.js` | Prepared/native mode, silent transport, renderer integration |
| `demo/castle-radio/pulse_clarity.py` | Standalone option 1 experiment; separate output directory |
| `demo/castle-radio/compare_lights.mjs` | Full-song A/B validation, normal/soft metrics and RGB clip samples |
| `demo/castle-radio/simulate_show.mjs` | Silent full-song finite-pixel/cue-execution checks |
| `demo/castle-radio/test_rich_show.py` | Preparation, routing, file invalidation, emulator transfer tests |
| `demo/castle-radio/test_pulse_clarity.py` | Option 1 behavioral boundaries |
| `demo/castle-radio/test_rich_preview.test.mjs` | Prepared preview, silent transport, card strike behavior tests |
| `demo/castle-radio/light-comparison.fragment.html` | Original reusable two-version visual template; not the latest option picker |
| `demo/castle-radio/.radio-data/comparison/build_options.py` | Ignored, local-only generator for options 2–4 |
| `demo/castle-radio/.radio-data/comparison/build_options_page.py` | Ignored, local-only composer for the four-option comparison |
| `demo/castle-radio/.radio-data/comparison/lighting-options.html` | Latest interactive HTML fragment with embedded RGB samples |
| `demo/castle-radio/.radio-data/comparison/all-three-preview.html` | Latest standalone browser page; served at the URL above |

Other tracked modifications from the integration are in `Makefile` and the
Radio files `castle-direct.js`, `desktop_tools.py`, `device_site.py`,
`imports.js`, `index.html`, `library_ops.py`, `radio_jobs.py`,
`remote-library.js`, `remote_library.py`, `server.py`, and `style.css`.
Use `git diff` to review exact current changes, not this list as a patch.

## Local song assets and baseline identity

Library: `demo/castle-radio/.radio-data/tracks/` (ignored).
Catalog: `demo/castle-radio/.radio-data/catalog.json` (ignored).

| Song | Key | Baseline records | Baseline cue CRC32 |
| --- | --- | ---: | --- |
| Monster Mash | `radio_a1f0fc4d6545` | 4,459 | `1b80e0f6` |
| Day-o | `radio_4965453bf420` | 1,563 | `37e69d96` |

Each baseline has `<key>.cue` and `<key>.show.json`. Preserve original audio,
stem analysis, catalog and these baseline files. Candidate companions are in
the separate `comparison/` directory: `<key>.clarity.cue` for option 1,
`<key>.option2.cue`, `.option3.cue`, `.option4.cue` for the others, each with a
matching `.show.json`. Candidate previews are decoded from encoded cue bytes.

## Evidence and limitations

- Most recent completed production-source verification: 1,193 main Python
  tests, 109 Radio Python tests and 84 Radio JS tests passed. Lint, mypy, Rust,
  TypeScript, file/image guards and existing web rendering/parity suites passed.
- Exact gate history: `make check` reached a missing mypy annotation after its
  test stages passed. After fixing that annotation,
  `make check -o audio -o test -o test-radio` completed the remaining stages.
  Do not describe this as a single uninterrupted green command.
- `make coverage-radio` passed at 72%. Full browser E2E was not run; targeted
  muted browser interaction checks were performed instead.
- Option 1: 75,900 full-song simulation frames across both versions, both
  songs and normal/soft modes. All pixels finite/in range; all cues execute.
- Options 2–4: 227,700 frames, including repeated baseline runs. The six new
  binaries pass the real C++ firmware cue-reader trace checks on the Mac.
  Printed floats were compared within 0.0001 for float/double rounding.
- The C++ check runs the actual reader, not the physical LEDs. It establishes
  loading/scheduling/value behavior, not complete hardware or perceptual parity.
- The latest option picker passed browser checks for selection, playback/end,
  scrubbing, song switching, no audio/video elements, and 320 px layout overflow.
- Baseline CRCs remained unchanged after the experiments. No candidate has been
  installed. The original investigation did read the device; subsequent silent
  experiments did not contact it. Historical firmware/device observations in
  that investigation are not current-state proof.
- Full repository tests were not repeated for the final options 2–4 step,
  which changed only ignored preview artifacts and a report.
- The original option 1 visual displays frames every 32 ms. The latest four-
  option page downsamples to 64 ms to keep embedded data compact, even though
  the underlying simulation steps every 16 ms. Short pulses can be missed or
  understated on screen. Improve this before using the preview to fine-tune
  very short hits; a local standalone page need not inherit an inline size cap.
- Reduced hit counts change the denominator of “recovered before next hit.”
  That metric is not a quality score. Soft-mode improvement was much weaker.

Logs, screenshots, RGB samples and `options-firmware-validation.json` are in
`.radio-data/comparison/`. Earlier reports include older counts; the chronology
and later correction matter when interpreting them.

## Running or rebuilding safely

For the production Radio webpage with the castle deliberately unreachable:

```sh
CASTLE_RADIO_HOST=127.0.0.1:9 CASTLE_HOST=127.0.0.1:9 \
  .venv/bin/python demo/castle-radio/server.py 8883
```

Check port availability first. Keep output **This computer**. Use
**Import music → Preview show → Simulate lights silently**; do not click Play
or Sync. The separate comparison server on 8894 is simpler for option review.

Do **not** carry those host overrides into `make check`: some mocked launcher
and hermetic-environment tests expect them to be absent. The normal tests use
scratch data and emulators. Never use original song/scenes directories as test
fixtures. After production changes, run the repository's required checks and
Radio coverage again as appropriate.

To regenerate options 2–4 and their six comparison sample documents:

```sh
.venv/bin/python demo/castle-radio/.radio-data/comparison/build_options.py
for pair in monster:radio_a1f0fc4d6545 dayo:radio_4965453bf420; do
  sample_name=${pair%%:*}
  song_key=${pair#*:}
  for option in 2 3 4; do
    node demo/castle-radio/compare_lights.mjs \
      "demo/castle-radio/.radio-data/tracks/${song_key}.show.json" \
      "demo/castle-radio/.radio-data/comparison/${song_key}.option${option}.show.json" \
      "demo/castle-radio/.radio-data/comparison/${sample_name}-option${option}.json"
  done
 done
python3 demo/castle-radio/.radio-data/comparison/build_options_page.py
```

The page composer also needs the existing `monster.json`, `dayo.json` and
`all-three-lights-side-by-side.html`. It is not a clean-checkout build system.
It creates `lighting-options.html`, not the served standalone wrapper.
The existing wrapper was produced using the local visualization renderer:

```sh
python3 ~/.codex/plugins/cache/openai-bundled/visualize/1.0.37/skills/visualize/scripts/render.py \
  "$PWD/demo/castle-radio/.radio-data/comparison/lighting-options.html" \
  "$PWD/demo/castle-radio/.radio-data/comparison/all-three-preview.html" --force
```

That plugin path is machine-specific. If unavailable elsewhere, preserve/use
the already-generated standalone page, or build a normal standalone wrapper.
The fragment itself assumes theme utilities that the existing wrapper supplies.

## Recommended next steps — not yet done

1. Get Justin's reaction to options 2–4. No favorite has been chosen. The last
   suggestion was to start with option 4 because its coordinated accents are
   the largest visible change, not because it is proven best.
2. Make the standalone comparison display the full 16 or 32 ms output before
   judging fast pulse details. Keep all three fixtures visible and synchronized.
3. Refine the preferred choreography with explicit tradeoffs: retain vocal
   routing where desired, avoid excessive repetition, and assess dense and
   quiet sections. True beat/bar or verse/chorus adaptation is future work.
4. If adopting a candidate is requested, move its generator out of ignored
   scratch space into maintained source, add meaningful deterministic tests,
   make the comparison reproducible, and wire the chosen behavior into actual
   rich preparation and the prepared webpage preview using identical cue bytes.
5. Run appropriate regression checks and local emulator transfer rehearsal.
   Preserve a baseline for a later physical A/B. Justin handles physical
   acceptance; deployment is not authorized by the current preview requests.

## Supporting reports, in reading order

1. [Initial investigation](LIGHT-SHOW-BLANDNESS-INVESTIGATION-2026-09-18.md).
   Its opening “only repository change” describes that historical phase only.
2. [Native rich-show integration and validation](LIGHT-SHOW-SOFTWARE-VALIDATION-2026-09-18.md).
   Read its subsequent preview-correction note before relying on early visuals.
3. [Option 1 experiment](LIGHT-SHOW-OPTION-1-EXPERIMENT-2026-09-18.md).
4. [Options 2–4 experiment](LIGHT-SHOW-CHOREOGRAPHY-OPTIONS-2026-09-18.md).
5. [Options 5–7: beat-locked choreography and the live show lab](LIGHT-SHOW-BEAT-CHOREOGRAPHY-2026-09-18.md).
   Later than this handoff; supersedes its preview URL with `/show-lab.html`
   and its next-step 2 (full-rate display), which is done there.

## Suggested opening instruction for the next agent

> Read docs/LIGHT-SHOW-AGENT-HANDOFF-2026-09-18.md and inspect the existing dirty
> workspace before changing anything. Continue the silent, software-only castle
> lighting comparison. Preserve the baseline and keep all three fixtures visible
> on both sides. Option 1 was too subtle; options 2–4 are available but not yet
> accepted. Do not contact or operate the physical castle. Start by confirming
> the local comparison opens and understanding Justin's preferred next change.
