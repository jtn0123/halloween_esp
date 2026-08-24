# Halloween Castle

A store-bought decorative castle with three lit apertures — two tower windows and
a doorway — driven by an ESP32-S2 Feather running ESPHome. Addressable RGBW pixels,
pre-rendered spooky audio, and a cue engine that keeps the two in step.

See [PROJECT_NOTES.md](PROJECT_NOTES.md) for the design record and the hardware
research behind every choice here — it is an index; the record itself lives in
parts under [`docs/notes/`](docs/notes/). For the night-to-night view —
adding a song end to end, pushing to the castle, what to check when a scene
will not play — see [docs/RUNBOOK.md](docs/RUNBOOK.md).

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
| Light | 2 × NeoPixel Jewel 7 RGBW (towers) + NeoPixel Ring 12 RGB (door) — three zones, 26 pixels; see [docs/WIRING.md](docs/WIRING.md) |
| Sensor | AM312 PIR on the walkway |

I2S and RMT are separate peripherals on the ESP32-S2, so audio and pixels never
contend for hardware.

### Wiring

| Feather pin | GPIO | Goes to |
|---|---|---|
| A0 | 18 | 74AHCT125 → 470 Ω → Jewel `DIN` |
| A1 | 17 | AM312 PIR out |
| A3 | 15 | MAX98357A `DIN` |
| D11 | 11 | MAX98357A `BCLK` |
| D12 | 12 | MAX98357A `LRC` |
| USB | — | 5 V to amp, pixels, level shifter |
| GND | — | common ground |

**Why not D5/D6/D10, which would be the obvious choices?** The 2.13" eInk
FeatherWing — the thing that carries the microSD slot — hard-wires exactly
those: SD chip select on D5, SRAM chip select on D6, eInk chip select on D9,
eInk data/command on D10. Only the first two are cuttable, and putting
800 kHz NeoPixel data on the SD card's chip select is not a mistake you find
quickly. D11/D12/D13 are untouched by the wing, and A0–A3 are free.

If you are **not** stacking the wing, those three signals can move back to
D5/D6/D10 by editing the substitutions at the top of `firmware/castle.yaml` —
nothing else refers to them.

Put a 1000 µF capacitor across 5 V/GND at the pixels. With 26 pixels the
worst case (a full-white lightning strike) is ~2 A, so **split the 5 V supply
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

Copy `firmware/secrets.yaml.example` to `firmware/secrets.yaml` and set real
WiFi credentials before flashing. `make help` lists every target.

---

## The cue desk

```bash
make studio     # http://127.0.0.1:8765 — the previewer plus a local server
```

The previewer is one static HTML file (`previewer/castle-cue-desk.html`, built
by `make preview` from `web/src/`). Behind it, `tools/studio.py` adds what a
static page cannot do: the **Tracks** panel imports audio (a file, or a link via
yt-dlp), shows onsets and waveforms, auditions clips, writes scenes into
`scenes/scenes.yaml`, and sends files to the castle's SD card when one answers.
`--lan` opens it to the phone/iPad remote; leave it off otherwise — a LAN
visitor has the whole desk, not a read-only view: they can import and delete
tracks, rewrite `scenes/scenes.yaml`, send files to the castle and stop the
server (`POST /studio/server/stop`), with no login. The route
table — what the studio owns (`/studio/…`) and what it relays to the castle
(`/api/…`) — is [docs/API.md](docs/API.md).

Three environment variables sandbox it: `CASTLE_TRACKS` (track library
directory), `CASTLE_SCENES` (the scenes file it may write), `CASTLE_HOST`
(the castle's address; set-but-empty means "no castle"). The tests set all
three so a run can never touch the real show. `tools/castle_emu.py 8093` plus
`CASTLE_HOST=127.0.0.1:8093` gives the whole desk→studio→castle chain with no
hardware at all.

---

## Development

```bash
make test       # python unit tests         make lint   # ruff + mypy
make check      # tests + lint + guards + tsc + node suites (what CI runs)
make e2e        # Playwright against the real studio (CASTLE_E2E_PORT=8821 to run two)
make coverage   # non-gating coverage report     make audit  # pip-audit, non-gating
```

The four implementations of the show's arithmetic (Python generators, TS
effects, C++ firmware, host-compiled dump) are kept bit-exact by seeded fuzz —
[docs/PARITY.md](docs/PARITY.md) is the contract and what to do when it goes red.

Every file is held to 500 lines (`tools/check_loc.py`, prose included).
`make setup` installs the commit hook (`git config core.hooksPath githooks`).
A git worktree needs its own `.venv` and `web/node_modules`, or symlinks back
to the main checkout's. The short version of all of this is
[CONTRIBUTING.md](CONTRIBUTING.md).

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
