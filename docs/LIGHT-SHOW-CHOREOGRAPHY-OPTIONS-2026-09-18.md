# Silent choreography preview options

The comparison page now includes the original pulse-clarity candidate plus
three stronger choreography experiments. These are local preview candidates;
none is installed, registered as a default, or uploaded. Original prepared
cue CRCs remain `1b80e0f6` (Monster Mash) and `37e69d96` (Day-o).

## Choices

- **2: Alternating towers.** Strong backing accents alternate between amber
  left and violet right. The door retains selected vocal onset times.
- **3: Tower-to-door response.** Alternating towers, with a green door-ring
  response 144 ms after every third selected backing accent. This intentionally
  replaces the original door/vocal routing.
- **4: Full-castle accents.** Alternation with a coordinated amber strike on
  all three fixtures every fourth selected backing accent, and occasional
  delayed green door responses. This also changes the original routing.

All three deliberately lower base levels to 0.12 and increase selected pulse
intensity to 0.5–0.85. These are aesthetic changes beyond pulse timing, not
isolated tests of alternation alone. Existing backing onsets supply timestamps;
strongest-first selection keeps backing accents at least 220 ms apart and
vocal accents at least 180 ms apart. Accent counts are not detected musical
beats or bars. There is no new verse/chorus detector in this experiment.

## Implementation and evidence

Generated candidate cue files, decoded previews, sampled RGB frames and
reproduction scripts live under the ignored directory
`demo/castle-radio/.radio-data/comparison/`:

- `build_options.py`: generate six candidates from both original prepared shows.
- `build_options_page.py`: add options to the existing synchronized comparison.
- `*.option[234].cue` and matching `.show.json`: actual encoded candidates.
- `monster-option[234].json` / `dayo-option[234].json`: full renderer results.
- `options-firmware-validation.json`: all six real C++ cue-reader traces pass
  against decoded records within 0.0001 printed float precision.

Each candidate was exercised with its baseline across the complete song in
normal and soft modes using `compare_lights.mjs`: 227,700 total simulated
frames including repeated baseline runs. Every cue executes; every rendered
channel is finite and within [0,1]. Playback runs at 16 ms simulation ticks;
the comparison displays a sampled frame every 64 ms to keep its embedded data
compact. Short flashes can therefore look less distinct than in full-rate
playback. No physical brightness or subjective musical quality is proven.

Browser checks confirmed all four choices change the candidate canvas while
retaining the baseline frame, playback advances and ends, song switching and
scrubbing work, and there are no audio/video elements. No horizontal overflow
at 320 px. The renderer uses the same gain for both sides; differing base
levels and cue intensities are intentional properties of each candidate.

The existing local URL now serves these choices:
`http://127.0.0.1:8894/all-three-preview.html`.
The standalone page is `all-three-preview.html` in the comparison directory.
Only the local comparison and this report changed; no production algorithm
or physical device changed in this step. Full repository tests were not rerun
for these isolated preview artifacts.
