# Option 1: clearer pulses, software-only experiment

## Outcome and scope

A synchronized, silent before/after visual compares the **current locally
prepared rich cue files** with an experimental pulse-clarity pass. This does
not claim that those local files are currently installed on the castle.
No device requests, upload, playback, firmware change, or catalog replacement
were used for this experiment. The physical test is still Justin's next step.

The candidate has much more recovery between hits in normal mode, but it
suppresses substantial fine detail. Keep it opt-in pending visual judgment;
this is a useful first experiment, not a claim of perfect lighting.

## What option 1 actually changes

`demo/castle-radio/pulse_clarity.py` operates on the existing binary cue file,
so the comparison does not rerun audio analysis or change its detected onsets.

- Independently for each fixture, keep the strongest strike within a 64 ms
  exclusion window, retaining its original timestamp, color and pixel mask.
  Intensity ties favor the earlier strike. No cross-fixture suppression.
- For accepted strikes less than 400 ms apart, shorten the attack to at most
  one quarter of the gap, in 16 ms steps. Shorten the decay toward 15% remaining
  amplitude before the next strike, allowing one tick for scheduling/peak time.
- Never lengthen a fade. A 0.72 minimum decay bounds how abruptly it can fall;
  the shortest gaps therefore cannot always reach the 15% target.
- Protect long swells (attack >= 160 ms), authored effect changes, base looks,
  levels, routing and duration. These two songs have 0 or 90 ms attacks.
- Write `.clarity.cue` and its decoded `.clarity.show.json` into a separate
  ignored comparison directory. Baselines and automatic preparation stay intact.

This selects the strongest cue, not the perceptually strongest colored pixel.
Some frequency-band accents, colors and scatter patterns are consequently
removed. Fewer strike epochs also change subsequent scatter choices.

## Evidence from complete songs

All figures below come from the same prepared cue playback and shared webpage
pixel renderer at 16 ms steps, starting at song time zero. The interactive
visual samples those frames every 32 ms, with no separate brightness boost.

| Measure | Monster Mash current | Monster Mash option 1 | Day-o current | Day-o option 1 |
| --- | ---: | ---: | ---: | ---: |
| Total cue records | 4,459 | 2,339 | 1,563 | 918 |
| Strike records | 4,447 | 2,327 | 1,530 | 885 |
| Normal: incoming hit opportunities with prior flash < 0.15 | 41.3% | 99.5% | 81.4% | 99.3% |
| Soft: incoming hit opportunities with prior flash < 0.15 | 9.4% | 22.5% | 58.2% | 60.9% |
| Normal: fixture time with flash > 0.5 | 1.6% | 1.8% | 0.6% | 1.0% |

A hit opportunity is a fixture receiving at least one strike within a 16 ms
frame. Recovery is sampled immediately before that frame's cues. The metric
measures the strike envelope, not total light: background effects remain lit.
Its denominator changes when hits are removed, so it is **not a quality score**
or proof that the same number of musical accents improved. Slightly more time
above half amplitude shows that prioritizing stronger hits is not merely dimming
all output. Soft mode intentionally slows fades and needs separate tuning.

| File | Baseline CRC32 | Candidate CRC32 |
| --- | --- | --- |
| `radio_a1f0fc4d6545` (Monster Mash) | `1b80e0f6` | `eb283e3d` |
| `radio_4965453bf420` (Day-o) | `37e69d96` | `ad470dd4` |

## Preview mismatch discovered and corrected

The desk renderer adds new strikes to the remaining flash and attenuates their
input in soft mode. The card player in `firmware/castle_cues.h` replaces the
flash/attack target with the encoded intensity. Using desk gestures for card
preview therefore made dense sections look more continuously bright.

`cue-playback.js` now applies prepared cues with the card player's semantics.
The prepared webpage preview and offline simulator both use it; authored desk
and routing experiments retain their existing behavior. The device-site builder
includes this script, but no built site was uploaded. Both A/B sides use the
corrected semantics, so the visual isolates option 1 rather than mixing this
preview correction into the comparison.

## Visual and reproducibility

The silent visual is generated at:
`demo/castle-radio/.radio-data/comparison/current-vs-option-one.html`.
Its reusable source is `demo/castle-radio/light-comparison.fragment.html`.

There are two 20-second passages per song, chosen from complete windows away
from the intro/outro: the densest window and the window at the 75th percentile
of descending cue count. Monster Mash uses 40–60 s and 70–90 s; Day-o uses 40–60 s and 20–40 s.

`compare_lights.mjs` warms playback from zero, validates every pixel channel,
checks that every cue fires, and emits RGB8 samples plus normal/soft metrics.
Its CLI takes baseline JSON, candidate JSON and output JSON. Insert an array of
both output documents at the template's `__COMPARISON_DATA__` placeholder.
The self-contained visual has no network or audio calls. Play, Pause, shared
scrubbing and song/passage selection work without any device connection.

Example candidate generation:

```sh
.venv/bin/python demo/castle-radio/pulse_clarity.py radio_a1f0fc4d6545
node demo/castle-radio/compare_lights.mjs \
  demo/castle-radio/.radio-data/tracks/radio_a1f0fc4d6545.show.json \
  demo/castle-radio/.radio-data/comparison/radio_a1f0fc4d6545.clarity.show.json \
  demo/castle-radio/.radio-data/comparison/monster.json
```

## Validation

- Six algorithm tests cover competing hits, independent zones, fade recovery,
  sparse hits, protected swells, bounded suppression, source immutability and
  encoding preservation. A first fade test exposed a too-high decay floor;
  0.78 left 17.6% instead of the intended 15% at a 160 ms gap. The bounded
  floor was adjusted to 0.72 and the same test passed.
- Prepared-preview tests exercise checksum rejection and silent transport.
  A new real-renderer test verifies replacement, attacks, masks, independent
  fixtures and no duplicate cue execution in normal and soft modes.
- 75,900 frames rendered across both whole songs, both versions and both
  modes. All channels finite and within [0,1]; all cues executed.
- Both candidate binaries run through the actual C++ firmware cue reader on
  the Mac. Full traces agree with decoded records within 0.0001 printed float
  precision (C++ float versus Python double rounding of attack rise). This is
  scheduling/value evidence, not a physical brightness calibration.
- Browser checks exercise playback advance, pause/scrub and song/passage
  switching; no audio/video elements. Desktop and 320 px mobile layouts checked
  in light/dark themes. The real radio page was separately exercised against
  a loopback-only server: Monster Mash advanced 6.4 s / 57 cues while the audio
  element remained paused. The prepared preview used the original CRC.
  A state-save echo initially paused playback immediately; the visual now ignores
  its own saved-state echo, and playback was verified advancing afterwards.
- The first full suite caught a missing entry for the new script in the
  standalone site builder. Added the script to the builder's explicit list;
  broad verification was rerun after the correction.

## Final verification result

All required `make check` stages passed across the completed run and its
continuation: 1,193 main Python tests, 109 radio Python tests, 84 radio JS tests,
format/lint, mypy, Rust checks, image/line/citation guards, TypeScript and the
web renderer/parity suites. A final mypy annotation was added to the pulse
selection buckets; `make check -o audio -o test -o test-radio` then completed
the remaining stages without repeating the already-passing suites.
`make coverage-radio` passed at 72%. Full browser E2E was not run; targeted
browser interaction checks above were performed instead. Both original cue
CRCs were rechecked and unchanged. Logs and RGB samples are under the ignored
`.radio-data/comparison/` directory. No commit, push or deployment was made.

## Recommended next decision

Compare the dense and quieter passages first. If the candidate feels too sparse,
reduce the exclusion window before promoting it. Tune soft mode separately.
After agreeing on the visual balance, explicitly adopt the algorithm in show
preparation and perform one physical A/B with the actual diffuser and audio.
Screens cannot establish perceived brightness, diffusion or musical enjoyment.
