# Halloween Castle — Build — firmware, audio-driven light, previewer, custom tracks (§12–§12.8)

Part of the design record; the index is [`PROJECT_NOTES.md`](../../PROJECT_NOTES.md). Section numbers are global across the parts, so `§12.9` means the same thing in every file.

---

## 12. Build — implemented

Project scaffolded and validated. See [README.md](../../README.md) for usage.

```
scenes/scenes.yaml            ← source of truth (7 scenes, 46 cues)
  ├── tools/synth.py          numpy port of the previewer's Web Audio graph
  ├── tools/render_audio.py   → audio/NN_<id>.mp3   (1.52 MB, 61% of budget)
  ├── tools/gen_esphome.py    → firmware/generated/scenes.yaml
  ├── firmware/castle.yaml    device config — validated by esphome 2026.7.4
  └── firmware/castle_effects.h   effect engine (C++)
```

### 12.1 Things pinned down during the build

- **Python 3.13**, not 3.14 — ESPHome 2026 requires ≥3.12 and 3.14 has known
  breakage. Installed 2026.7.4.
- **Board `adafruit_feather_esp32s2` is known to ESPHome** but carries **no pin
  aliases**, so the config uses raw GPIO numbers. Authoritative map came from the
  arduino-esp32 variant header:
  `D5=5, D6=6, D9=9, D10=10, D11=11, D12=12, A0=18, A5=8, SCK=36, MOSI=35, MISO=37`.
- **`esp32_rmt_led_strip` supports `is_rgbw`** — confirmed against the installed
  component, so the RGBW Jewels work natively.
- **Speaker media player needs `framework: esp-idf`**, not arduino.
- **The announcement and media pipelines cannot share one speaker.** ESPHome wants
  a `mixer` component for that. Since the show plays one scene at a time, the
  config defines *only* the announcement pipeline and scenes play with
  `announcement: true`.
- **Play action is `media_player.speaker.play_on_device_media_file`** with
  `media_file: <id>` — files are embedded in flash, no SD card anywhere.
- **Per-file cap is 5 MB** in `audio_files_schema`; our largest is 411 KB.
- **Scene audio is peak-normalised to 0.89** and relative loudness is set per
  scene by `volume:` in scenes.yaml. Rendering quiet material quietly just moves
  it toward the DAC noise floor for no benefit.

### 12.2 The flash wall — and what it cost

The first compile **failed**. The earlier "~2.5 MB free after the ESPHome image"
estimate in §11 was wrong, and the real numbers are worth recording:

| | bytes | |
|---|---:|---|
| Binary with embedded audio | 2,610,304 | 2.49 MB |
| ESPHome default app slot | 1,835,008 | 1.75 MB |
| **Overflow** | **775,296** | **0.74 MB** |
| — of which, firmware alone | 1,014,912 | 0.97 MB |
| — of which, embedded audio | 1,595,392 | 1.52 MB |

The cause: ESPHome's default layout puts **two** 1.75 MB OTA slots in 4 MB of
flash. The binary fits in neither. That leaves **801 KB for audio** — we use 1558.

**Resolution: single app partition** (`firmware/partitions_single_app.csv`),
3.87 MB. Compiles at **64.2 % flash, 74.4 % RAM**.

*(Both the file and the build it served were deleted on 2026-09-01 — the same
wall came back taller once the show had real songs in it. §12.15.)*

**Cost: OTA is gone.** Over-the-air needs a second slot to write into while the
first runs. Two 2.5 MB slots do not fit in 4 MB. The `ota:` block is commented
out in `castle.yaml` rather than left in place failing silently.

**The alternative, if OTA matters more than fidelity:** audio must drop from
1558 KB to ~750 KB — roughly `bitrate: 48` plus trimming Vigil (30 s→14 s) and
Séance (35 s→29 s). That is a real quality hit on the music box and piano
transients; wind, thunder and organ drone would survive it better.

### 12.3 Verified by compiling

- `castle_effects.h` and the render lambda compile clean under esp-idf 5.5.5.
- `esp32_rmt_led_strip` accepts `is_rgbw: true` with 21 pixels.
- The generated scene scripts merge cleanly via `packages:`.

### 12.4 Still unverified — needs the board

- **Single-core MP3 decode alongside WiFi and RMT.** The open risk from §11.
  Compiling proves nothing about it. **RAM is at 74.4 %**, which is the number to
  watch — if it misbehaves, `buffer_size` (currently 250 KB) is the first dial.
- Whether the 74AHCT125 is needed in practice, or 3.3 V data drives the Jewels
  directly over a short run.

## 12.5 Dry-run work (2026-08-10, waiting on parts)

**Previewer is now firmware-faithful, generated end-to-end.**
`tools/gen_previewer.py` splices into the HTML between `@GEN-DATA` markers:
- scene data straight from scenes.yaml (cues, score, volumes, verbatim YAML
  slices for the source panel) — the hand-coded scene list is gone
- the rendered `audio/*.mp3` as data URIs (~2.1 MB base64), so the previewer
  plays **byte-identical audio to what ships in flash**; a toggle switches back
  to the live synth for parameter tweaking
- effect maths replaced with a line-for-line port of `castle_effects.h`,
  including the RGBW warm-white channel (screen-faked as 3000 K) and the
  firmware's per-pixel seed formula `z*4.7 + p*1.31`
- each aperture draws its 7 jewel pixels individually (1 centre + 6 ring,
  the physical layout), so per-pixel flame motion is visible pre-hardware

**Bench dry-run: `firmware/bench.yaml`** (`make bench`) — the bare Feather,
zero new parts, retires the §12.4 decode risk early:
- full audio pipeline runs into unconnected I2S pins; watch logs for underruns
- onboard NeoPixel (GPIO33, power GPIO21) plays towerL pixel 0 — candle
  flicker, strikes, palette changes all visible on the bare board
- overrides via package substitutions only (`pin_led_data: 33`,
  `led_rgbw: false`); the 20 phantom pixels fall off the end of the wire
- PIR pin gained a pulldown in the base config, so an unwired GPIO6 can't
  randomly fire Approach (also correct for the real AM312)

**Bug fixed while in there:** `render_audio.py` seeded numpy with Python's
salted `hash()`, so every render produced different bytes despite claiming
determinism. Now `zlib.crc32`; verified identical MD5 across runs.

---

## 12.6 Making audio actually drive the light (2026-08-10)

Justin, bluntly: *"lighting is bad."* Target scene: Crypt.

**Diagnosis, measured not guessed.** Sampled the previewer's own canvas at
each aperture centre for a full 24 s loop and compared zone statistics:

| | towerL mean | door mean |
|---|---|---|
| before | 69 | 25 |

The `eyes` effect was a bright *constant* red flooding the towers, tied to
nothing in the audio — and in the same colour as the heartbeat, so the one
thing that should own the scene was buried by the one thing that meant
nothing. No contrast, no correlation: that is what "bad lighting" was.

**Three changes, in order of importance.**

1. **Per-zone `level`** (`zone_level[3]` global, `level:` on set-cues and an
   optional scene `levels:` map). Scales the *base effect only* — strikes are
   unscaled. This is the mechanism that buys contrast: hold the standing
   effect low and the audio-driven pulses become the loudest thing in the
   room. Crypt runs `eyes` at `0.18`.

2. **Per-strike colour and decay** (`zone_flash_col[12]`, `zone_flash_decay[3]`).
   A strike is no longer always white with one global fall time. Lightning
   stays white and snappy (0.90); a heartbeat is blood red and faster (0.82);
   a bell toll blooms violet and falls slow (0.972). The previewer's sky/stone
   wash is tinted by the strike colour too, so a crypt thump breathes red onto
   the stonework instead of flashing it storm-blue.

3. **Markers became per-synth, with velocity.** `audio/markers.json` went from
   `{scene: [ms]}` to `{scene: {synth: [[ms, velocity]]}}`. Synths now report
   *both* heartbeat thumps (the dub at 0.55 of the lub), every whispered word
   (velocity from word length), and accented waltz phrase-heads. A scene's
   `pulse:` is now a *list of streams*, one per synth, each with its own zones,
   colour, intensity and decay — and `alternate: true` round-robins a stream
   across zones.

**Crypt now has a colour language, and every zone is driven by a sound:**

| colour | sound | zone | feel |
|---|---|---|---|
| red | heartbeat (39 thumps) | door, epicentre | snap, 0.82 |
| red, faint | heartbeat | both towers @ 0.10 | sympathetic throb |
| green | whispers (15 words) | towers, alternating | flicker, 0.94 |
| violet | toll (1) | all zones | bloom, 0.972 |

Nothing in the scene is lit by the clock any more except the two `eyes`
entrances, and those now sit *under* the pulses.

**Measured after:**

| | towerL mean | towerR mean | door mean | door median | door p90 |
|---|---|---|---|---|---|
| after | 28.4 | 28.1 | 66 | 27 | 202 |

The door went from the dimmest zone to the brightest, and its median/p90 gap
(27 → 202) is the dynamic range that makes a pulse read as a pulse. Canvas
capture confirmed the lub-dub in the pixels: `247 → 87 → 193` then a decay
tail, repeating at ~1.28 s, and a pure red `[255,15,16]` thump between tolls.

**Also fixed:** the *Arm audio* button was the only way to start sound, and it
was easy to miss. Browsers only require one user gesture, so the first
`pointerdown` or `keydown` anywhere on the page now arms everything — audio is
on by default in practice. The button remains as a visible hint. Verified by
instrumenting `HTMLMediaElement.play`: one click anywhere → crypt's 24 s file
playing, looping, clock advancing.

**Cost:** RAM 75.2% → 76.2%, flash 71.4% → 71.5%. Still fits.

---

## 12.7 Previewer transport rebuild (2026-08-10)

**Crypt towers came off true black.** Between whispers the towers were dead
pixels for seconds at a time. They now rest on `chill` at level 0.35 — a cold
near-dark. Being cold, it also separates the towers (voices) from the door
(heart) at rest, so the colour language holds even when nothing is firing.

**The audio model was wrong, twice, and the second version was worse.**

First the *Arm audio* overlay was the only way to start sound and was easy to
miss. The fix — arm on the first gesture anywhere — made the overlay's own text
("browsers hold audio until you click") a lie, and worse, meant any stray click
started a scene at full volume. Justin, correctly, called this broken.

The rule now: **muted by default, always, and audio starts only from Play.**
Nothing about loading the page, picking a scene, toggling a mode or clicking
anywhere else can put sound out of a speaker.

Mute is enforced with the element's own `muted` property, not by zeroing
volume. Volume is also written by the fade-in and by the master slider, so a
mute expressed as "volume 0" is one stray write from being silently undone —
which is exactly the class of bug being reported. `muted` is independent of
both and cannot be lost. `armAudio()` also builds the master gain at 0 when
muted, so the synth path obeys the same rule.

**Real bug found while rebuilding:** Stop forced every zone to `off`, and Play
resumed without re-deriving the scene, so *Stop → Play ran the whole scene on a
black stage*. `startPlayback()` now always calls `rebuildLightsAt()` first.

**Transport, rebuilt.**
- Play/Pause with a frozen clock (`state.held`) rather than a free-running one
- Scrub bar: drag to seek. Seeking calls `rebuildLightsAt()`, which replays
  every `set` cue from zero — without it, scrubbing leaves whatever effects
  happened to be on stage when you grabbed the bar, the classic seek bug.
  Verified locked: light clock 14.75 s against audio 14.71 s.
- **Cue ticks painted under the scrub bar in each strike's own colour**, height
  scaled by intensity. This is the fastest way to see whether light is
  following audio — the thing this project keeps needing to judge.
- Clicking a cue row in the sheet jumps to that cue
- Keyboard: space, ←/→ (shift = 5 s), Home, R, M, Esc, 1–9 for scenes
- 180 ms fade-in on scene audio: cutting a 44.1 kHz file in at an arbitrary
  sample is a step discontinuity — a click, and at the top of a loud scene, a
  blast
- Stale `DFPlayer DFR0299` chip in the header replaced with `MAX98357A I²S`

**Note to self on testing:** driving the previewer with the browser tools plays
audio on Justin's actual speakers. Pin `HTMLMediaElement.prototype.volume` to 0
in the test tab before touching the transport.

---

## 12.8 Custom tracks (2026-08-10)

Justin wants to bring in his own audio alongside the generated scenes.

**Tooling.** `yt-dlp` installed via brew (2026.07.04); `ffmpeg`, `lame` already
present. The linked GUI (bytePatrol/YT-DLP-GUI-for-MacOS) is a self-contained
`.app` with yt-dlp/ffmpeg bundled — fine to use for grabbing files by hand, but
the pipeline here drives the CLI so imports are scriptable and repeatable.

**`tools/import_track.py`** takes a local file or a URL and: converts to the
project format (mono / 44.1 kHz / 96 kbps), optionally trims (`--start`,
`--take`), reports the flash cost against the real budget, runs onset
detection, and prints a ready-to-paste scene block wired to whatever it found.

**`tools/analyze.py` — the interesting part.** The synths report their own
event times, so generated scenes are locked to their audio by construction. An
imported track can't do that, so its onsets are *detected*: spectral flux with
an adaptive (running-median) threshold, run separately in three bands.

| marker | band | typically | zone |
|---|---|---|---|
| `onset_low` | < 200 Hz | kick, heartbeat, pedal | door |
| `onset_mid` | 200–2000 Hz | voices, piano, melody | towers |
| `onset_high` | > 2000 Hz | cymbals, bells, sibilance | accents |

Banding matters more than it sounds: one undivided onset track gives every
zone the same pulse and the castle blinks as a single lamp. Per-band onsets
give the bass its own zone and let the towers answer the melody.

The detected onsets are emitted under the **same marker names the synths use**,
so `pulse:` streams work identically for imported and generated audio, and
`gen_esphome.py` / `gen_previewer.py` needed *no changes at all*.

**Verified against ground truth.** A synthetic file with kicks every 0.5 s and
hats offset by 0.25 s: 16/16 kicks in `onset_low`, 15/15 hats in `onset_high`,
all within 10 ms. First run missed the onset at exactly t=0 — frame 0 diffs
against itself — which would silently drop the downbeat of every loop. Fixed by
padding the front with silence and subtracting the offset back.

Also: imported audio defaults to **dry** (`reverb: 0.0`). It arrives already
produced, and stacking the stone hall on someone else's reverb is mud.

**The constraint to keep in view:** ~2.9 MB for *all* scenes combined, of which
the eight generated ones use 1.8 MB. At 96 kbps mono a minute costs ~700 KB, so
a full song does not fit alongside the existing show. `--take` a loopable
20–30 s section instead — which reads better on a porch anyway.

Files in `tracks/` are git-ignored (they're Justin's, and large); the scene
definitions referencing them are tracked, so the show stays reproducible.

## 12.15 One build, on the card (2026-09-01)

**Decision, in the operator's words:** *"The SD card should be the only
version, as it will always be run with an SD card because there's literally
not enough space."* `firmware/castle_flash.yaml` and
`firmware/partitions_single_app.csv` are deleted, and `castle_sd.yaml` is what
`make build`, `make upload`, `make logs` and `make validate` mean.

**What forced it.** §12.2 recorded the flash wall and bought past it with a
single 3.87 MB app partition, at the price of OTA. §12.8 recorded the ceiling
that replaced it — ~2.9 MB for all scenes combined, and *"a full song does not
fit alongside the existing show"*. Scenes 9 and 10 became full songs anyway,
because the card made that free, and `audio/` is 2.2 MB now: the image is
3.2 MB and esphome answers **"All app partitions are too small"**. There is no
bitrate that fixes this and leaves a show worth listening to. The wall won.

**Why nobody noticed for a week.** CI only ran `esphome config` on the flash
build — which resolves happily, since nothing about a config says how big the
binary will be — and its weekly *compile* job had always pointed at
`castle_sd.yaml`. So the build rotted with every check green. The validate
loop now lists only variants that can actually be built, which is the real
fix; `esphome config` is not a build and this is what that costs.

**Why it costs nothing.** The porch board has run the SD build since
2026-08-22. The desk, `tools/sd_sync.py`, `make publish` and `make ota` all
targeted it already. Deleting the other one removed a build nobody had flashed
in ten days and could not have flashed if they tried.

**What is genuinely lost.** The safety net: an empty card slot used to fall
back to embedded scenes, and now it plays a one-second chirp. `make publish`
is therefore part of flash day rather than an optional extra — the runbook and
`make help` both say so now. `bench.yaml` and `bench_audio.yaml` based on the
flash build for its embedded audio; they base on `castle_sd.yaml` and want the
show card in the wing to mean anything. And `bench.yaml` can no longer route
tower L to the onboard NeoPixel, because the show build claims GPIO33 for its
status pixel and ESPHome refuses two strips on one pin.

**Not lost:** OTA. The default dual-slot layout is the one this build has
always used, and with the audio off the image it has room to spare —
`tools/check_image.py` still measures against the 1.75 MB slot.
