# Halloween Castle — Mac-side mockup and bill of materials (§10–§11)

Part of the design record; the index is [`PROJECT_NOTES.md`](../../PROJECT_NOTES.md). Section numbers are global across the parts, so `§12.9` means the same thing in every file.

---

## 10. Mac-side mockup (design everything before hardware arrives)

Goal: hear the audio and see the light effects **on the Mac**, tune the timing, and
only then flash a board. Reflashing a Feather forty times to tune a flicker is
miserable; a browser reload is instant.

### 10.1 Shape of it

A **browser-based castle previewer**:
- Canvas rendering of the castle with LED positions mapped to pixel indices
- **Web Audio** for sound — spooky effects synthesized procedurally (wind = filtered
  noise, thunder = low-passed noise burst + rumble), so we need **zero downloaded
  assets** to start prototyping
- Scene timeline with a scrubber, plus live sliders for flicker/lightning parameters
- Runs as an Artifact — no install, and shareable

### 10.2 The important architectural bit

**One scene definition file is the source of truth**, consumed by *both* the
previewer and an ESPHome config generator. What you tuned is literally what gets
flashed — no hand-translation drift.

```yaml
scenes:
  storm:
    - t: 0ms     audio: folder(02, 001)      # thunder — fires FIRST
    - t: 80ms    led:   flash(all, white, 60ms)
    - t: 220ms   led:   flash(all, white, 40ms)
    - t: 900ms   led:   fade(ambient_purple, 2s)
```

This mirrors the pattern already in the `garage_fan` repo (`gen_web_page.py`,
`gen_device_header.py` as PlatformIO pre-scripts generating code from config).
Same habit, new project.

### 10.3 Built — Castle Cue Desk

**Live:** https://claude.ai/code/artifact/ce079a34-b185-4d33-974b-9edac5a6ae81
**Source:** [`previewer/castle-cue-desk.html`](../../previewer/castle-cue-desk.html)

Castle geometry per Justin: **two tower windows + one door = 3 zones.**

| Zone | Ch | Notes |
|---|---|---|
| `towerL` | 1 | Tower window, stage left |
| `towerR` | 2 | Tower window, stage right |
| `door` | 3 | Doorway, centre |

**Effect vocabulary**

| Effect | Look |
|---|---|
| `candle` | Warm amber flicker on smoothed fbm noise |
| `ember` | Low steady door glow |
| `furnace` | Hot doorway blaze |
| `spirit` | Cold breathing green |
| `eyes` | Red, irregular blink |
| `seance` | Violet breathing — mansion palette |
| `wisp` | Jittery will-o-wisp green, noise-driven |
| `mansion` | Slow violet↔green crossfade |
| `chill` | Deep low violet, doorway |
| `off` | Dark |

Plus a global `strike` overlay for lightning.

**Scenes:** Vigil (loop) · Storm · Séance (loop) · Ballroom (loop) · Visitation · Approach (PIR).

**Audio** — all synthesized in Web Audio, zero downloaded assets. Requires a click
to arm (browser autoplay policy).

- *Atmosphere:* wind (bandpassed noise + LFO), thunder (noise burst + lowpass sweep + sub rumble), creak, shriek, bell toll
- *Music:* **original** material in the haunted-parlour idiom — A minor, 3/4, raised
  7th on the dominant. Not transcriptions of any existing composition.
  - `waltz` — piano bass on the downbeat, triad on 2 and 3, music box melody over the top
  - `organ` — pipe organ procession i–VI–iv–V, additive drawbar synthesis (5 sine partials)
  - `musicbox` — descending celesta figure, stinger

**Controls:** candle depth/rate, **mansion balance** (violet↔green), master
brightness, master volume, **DFPlayer latency offset**, and a
**soften-lightning** toggle (auto-on under `prefers-reduced-motion`; recommend
shipping it on the real castle — hard white strobe on a public porch is a
photosensitivity risk).

**Implementation notes:** one-shot cues route through a `showBus` gain node so a
scene change cuts the previous cue wholesale, rather than tracking every scheduled
oscillator. The wind bed sits outside that bus — it's persistent, not a cue.

### 10.4 Honest fidelity caveats

The previewer shows **intent, not hardware truth**:
- **WS2812 colour ≠ sRGB on a monitor.** Gamma and low-brightness behaviour differ
  a lot, and flicker effects live exactly in that low-brightness region. Expect a
  retune pass on real hardware.
- **DFPlayer trigger latency (~50–100 ms, varies by chip)** won't exist in the sim.
  Build in a configurable latency offset so timing tuned on the Mac survives the
  move to hardware.
- **Synthesized preview audio ≠ the MP3s** that end up on the SD card. Swap real
  samples in before final timing tuning.

---

## 11. Bill of materials

### 11.1 Board facts that constrain everything

Verified against the Adafruit pinout for the ESP32-S2 Feather:

- **`USB` pin = +5 V from the USB-C rail.** This is where the DFPlayer and the
  pixels get their 5 V. There is no other 5 V source on the board.
- **3.3 V regulator is 500 mA peak.** Do not hang pixels or the amp off it.
- Broken out: `A0–A5`, `D5`, `D6`, `D9–D13`, `SCK/MOSI/MISO`, `RX/TX`, `SDA/SCL`.
  No bootstrapping pins to avoid — Adafruit states none are special.

### 11.2 The list

| # | Part | Qty | ~Cost | Why this one |
|---|---|---|---|---|
| 1 | **DFPlayer Mini — genuine DFRobot DFR0299** | 2 | ~$9 ea | The whole audio subsystem. Buy two; the clone failure mode is silent weirdness and a spare isolates "bad module" from "bad wiring" instantly. See §9. |
| 2 | **NeoPixel Diffused 8 mm Through-Hole — 5 pack** (Adafruit 1734) | 1 | **$4.95** | Three zones, two spares. Already diffused, 5 V, chains on one data line. WS2812B/SK6812. |
| 3 | **74AHCT125 level shifter** | 1 | ~$1.50 | 5 V pixels want ~3.5 V logic high; the S2 gives 3.3 V. Marginal. Cheap insurance. |
| 4 | **5 V 3 A USB-C power supply** | 1 | ~$10 | Peak draw ~1.3 A. A phone charger will not reliably do this. |
| 5 | **Speaker, 4 Ω 3 W, 2–3 inch** | 1–2 | ~$5 ea | *Only if the harvested RAK speakers don't measure up — see §11.5.* |
| 6 | **AM312 PIR sensor** | 1 | ~$3 | 3.3 V native, unlike the HC-SR501 which wants 5 V. Smaller too. |
| 7 | **Passives:** 470 Ω, 1 kΩ, 1000 µF ≥6.3 V electrolytic | — | ~$2 | Standard practice, see §11.4. |
| 8 | **PAM8403 stereo amp** | 1 | ~$2 | *Only if* you want stereo — DFPlayer's onboard amp is mono. |
| 9 | Hookup wire 22–26 AWG, heat-shrink, Feather proto wing | — | ~$10 | Assembly. |
| 10 | Vellum / thin white acrylic | — | ~$5 | Window diffuser. Matters more than it sounds — see §11.6. |

**~$55 all in**, or ~$40 skipping the spare DFPlayer and stereo amp.
*Prices approximate except the Adafruit 1734, which is confirmed at $4.95.*

**Already owned, no purchase:** ESP32-S2 Feather · 32 GB microSD · 2 speakers
harvested from the RAK18060.

### 11.3 Power plan

```
5V 3A USB-C supply ──USB-C──▶ ESP32-S2 Feather
                                   │
                              [USB pin] = +5V rail
                                   ├──▶ DFPlayer VCC
                                   └──▶ NeoPixel 5V (+ 1000 µF across 5V/GND)
                              common GND throughout
```

Budget: pixels ~180 mA worst case (realistically 60–90 mA at our brightness),
DFPlayer amp ~1 A peak into 4 Ω, Feather ~120 mA with WiFi. **~1.3 A peak.**

All of it flows through the Feather's USB connector and trace. That's acceptable
at this load, but if brownouts or resets appear under loud playback, split the
supply *before* the Feather and feed the three loads in parallel instead.

### 11.4 Wiring map

| Feather pin | Goes to | Notes |
|---|---|---|
| `TX` | DFPlayer `RX` | **through a 1 kΩ series resistor** — the standard noise fix |
| `RX` | DFPlayer `TX` | needed for playback-finished feedback |
| `D5` | DFPlayer `BUSY` | as an ESPHome `binary_sensor` — the *authoritative* "is it playing", since clone feedback is unreliable |
| `D6` | 74AHCT125 → pixel `DIN` | **470 Ω in series** at the first pixel |
| `D9` | AM312 PIR out | 3.3 V logic, direct |
| `USB` | DFPlayer VCC, pixel 5 V, 74AHCT125 Vcc | |
| `GND` | everything | single common ground |

DFPlayer `SPK_1` / `SPK_2` → speaker. That's a **bridge-tied mono** output —
do not ground either leg, and do not parallel two speakers across it.

### 11.5 Speakers — resolved

**The RAK speakers are 4 Ω / 3 W / 93 dB SPL @ 10 cm.** Electrically that is a
*perfect* match — DFPlayer's 3 W rating is specified at exactly 4 Ω. Wire one
straight to `SPK_1`/`SPK_2` and it works.

The remaining problem is cone area, not impedance. They're tiny, so they roll off
long before the 32′ and 16′ organ ranks in the Descent piece. That content simply
won't be reproduced — physics, not settings.

**Fix, and it's cheap: Adafruit 1314 — 3″ 4 Ω 3 W speaker, $1.95.** Same impedance,
same power rating, roughly nine times the cone area. This is the single highest
value-per-dollar item in the entire build.

**Mono vs stereo:** start mono. Across a castle this size the two apertures are a
few inches apart, so stereo separation is imperceptible — and **mono through a 3″
driver beats stereo through two 30 mm drivers by a wide margin.** Keep the RAK
speakers as spares; add the PAM8403 later only if you want stereo for its own sake.

Note the 3″ needs somewhere to live — it has four mounting tabs 60 mm apart. Try
the castle body as the enclosure first; an unbaffled driver cancels its own bass.

### 11.7 Where to buy what

Split by where it actually matters.

**DigiKey or Mouser** — *the one place source genuinely matters*
- **DFR0299 — genuine DFPlayer Mini.** Confirmed in authorized stock at both.
  This is the entire point of §9: buying here skips the clone lottery outright.
- 470 Ω, 1 kΩ resistors, 1000 µF electrolytic

**Adafruit** — best quality-per-dollar, and their guides match their parts
- **1734** — NeoPixel Diffused 8 mm Through-Hole, 5-pack — **$4.95** ✅ confirmed
- **1314** — Speaker 3″ 4 Ω 3 W — **$1.95** ✅ confirmed
- **1787** — 74AHCT125 level shifter — ~$1.50
- **2884** — FeatherWing Proto — ~$4.95

**Amazon** — fine for commodity items, where a clone is just a clone
- 5 V 3 A USB-C supply · AM312 PIR · PAM8403 (only if going stereo) ·
  vellum/diffuser · hookup wire · heat-shrink

⚠️ **Do not buy the DFPlayer on Amazon.** That marketplace is the primary source
of the MH2024K clones documented in §9.

### 11.6 DFPlayer — honest assessment

**Verdict: right for this build, not a good audio device in absolute terms.**

Weaknesses, all documented by users:
- **Noise floor** — background hiss and clicking, worse at volume; SD access induces
  clicks. Powering the module down when idle cures the hiss but adds a startup pop.
- **Bass roll-off on `DAC_L`/`DAC_R`** — the coupling caps are 0.1 µF, which strongly
  attenuates low frequencies. Directly relevant to the 32′/16′ organ content.
  Fix is 10 µF replacements — **or just use `SPK_1`/`SPK_2`**, whose path doesn't go
  through them. *This is now a reason to prefer the built-in amp over the line out.*
- **Coarse volume** — 30 steps, badly spaced at the bottom.

#### ⚠ It plays ONE track at a time

A stinger does not layer over the ambient bed — it **replaces** it. This breaks
scenes already designed: Séance (toll over organ), Ballroom (music box over waltz),
and Vigil's assumption that wind runs continuously underneath.

**Resolution: pre-render each scene as a single mixed MP3.** The cue sheets are
already timestamped, so a scene becomes *one audio file + a light cue list* — which
is exactly what the show engine consumes.

This raises the ceiling rather than lowering it. Mixing, ducking, crossfades and
reverb all happen offline with unlimited CPU instead of being impossible on a $9
module. The DFPlayer's only job becomes "play file 4", which it does reliably.

Consequence: the **previewer becomes the authoring tool** — it should generate both
the mixed audio and the matching cue list. The second DFPlayer stays a spare rather
than a second audio channel.

### 11.8 Gotchas that will bite

1. **The microSD must be FAT32, not exFAT.** DFPlayer supports FAT16/FAT32 up to
   32 GB — your card is exactly at the ceiling, and macOS formats 32 GB cards as
   exFAT by default. Disk Utility → *MS-DOS (FAT)*, or if that misbehaves:
   `diskutil eraseDisk FAT32 SPOOKY MBRFormat /dev/diskN`
2. **These pixels are RGB order, not GRB.** Adafruit's 8 mm through-hole NeoPixels
   differ from standard 5050s. In ESPHome: `rgb_order: RGB`. Symptom if missed —
   the candle flicker comes out green.
3. **Diffusion is not optional.** Even "diffused" LEDs read as a bright dot through
   a window. A vellum or thin-acrylic pane across each aperture turns a dot into a
   glowing window. Cheapest, highest-impact item on the list.
4. **Don't power pixels from the 3.3 V pin** — 500 mA regulator, and 3.3 V pixels
   are dim and off-colour.
