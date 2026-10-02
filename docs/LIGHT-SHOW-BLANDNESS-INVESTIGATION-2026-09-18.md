# Why newer imported light shows look bland

Investigated 2026-09-18 against checkout `f5f12ad` and the live castle at `10.27.27.81`, firmware **5.70** (compiled September 17). Scope: investigation only. No application, firmware, show-data, settings, playback, or deployment changes were made. This report is the only repository change.

## Conclusion

There is a concrete pipeline difference that strongly explains the reported loss of variation. Older authored songs use rich, locally executed cue timelines. New Castle Radio imports are analyzed in detail, but their normal sync copies only audio. Their device lighting is reduced to a browser/Mac stream of whole-zone solid colors, at most four updates per second **across the entire castle**, not four per light.

The richer engine and the earlier beat/tempo improvements have not disappeared. The newer import-to-device workflow bypasses much of them. Firmware gained support for full imported-song cue files in v5.63, but Castle Radio's import/sync path is not connected to that full-show producer.

Confidence is high in this mechanism: it is supported by the current source, actual SD inventory, decoded on-device cue files, and the actual page served by the device. I did not start a song or physically observe the lights, so this is not a witnessed visual A/B test. The user did not identify particular songs; the comparison uses the older authored songs and the current newer import catalog found on this machine/device.

## What is actually on the device

Read-only GETs: `/api/status`, `/api/health`, `/api/files`, `/api/files?d=scenes`, `/`, and the two older scene `.cue` files under `/sd/scenes/`.

- Device was idle: `scene: stop`, `playing: false`, `cues: 0`. Zero cues while idle is normal and is not evidence of a failed show load.
- Card mounted; `missing` empty; 1,982 KB PSRAM free; 208 KB internal heap free.
- Health: zero recorded crashes, zero SD read errors, 170 KB minimum heap this boot. These readings do not exclude every hardware issue, but do not support storage failure or memory exhaustion as the main explanation.
- All five root-level `radio_…` audio files lack matching `.cue` files: `radio_d0183bda099a`, `radio_531d0eb77655`, `radio_83c87979a2af`, `radio_a1f0fc4d6545`, and `radio_4965453bf420`.
- The older songs have full scene cue files: `scenes/the_citizens_of_halloween___this.cue` and `scenes/the_ballad_of_the_witches__road_.cue`.
- The card also contains `cit_before.cue` and `cit_after.cue`; those are separate artifacts, not cue files for any `radio_…` song.
- The live page contains the streaming runner and the `castle_cues` delegation guard. Its embedded `radio-library` contains **Monster Mash**, with 715 frames. The current local catalog contains Monster Mash and **Day-o**. Thus the device page's embedded catalog also lags the local catalog; this is separate from the quality loss. The Mac helper can supplement a page's catalog, so this alone does not establish whether Day-o is accessible in a particular browser session.

### Measured show content

Older figures below come from decoding actual device files with `tools/cue_file.py:decode`, not merely counting YAML source. Newer figures come from the local catalog through the actual `imported_light_frames` reducer. Monster Mash's 715-frame count also matches the live page.

| Song | Duration | Detailed input / stored timeline | Device lighting output |
| --- | ---: | --- | --- |
| This Is Halloween | 193.36 s | 1,258 records: 1,216 strikes + 42 effect/level changes | 306 distinct RGBW strike colors; center/scatter/all masks; 39 all-zone strikes; different tower palettes, ember centers, door sparkle |
| Witches' Road | 200.54 s | 718 records: 637 strikes + 81 effect/level changes | 250 distinct RGBW strike colors; center/scatter/all masks; 12 all-zone strikes; different tower palettes, ember centers, door sparkle |
| Monster Mash (`radio_a1f0fc4d6545`) | 185.934 s | 4,528 analyzed hits, split voice/backing | 715 solid-color updates; only **15.8%** of input events retained |
| Day-o (`radio_4965453bf420`) | 117.621 s | 1,559 analyzed hits, split voice/backing | 352 solid-color updates; only **22.6%** retained by the current reducer |

This is a structural comparison between different songs, not a controlled music-quality benchmark. Hit counts are detected onsets across layers/bands, **not musical BPM**. Losing 84.2% or 77.4% of those events does not measure an equivalent percentage of perceived quality, but establishes the large reduction in available timing and spatial information.

The older strike color counts measure encoded color values, not named palettes or guaranteed perceptually distinct colors. Streamed imports have three fixed RGB hues, with changing brightness.

Device cue SHA-256 fingerprints:

```text
This Is Halloween fefaa77ab20f1f72e98ffda2138c42a9cab1165961504255e264211bd500c21d
Witches' Road     b3fbf022248bfd8dc67fcab60af0be5d861f31102835a2a27bd0bb951843c5f0
```

## Findings and causal chain

### 1. Normal Radio sync delivers audio, not a standalone rich show

`demo/castle-radio/radio_jobs.py:prepare` analyzes the song and saves a catalog containing `[time, zone, intensity, decay]` cues. With successful separation, vocals map to the door, and left/right backing to the towers. It also writes a simple YAML recipe using `tools/import_scene.py:scene_block`.

`demo/castle-radio/remote_library.py:start` resolves the audio file, reads that one file, and queues its upload. It does not render or upload a matching `.cue`. Its inventory explicitly calls a present imported track `audio_only`.

`tools/render_cues.py` already provides a richer producer: it uses an authored scene when available, otherwise calls the original desk's scene builder through `web/src/scene_cli.ts`, then expands the pulses and encodes a card cue file. `tools/sd_sync.py:cmd_cues` can upload those files. Neither is invoked by the Radio import/sync path examined here. The Radio library also lives under `.radio-data/tracks`, distinct from the usual top-level track library, so integration needs deliberate path and data handling.

Firmware `castle_cues.yaml:cues_begin` loads a companion file for raw playback when present. Without one, it leaves `g_cues` at zero and returns. Both Radio runners defer to firmware when it reports loaded cues, but the current imported files cannot take that branch because their companion files are absent.

### 2. The fallback collapses rhythm and spatial detail

`demo/castle-radio/light_show.py:imported_light_frames`:

1. Buckets every input event into `floor(time / 0.25) * 0.25`.
2. Keeps only the strongest event in each bucket, across all three zones.
3. Emits a single `zone:RRGGBB@brightness` command.

Consequences:

- At most four commands per second total. Simultaneous voice and backing hits compete rather than light multiple zones together.
- Timing moves to the bucket start, up to just under 250 ms earlier than the detected event, before transport latency is considered.
- Fixed hues: left purple `a832ff`, door red/orange `ff1f05`, right green `4dff8c`.
- Decay (the fourth input field) is discarded. There is no attack, RGBW strike color, pixel mask, alternate routing, or section transition in the outgoing command.
- Browser `castle-direct.js:runShow` can additionally skip overdue frames to avoid bursting commands after scheduling delays. That is defensive transport behavior, not restoration of lost choreography.

The reduction is understandable for the firmware's approximately 200 ms web-command mailbox. It is not a limit of the local cue engine, which ticks every 16 ms and can consume multiple due records. Increasing streaming traffic alone would not fix the missing show semantics.

### 3. The commands disable animation on the affected zone

`firmware/generated/lights.yaml:lights_override` handles hexadecimal colors by calling `set_effect("None")` and `set_rgbw(..., 0.0f)`. Its own comment says: “Park on a colour, no animation.”

The initial Radio command enables `show`, but each later streamed color disables the Show effect for its addressed strip. Once all zones have received colors, their ongoing behavior is successive solid brightness/color settings, rather than the per-pixel effect plus a decaying strike. There is no per-hit return-to-dark instruction in these frames. This directly explains why many analyzed hits can still look flat or persistently lit.

Source chain: `device_bridge.py:command` → `light_show.py`; device-hosted path: `device_site.py:catalog_rows` → `castle-direct.js:runShow`; firmware: `castle_web_actions.yaml` LIGHT dispatch → generated `lights_override`.

### 4. The old songs carry substantially richer choreography

`scenes/scenes.yaml:351–end` contains the two older imported scenes. Features include:

- Color gradients and hotter accent colors, rather than one hue per zone.
- Velocity-selected center/scatter/all pixel masks.
- Alternating tower/zone routing and strong bass strikes across multiple zones.
- Attack ramps and tempo-adjusted decays.
- Separate center effects, tower palettes/phases, and door sparkle.
- Hush, verse, pre-chorus dimming, chorus, and silence changes.

The new `tools/import_scene.py:scene_block` recipe uses fixed per-band zones/colors, density-adjusted intensity/decay, a fixed base, and `cues: []`. It does not recreate those richer scene features. Merely encoding that simple recipe into `.cue` would restore native pulses but would not, by itself, match the older show style.

### 5. Earlier beat-density patches still exist, but are not carried through

There are two relevant mechanisms:

- `tools/import_scene.py:fit_to_density` chooses decay and reduces intensity for dense hits, aiming for roughly 10% pulse brightness by the next median hit interval. Radio's `zone_cues` uses it and stores the resulting decay.
- `web/src/track_lights.ts:tempoFactor/tempoDecay` and `tools/pulse_dynamics.py:tempo_factor/tempo_decay` scale decay using median hit spacing. `tools/pulse_expand.py` applies this in the rich expansion path.

Radio's reducer retains intensity but drops decay, and solid-color commands cannot express a pulse tail anyway. Thus the beat-density work exists in source and parts of the preview, without delivering its intended physical behavior through this fallback.

More detected events or successful voice separation will not overcome this bottleneck: the split Monster Mash analysis produced 4,528 hits and still collapsed to 715 updates.

### 6. Newer preview settings are not a device-show export

`demo/castle-radio/preview.js:makeScene` creates browser strikes using preview-selected style, palette, intensity, and the saved decay. Physical frames use the reducer's fixed hues and catalog intensities instead. `desktop_tools.py:catalog` also produces those same reduced frames.

The page itself labels the action “Save preview style” and says these style choices shape the browser preview. Therefore improving the preview with these controls does not imply an equivalent improvement on the device. The preview renderer may be shared, but its inputs and device delivery are not equivalent.

## Relevant history

| Commit / date | Relevance |
| --- | --- |
| `6bd333e`, August 11 | Per-band zones/thresholds, fades, snap-to-beat |
| `c8c2beb`, August 12 | Rich track lighting: color ramps, velocity masks, sections, movement |
| `03d049e`, August 13 | Section dynamics, near-black silence, chorus pre-dimming, hush gating |
| `6528dc5`, August 13 | Optional palette drift, chorus takeover, sustained swells |
| `6466c1e`, August 21 | Old generated-script memory ceiling capped pulses at 200 |
| `35ac547`, September 15 | Castle Radio control room added |
| `d15b372`, September 17 | v5.63 supports full companion cue files for raw songs |
| `6752d71`, September 17 | Scenes move to card timelines; script-era pulse thinning removed |

The former 200-pulse cap is **not** the current explanation for these older scenes: the device files actually contain 1,216 and 637 strikes. Current `gen_scene_cards.py` keeps the full expansion. The native system now has more room for detailed shows; the Radio delivery gap remains.

Optional flavor toggles in `web/src/track_style.ts` default off; their mere presence does not prove that any specific historical song used them. The confirmed older-song features above come from the actual scene definitions and device files.

## Verification and limitations

All device interactions were GET requests. No playback, stop, sync, publish, settings change, reboot, or flash was requested. Source inspection and in-memory analysis did not regenerate shows.

Existing focused tests run with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s <directory> -p <filename> -q`:

| Directory / test | Result |
| --- | --- |
| `tests/test_cue_file_cxx.py` | 3 passed; exercises the actual C++ cue reader |
| `tests/test_scene_cue_equivalence.py` | 4 passed |
| `tests/test_pulse_cap.py` | 4 passed |
| `demo/castle-radio/test_device_bridge.py` | 36 passed |
| `demo/castle-radio/test_device_site.py` | 10 passed |

Total: **57 passed**. An initial pytest invocation could not run because pytest is absent from this virtualenv; the existing unittest suites were then run directly. Nothing was installed. Passing tests establish the checked implementation contracts; they do not assert that the reduced Radio show is visually equivalent to the rich authored show.

Not established: the exact song and playback route used when the user noticed the problem; current browser-local settings; perceived quality of a same-song physical comparison; whether any additional intermittent transport loss occurs during that playback. Boot-wide counters (`light_applied: 62`, `light_evicted: 2`) are not a measurement of the reported session.

## Proposed follow-up — not implemented

1. Connect Castle Radio import/sync to the rich show builder and companion `.cue` export, preserving intended voice/backing routing rather than silently replacing it with mixed-audio analysis.
2. Use one explicit show representation for preview and device export, including style, color, pixel patterns, sections, density/tempo handling, and routing choices.
3. Transfer and verify both audio and cue data; distinguish a complete native show from audio-only/streamed fallback in readiness reporting.
4. Keep reduced streaming only as an explicitly identified fallback. Do not try to solve this solely by increasing commands per second.
5. Validate on the same song: compare exported preview inputs with decoded cue records, confirm nonzero loaded cues during actual playback, verify the client sends no competing solid-color frames, then perform a physical A/B including quiet, dense, and chorus passages.

No fixes were applied as part of this investigation.
