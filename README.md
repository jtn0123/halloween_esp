# Halloween Castle

A store-bought decorative castle with three lit apertures — two tower windows and
a doorway — driven by an ESP32-S2 Feather running ESPHome. Addressable RGBW pixels,
pre-rendered spooky audio, and a cue engine that keeps the two in step.

See [PROJECT_NOTES.md](PROJECT_NOTES.md) for the design record and the hardware
research behind every choice here.

---

## How it fits together

```
scenes/scenes.yaml            ← THE SOURCE OF TRUTH
        │
        ├── tools/render_audio.py ──▶ audio/NN_<id>.mp3      (embedded in flash)
        ├── tools/gen_esphome.py  ──▶ firmware/generated/     (light cue scripts)
        └── previewer/            ──▶ browser cue desk        (tuning tool)
```

One file defines every scene: its light cues, its audio score, its length and its
playback level. Everything else is generated. Cue timings tuned in the previewer
cannot drift away from the ones on the device, because both come from here.

### Why the audio is pre-rendered

The MAX98357A plays one stream, and mixing on a single-core ESP32-S2 is not worth
fighting. So each scene is rendered offline into a single mixed file — where
convolution reverb, ducking and crossfades are free — and the firmware's only job
is to play it. This raises the quality ceiling rather than lowering it.

---

## Hardware

| | |
|---|---|
| MCU | Adafruit ESP32-S2 Feather — 240 MHz, 4 MB flash, 2 MB PSRAM |
| Audio | MAX98357A I2S class-D amp ([adafruit 3006](https://www.adafruit.com/product/3006)) → 4 Ω speaker |
| Light | 3 × NeoPixel Jewel, 7 RGBW pixels each = 21 in one chain |
| Sensor | AM312 PIR on the walkway |

I2S and RMT are separate peripherals on the ESP32-S2, so audio and pixels never
contend for hardware.

### Wiring

| Feather pin | GPIO | Goes to |
|---|---|---|
| D5 | 5 | 74AHCT125 → 470 Ω → Jewel `DIN` |
| D6 | 6 | AM312 PIR out |
| D10 | 10 | MAX98357A `DIN` |
| D11 | 11 | MAX98357A `BCLK` |
| D12 | 12 | MAX98357A `LRC` |
| USB | — | 5 V to amp, pixels, level shifter |
| GND | — | common ground |

Put a 1000 µF capacitor across 5 V/GND at the pixels. With 21 RGBW pixels the
worst case (a full-white lightning strike) is ~1.5 A, so **split the 5 V supply
before the Feather** rather than drawing it all through its USB trace.

---

## Getting started

```bash
make setup      # venv + esphome + render deps
make audio      # render the scene audio
make validate   # check the config without a toolchain
make build      # compile
make upload     # flash over USB
```

Copy `firmware/secrets.yaml` and set real WiFi credentials before flashing.

---

## Scenes

| Scene | Kind | Length | What happens |
|---|---|---|---|
| Vigil | ambient, loops | 30 s | Candles breathe, ember at the door, wind underneath |
| Storm | triggered | 6.5 s | Thunder first, strike 80 ms behind, rolling flashes |
| Séance | ambient, loops | 35 s | Organ procession i–♭II–i–V7♭9, violet with green wisps |
| Ballroom | ambient, loops | 15 s | Parlour waltz, music box over piano, violet↔green drift |
| Descent | showpiece | 27 s | 32′ pedal held throughout, chromatic descent, strikes on chord changes |
| Visitation | triggered | 11 s | Candles gutter out, cold green, eyes open in the left window |
| Approach | PIR | 8 s | Blackout, a beat of nothing, then the door blazes |

All musical material is original, written in the haunted-parlour idiom
(minor key, 3/4 or slow chords, raised 7th on the dominant). Nothing is a
transcription of an existing work.

### Effect vocabulary

`candle` · `ember` · `furnace` · `spirit` · `eyes` · `seance` · `wisp` ·
`mansion` · `chill` · `throb` · `strobe` · `off`, plus a global `strike` overlay.

Defined in [firmware/castle_effects.h](firmware/castle_effects.h). Flicker runs on
smoothed value noise, and the seed varies per pixel so flame moves *across* a
jewel rather than the whole window pulsing as one lamp.

---

## Safety

`Soften lightning` defaults **on**. Hard ~7 Hz white strobe sits in the
photosensitive seizure band, which matters for anything pointed at a public
walkway. Turning it off is a deliberate act.
