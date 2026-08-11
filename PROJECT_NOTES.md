# Halloween Castle — Tech Upgrade

Living design/research doc. Updated as we go.

**Goal:** Take a store-bought decorative castle and add controllable RGB lighting
and spooky audio, driven by an ESP32. Short term: prototype with hardware on hand.
Long term: addressable RGB + real speakers, network-controlled.

**Chosen stack:** ESPHome (config-driven, native Home Assistant).
**Chosen board:** Adafruit **ESP32-S2** Feather — 240 MHz, 4 MB flash, 2 MB PSRAM.
Currently attached at `/dev/cu.usbmodem1101`.

---

## 1. Hardware inventory

| Item | What it actually is | Verdict |
|---|---|---|
| ESP32-S2 Feather | 240 MHz, 4 MB flash, 2 MB PSRAM, **single core**, no BLE | ✅ The brain |
| microSD 32 GB | Spare card | ✅ Becomes the sound library |
| RAK19003 | WisBlock **Mini Base Board** — carrier, no MCU | 🟡 Only Slots C/D (24-pin) |
| RAK4630/4631 | WisBlock Core, **nRF52840 + SX1262 LoRa**, seated on the 19003 | 🟡 No WiFi — repurpose (§4) |
| RAK18060 | Stereo amp, **2× TI TAS2560**, I2S in + I2C control | ❌ Shelve — see §2 |
| 2× speakers | Wired to the RAK18060 | ✅ **Harvest these** |
| Castle stock LED board | White-only, unknown drive circuit | 🟡 Free Stage-1 win |

---

## 2. Can the ESP32-S2 Feather drive the RAK18060? — Researched: technically yes, practically no

Three independent obstacles, each verified:

### 2.1 No solderless wiring path exists

The RAK18060's only interface is a **40-pin, 0.5 mm-pitch board-to-board connector**
(IO-slot type). Signals we'd need: `3V3`, `GND`, `I2C1_SDA` (19), `I2C1_SCL` (20),
`I2S_BCLK` (38), `I2S_WS` (37), `I2S_DO` (35) — seven wires.

- **RAK19003 can't host it.** It exposes only Slot C (10 mm) and Slot D (23 mm),
  both **24-pin** sensor connectors. Wrong connector, and its 2.54 mm headers carry
  only `VDD/GND/SCL/SDA` and `RX/TX/GND/BOOT` — **no I2S**.
- **RAK13002 doesn't rescue it.** It's the one WisBlock IO-slot→2.54 mm breakout,
  but its header signals are UART ×2, I2C ×2, SPI, 6× GPIO, 2× ADC, power/reset.
  **I2S is not routed.** Verified against its datasheet.

So the only physical route is hand-soldering magnet wire to a 0.5 mm-pitch BTB
connector. Doable under a microscope with 34 AWG and flux; high odds of wrecking
a $25 module.

### 2.2 ESPHome has no TAS2560 driver

Even perfectly wired, the amp stays **silent** until something walks the I2C init
sequence for **both** chips — `0x4C` (left) and `0x4F` (right). RAK's driver is
~670 lines (`tas2560.cpp`). It's clean, portable Arduino `Wire` code with no nRF
dependencies, so porting it into a custom ESPHome component is *possible* — but
it's a real driver-writing project, not a config change.

By contrast a MAX98357A needs **zero** configuration, and a DFPlayer needs none either.

### 2.3 ESP32-S2 is the weakest ESP32 for ESPHome audio

Single core. ESPHome's modern `speaker` media player requires the ESP-IDF framework,
and the community consensus for single-core parts (S2/C3) is an external ESP8266Audio-
based component rather than the stock path. Decoding MP3 *on* the S2 while also
running WiFi and LED effects is the least comfortable configuration available.

### 2.4 Verdict

Three strikes: fiddly soldering, a custom driver to write, and the worst host chip
for on-device decoding. **Shelve the RAK18060.** Harvest its speakers.

Keep the module — if you ever add a **RAK19007** base (has a real IO slot), the
RAK4630 + RAK18060 combo works out of the box with RAK's `PlayBack` examples as a
standalone BLE/LoRa audio node. That's a fine rainy-day project, just not this one.

---

## 3. Recommended architecture — DFPlayer Mini

This sidesteps every problem in §2 at once.

```
                  ┌──────────────────────────────┐
   5V ────────────│  ESP32-S2 Feather (ESPHome)  │
                  │                              │
       UART TX/RX │◀──▶ DFPlayer Mini ──▶ speakers (harvested)
                  │        └── 32GB microSD = sound library
       RMT        │──▶ WS2812B / SK6812 pixels
       GPIO       │◀── PIR motion sensor
       WiFi       │──▶ Home Assistant / web
                  └──────────────────────────────┘
```

**Why DFPlayer Mini (~$6) is the right call here:**

- **Native ESPHome component** — `dfplayer:` on a UART bus. Actions: `play`,
  `play_folder`, `play_mp3`, `set_volume` (0–30), `set_eq`, `random`, `stop`.
- **Zero decoding on the ESP32.** The S2's single core stops mattering entirely —
  no I2S, no MP3 decode, no PSRAM juggling. ESPHome just sends UART commands.
- **Uses your 32 GB microSD.** A whole library of thunder/screams/organ/wind,
  organized in numbered folders. Far more audio than 4 MB of flash could hold.
- **Built-in 3 W mono amp** drives a harvested speaker directly off `SPK_1`/`SPK_2`.
- **Two wires.** TX→RX is the bare minimum; add RX→TX to detect "still playing".

**Caveats to design around:**
- The built-in amp is **mono** (single BTL output). For stereo, use `DAC_L`/`DAC_R`
  into a small stereo amp (PAM8403, ~$2) — that's when the second speaker earns its place.
- Built-in amp wants **5 V / 1 A** for full 3 W. Logic is fine at 3.3 V.
- Clones vary. Prefer genuine DFRobot; the common noise fix is a **1 kΩ resistor in
  series on the module's RX line**.
- Trigger latency is ~50–100 ms — irrelevant for thunder, so long as we schedule
  the audio cue *before* the light cue.

**LEDs:** `esp32_rmt_led_strip` supports ESP32-S2 with WS2812/SK6812. Note RMT
memory is shared between RX and TX on the S2 — worth remembering if we ever add an
RMT receiver. `NeoPixelBus` is the fallback.

### 3.1 ESPHome vs WLED

They're separate firmwares — one chip runs one of them. WLED has gorgeous effects
but **cannot play audio** (its sound-reactive forks *listen*, they don't play).
Splitting across two Feathers means lightning-vs-thunder sync goes through Home
Assistant, which is too laggy to look right.

**Decision: ESPHome for both, on one board.** Local automations keep the flash and
the thunderclap tied together with no network round-trip.

---

## 4. Repurposing the RAK4630

Don't leave it in a drawer. The nRF52840 + SX1262 is excellent at exactly what the
ESP32 is bad at: sipping microamps for months and reaching a long way.

**Idea: a battery-powered LoRa trigger node.** PIR sensor at the end of the
driveway, deep-sleeping at µA, fires the castle from 100 m+ when someone approaches.
Needs a LoRa receiver on the castle side. A BLE-advertisement trigger is the simpler
variant if ~10–30 m is enough (but note: **the ESP32-S2 has no BLE**, so that path
would need a different Feather).

---

## 5. Shopping list

| Part | Qty | ~Cost | Why |
|---|---|---|---|
| DFPlayer Mini (genuine DFRobot) | 1 | $6 | The whole audio problem, solved |
| WS2812B / SK6812 pixels | 1 | $10–15 | The actual RGB |
| 74AHCT125 level shifter | 1 | $2 | Reliable 5 V WS2812 data from 3.3 V |
| PAM8403 stereo amp | 1 | $2 | *Only if* stereo matters |
| PIR sensor (HC-SR501) | 1 | $3 | Motion trigger |

*Prices unverified — rough figures for planning.*
**Stages 0–1 below need none of this.**

---

## 6. Staged plan

- **Stage 0 — no new parts.** Install ESPHome, adopt the S2 Feather, get WiFi +
  OTA + onboard LED blinking. Proves the toolchain end to end.
- **Stage 1 — no new parts.** Drive the castle's salvaged white LED board from a
  GPIO through a MOSFET; PWM a convincing candle flicker. Already better than stock.
- **Stage 2 — needs DFPlayer.** Sound library on the microSD, ambient loop + triggered stingers.
- **Stage 3 — needs pixels.** Addressable RGB and the effect set.
- **Stage 4.** PIR trigger, scene engine, Home Assistant, quiet-hours scheduling.

---

## 7. Effect brainstorm

**Lighting**
- Candle/torch flicker in windows — warm amber, smoothed noise (random per-frame looks wrong)
- Lightning: full-strip white burst, 2–3 stutters, decay
- Ghost glow: slow cyan/green breathing in one tower
- Eyes: two red pixels in a high window, occasional blink
- Blackout → one lit window → blackout. Cheap and very effective.

**Audio**
- Ambient bed: wind, distant wolves, low drone — loops quietly forever
- Stingers: thunder, scream, door creak, chains, organ chord
- Duck the ambient bed while a stinger plays

**Show sync** — the one thing worth getting right: fire the thunder sample
**first**, then the light flash ~80 ms later. Light should reach the eye before
sound reaches the ear, and it hides the DFPlayer's trigger latency.

**Triggers:** PIR at the walkway · HA button · quiet mode after 10 pm · off at midnight

---

## 8. Open questions

1. Speaker impedance (4 Ω / 8 Ω) and wattage? Determines whether DFPlayer's onboard
   3 W amp is enough or we go `DAC_L/R` → PAM8403.
2. Castle size, number of windows/towers → sets pixel count.
3. Do you have a spare **ESP32-S3** Feather? Not required with the DFPlayer plan,
   but it'd restore BLE and dual-core headroom for free.
4. Target Halloween 2026 (Oct 31, ~3 months out)?
5. Power/placement — deferred at your request.

---

## 9. Buying a DFPlayer that actually works

"DFPlayer Mini" is a *form factor*, not a product. Boards from different sellers
carry different decoder chips with different firmware, and listings routinely don't
match what ships. This is the single biggest practical risk in the plan.

### 9.1 The chip lottery

| Chip marking | Verdict |
|---|---|
| **YX5200-24SS** | ✅ The original DFRobot part. Most compatible, best documented. Usually paired with a YX8002 3 W amp. |
| **GD3200B** | 🟡 Works, but needs longer command timeouts (350–500 ms vs 200–300 ms). |
| **MH2024K-24SS / -16SS** | ❌ Widely reported as fiddly. Avoid. |
| **AB23 / AB24 / AF24 / JL** | ❌ Assorted clones with incomplete firmware. Avoid. |

**Spotting them:** MH2024K boards are the easy tell — different PCB layout from the
original, and sometimes a 16-pin footprint instead of 24. Modules labeled
**"MP3-TF-16P"** are generally the clone lineage.

### 9.2 Recommendation

Buy the **genuine DFRobot DFR0299** from an authorized distributor (DFRobot direct,
DigiKey, or Mouser) rather than a marketplace listing. It's roughly $8–10 against
$3 for the lottery ticket. The clone failure mode is *silent weirdness* — plays the
wrong track, ignores volume, busy pin never asserts — which costs far more in
debugging time than the few dollars saved. **Buy two**; spares are cheap and it lets
you isolate "bad module" from "bad wiring" instantly.

### 9.3 Known mitigations (design these in from the start)

- **Disable ACK.** Feedback-on is the default and is the most common source of trouble.
- **1 kΩ resistor in series on the module's RX line** — the standard noise fix.
- **Don't trust the BUSY pin for ~350 ms after issuing play** — it lags the command.
- **Timeouts:** 200–300 ms for YX5200; 350–500 ms for GD3200B/MH2024K.
- **5 V / 1 A** for the built-in amp to hit its rated 3 W. Logic is happy at 3.3 V.

**ESPHome angle:** the `dfplayer` component is largely fire-and-forget over UART,
which dodges most ACK pain by design. The fragile part is `on_finished_playback`,
which depends on the module's RX feedback — exactly what clones get wrong. Plan to
**also wire BUSY to a GPIO as a `binary_sensor`**, and treat that as the authoritative
"is it still playing" signal. Cheap insurance, one wire.

**Fallbacks if the module disappoints:** DY-SV17F / DY-SV5W are more reliable
modules, but have no native ESPHome component (UART protocol would need a custom
component). JQ6500 is another option.

### 9.4 Sound sources for the SD card

- freesound.org (CC0/CC-BY — check per-file licence), BBC Sound Effects (free for
  personal use), soundbible
- MP3, 44.1 kHz. The module also handles WAV/WMA.
- Folder layout for `dfplayer.play_folder`: `01/` ambient, `02/` thunder, `03/` stingers,
  files as `001.mp3`, `002.mp3`, …

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
**Source:** [`previewer/castle-cue-desk.html`](previewer/castle-cue-desk.html)

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

---

## 12. Build — implemented

Project scaffolded and validated. See [README.md](README.md) for usage.

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

## 13. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-09 | RAK18060 ruled out | No solderless I2S path; no ESPHome TAS2560 driver; S2 poor audio host (§2) |
| 2026-08-09 | Audio via DFPlayer Mini | Native ESPHome support, uses the 32 GB SD, zero DSP load on a single-core S2 |
| 2026-08-09 | ESPHome for lights *and* sound on one board | WLED can't play audio; cross-device sync too laggy for lightning/thunder |
| 2026-08-09 | RAK4630 repurposed as remote trigger | Plays to nRF52840 strengths (µA sleep, LoRa range) instead of its no-WiFi weakness |
| 2026-08-09 | Buy genuine DFR0299, wire BUSY pin anyway | Clone chip lottery is the top practical risk (§9) |
| 2026-08-09 | Prototype in a browser previewer first | Tune timing without reflashing; one scene file feeds preview *and* ESPHome |
| 2026-08-09 | Second palette: Haunted Mansion violet/green | Justin's reference. Coexists with candlelit warm — they're different scenes, not a global mode |
| 2026-08-09 | Music written original, not sourced | Haunted-parlour idiom (A minor, 3/4, raised 7th). Avoids putting copyrighted material on a public-facing display |
| 2026-08-09 | Parts sourcing parked to the end | Justin's call — settle the show first, then buy to fit it (§11) |
| 2026-08-10 | Light cues derive from audio markers, not hand-typed times | Synths report their own event times; a jittered heartbeat can't drift from its light (§12.6) |
| 2026-08-10 | Base effects get a per-zone `level`; strikes stay unscaled | Contrast is the whole mechanism — a standing effect at full brightness buries the audio-driven pulses (§12.6) |
| 2026-08-10 | Each strike carries its own colour and decay | One global white snap can't express both a lightning bolt and a heartbeat; colour is how a viewer hears with their eyes (§12.6) |
| 2026-08-10 | Previewer arms audio on the first gesture anywhere | The Arm button was a discoverability trap; browsers only need one gesture, not that specific one (§12.6) |
