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

---

## 12.9 microSD audio — what's true, and what it costs (2026-08-10)

Justin wants the audio library on the 32 GB microSD card rather than in flash.
Researched properly before building, because the answer is mostly "no".

### The blockers, verified

1. **ESPHome has no SD component.** Not in 2026.7.4, not anywhere in
   `esphome/components/`.
2. **The audio pipeline has no filesystem source.** `audio/audio_reader.h` has
   exactly two `start()` overloads: a URL via `esp_http_client`, and an
   `AudioFile *` in flash. Nothing else.
3. **No external component bridges SD to audio.** Several SD components exist
   (n-serrette/esphome_sd_card is the main one at ~79★) but every one is
   storage-only — read, write, list, serve over HTTP. None can feed `speaker`
   or `media_player`. Upstream, [media-players#47] was closed *not planned*;
   [feature-requests#2989] has had no maintainer response since Dec 2024.
4. **The ESP32-S2 has no SDMMC host controller at all** — Espressif's own docs
   say so; the S2 can only talk to cards over SPI. This alone disqualifies
   n-serrette's component, which hard-rejects any variant except ESP32/S3:
   ```python
   if variant not in [VARIANT_ESP32, VARIANT_ESP32S3]:
       raise cv.Invalid(f"Unsupported variant {variant}")
   ```
5. **The Adafruit ESP32-S2 Feather (5000) has no microSD slot**, and neither
   does any S2/S3 Feather variant. A card comes from a stacked wing — the
   Adalogger (2922), or the 2.4"/2.8"/3.5" TFT FeatherWings, which do carry a
   microSD socket. Whichever it is, it is SPI, and the CS pin differs per wing
   (usually a cuttable jumper).
6. **Both well-known ESP32 audio libraries decline to support the S2.**
   schreibfaul1/ESP32-audioI2S says verbatim that it does not work on the S2.
   The ESPHome wrappers around it were last touched in 2023.

### The one path that exists

`audio::AudioFile` is a plain struct — `{const uint8_t *data; size_t length;
AudioFileType type;}` — and `SpeakerMediaPlayer::play_file()` is **public**. So
the decoder never needs touching: mount the card over SPI, read a whole file
into PSRAM, point an `AudioFile` at the buffer, call `play_file()`.

Built as `firmware/sd_audio.h` + `firmware/castle_sd.yaml`, kept as a separate
variant so the working flash build is untouched.

**What it buys:** the card is the library. Flash holds one show's worth
(~2.9 MB, all scenes competing); the card holds as many tracks as you like.

**What it does not buy:** streaming. The whole file lands in PSRAM before
playback, so a single track is capped by free PSRAM — roughly 1.5 MB of the
2 MB once buffers and the framebuffer have taken theirs. About two minutes at
96 kbps. Real streaming means writing a custom `AudioReader`, or a
`media_source::MediaSource` (ESPHome 2026.x added that base class, so it is now
*writable* as an external component). Nobody has written either.

### Risks worth stating plainly

- **Nobody has published ESPHome SD audio on an ESP32-S2.** This is new ground,
  not a recipe being followed.
- **Single core.** WiFi, the ESPHome loop, RMT for the LEDs, I2S feed and MP3
  decode all share one core with nowhere to park the decoder. There are
  documented S2 audio failures (esphome/issues#4106: plays half a second then
  drops frames, same config fine on a plain ESP32). No one has published an
  actual S2 MP3 benchmark either — "the S2 is too slow" is folk wisdom, not
  measurement. The bench dry-run (§12.5) is still the thing that settles it.
- **The S2 cannot DMA from PSRAM** (Espressif docs, stated outright), so I2S
  buffers must live in internal SRAM and every frame costs a PSRAM→SRAM copy
  the S3 would avoid.

### The cheaper answers, honestly

- **WAV costs zero decode CPU** — it is a memcpy into the I2S DMA ring. At
  22 kHz mono that's 44 KB/s, nothing for SPI SD, and it removes the entire
  decode question. Worth trying first if MP3 stutters.
- **HTTP already works today.** `start_url()` is supported right now, with no
  custom code, no card, no length limit. A Mac or Pi serving a folder gets
  full-length tracks at any bitrate. The cost is that the server must be up
  during the show.
- **If this turns into a fight, an ESP32-S3 Feather ends it** — dual core, DMA
  from PSRAM, and the audio libraries actually support it.

---

## 12.10 The eInk FeatherWing takes three of our pins (2026-08-10)

Justin's card slot is on an **Adafruit 2.13" eInk FeatherWing**, stacked on the
ESP32-S2 Feather. Researched the pinout before wiring anything, and it is worse
than a single clash.

Adafruit documents one convention for the whole eInk FeatherWing family
(4128 tri-colour, 4195 mono, 4814 HD) — it does not vary by revision:

| Wing signal | Header | GPIO | Cuttable? |
|---|---|---|---|
| SD chip select | D5 | 5 | yes |
| SRAM chip select | D6 | 6 | yes |
| eInk chip select | D9 | 9 | **no** |
| eInk data/command | D10 | 10 | **no** |
| SCK / MOSI / MISO | — | 36 / 35 / 37 | shared bus |
| RST | — | tied to Feather RESET | — |
| BUSY | — | not connected | — |

Against the old pin map, **three of five collided**:

| Signal | Was | Wing wanted it for |
|---|---|---|
| NeoPixel data | 5 | SD chip select |
| PIR input | 6 | SRAM chip select |
| I2S DOUT | 10 | eInk data/command |

The GPIO5 one was the dangerous one — NeoPixel data would have been driving the
SD card's active-low chip select at 800 kHz. Not a fault you'd diagnose quickly;
it would present as "the card sometimes doesn't mount".

**Remapped** (D11/D12/D13 are untouched by the wing, and A0–A3 are free):

    pin_led_data:  18   # A0   was 5
    pin_pir:       17   # A1   was 6
    pin_i2s_dout:  15   # A3   was 10
    pin_i2s_bclk:  11   # D11  unchanged
    pin_i2s_lrclk: 12   # D12  unchanged

A0/A1/A3 are ADC2, unusable as ADCs while WiFi is on — irrelevant, these are
plain digital signals. Avoided D13 despite it being free: it also drives the
onboard red LED, which would then flicker in time with the audio data.

**A second finding worth more than the remap.** The eInk panel and its SRAM sit
on the same SPI bus as the card, and we use neither — but their chip selects are
still wired. A floating chip select is a device that may answer while the card
is being addressed. `castle_sd.yaml` now drives GPIO 9 and GPIO 6 HIGH at boot
and leaves them there. This is the difference between "the card works" and "the
card works most of the time", which is the worst kind of bug to chase on a porch
in October.

**Caught while writing it:** the first version used `inverted: true` on those
park pins, which would have made `output.turn_on` drive them *low* — actively
selecting both devices, the exact opposite of the intent.

---

## 12.11 Audio capacity — the numbers behind the 4-minute question

Load-into-PSRAM (§12.9) caps a track by free PSRAM, realistically ~1.5 MB of the
2 MB once the player buffer and framebuffer have taken theirs:

| Format | Fits in ~1.5 MB |
|---|---|
| 96 kbps mono | ~2 min |
| 64 kbps mono | ~3 min |
| 48 kbps mono | ~4 min |
| 96 kbps stereo | ~2 min |
| 128 kbps stereo | ~1.5 min |

So four minutes is reachable today only by dropping to 48 kbps mono.

**Stereo costs no GPIOs.** The MAX98357A is mono, so stereo means a second amp
and speaker — but both amps share the same three I2S lines and each picks its
channel from a resistor on its SD pin. Wiring is soldering the second amp to the
same three wires. The cost is data rate, not pins.

**The codec trap:** the formats that are kindest to the CPU are the ones too big
for RAM, and the ones small enough for RAM are the hardest to decode.

| Codec | Decode cost | 4 min mono | Verdict |
|---|---|---|---|
| WAV | ~zero (memcpy to I2S) | 21 MB | streaming only |
| FLAC | moderate | ~10 MB | streaming only |
| MP3 | ~30% of a core @128k on ESP32 | 2.8 MB @96k | needs 48k to fit |
| Opus | highest | 0.96 MB @32k | fits, but CPU unproven on S2 |

Load-into-RAM forces the wrong end of that trade in every direction.

**The fix that removes all of it at once: a streaming SD `MediaSource`.**
ESPHome 2026.x ships `media_source::MediaSource`, an abstract base where a
source pushes chunks via `write_audio()`, consumed by the `speaker_source`
media player. `audio_file/media_source` is a working reference implementation
that does exactly this from flash — an SD version reads from a `FILE *` instead
of a byte array. That is a tractable component, not a rewrite, and it gives
unlimited length, free choice of codec (including zero-CPU WAV), and stereo if
the data rate allows.

---

## 12.13 The decode benchmark (2026-08-10)

`firmware/bench_audio.yaml` (`make bench-audio`) — the bare Feather, no
speakers, no jewels, no card. It exists because every claim about what an
ESP32-S2 can decode is currently folk wisdom: Espressif publish figures for the
ESP32 and S3 and none for the S2, and both well-known ESP32 audio libraries
decline to support the chip. Choosing a codec on vibes is how you find out on
Halloween night.

The full pipeline runs — MP3 decode, resample, I2S out — with WiFi up and the
LED RMT driver going, which is the contention that actually matters on one
core. The I2S pins simply have nothing listening.

It logs one greppable line every 2 s:

    loop_max=..ms  int_ram=..B  psram=..B  playing=yes|no

then runs 30 s idle, then vigil, crypt (78 beat cues, the heaviest cue load),
descent (densest audio) and storm. Compare `loop_max` idle against playing:

- under ~30 ms — comfortable, MP3 is fine
- 50–100 ms — decoding, but light cues will visibly drift
- component-loop warnings, `underrun`, `Failed to decode` — not keeping up

`free internal RAM` is the tighter pool, because the S2 cannot DMA from PSRAM
so the I2S buffers must live in internal SRAM.

### RESULTS — first run on real hardware, 2026-08-10

105 samples over ~3.5 minutes, with a scene playing throughout:

| | value |
|---|---|
| loop time, average | **32.7 ms** |
| loop time, max | **60 ms** |
| free internal SRAM | **91 KB** |
| free PSRAM | **1713 KB** |
| decode errors / underruns | **0** |
| blocking-loop warnings | **0** |

**MP3 decode works on this chip.** Not one underrun, not one decode failure,
not one "components should block" warning, while WiFi was up and the LED RMT
driver was running. The folk wisdom that the ESP32-S2 cannot do this is, at
least at 96 kbps mono, wrong.

Loop time sits a little above the 30 ms mark the benchmark's own header calls
"comfortable" — but with zero underruns and zero blocking warnings, and with
the light engine updating on a 16 ms tick, that is a paced loop rather than a
starved one. Worth re-measuring once real pixels and a real amp are drawing
current.

**Caveat, stated plainly:** every sample came back `playing=yes`, because
`vigil` loops forever and had already started before the log connection was
made. So there is no idle baseline to compare against — the "idle vs playing"
delta the benchmark was designed to show was not actually captured. The
absolute numbers stand; the comparison does not exist yet.

**1713 KB free PSRAM is more than the 1.5 MB that had been assumed**, which
moves the whole-file SD numbers in the right direction:

| Format | Fits in the measured 1713 KB |
|---|---|
| 96 kbps mono | 2:26 |
| 64 kbps mono | 3:39 |
| 48 kbps mono | **4:52** — four minutes with real margin |

The previewer's capacity readout now uses the measured figure rather than the
estimate.

---

## 12.14 Getting logs off this board (2026-08-10)

Cost most of an evening. Recorded so nobody repeats it.

**There is no USB serial console on this board, and there never was.** The
ESP32-S2 has no USB Serial/JTAG peripheral — that is an S3/C3 feature, which is
why serial "just works" on Justin's other ESP32 projects and not here. The S2's
only USB is the OTG peripheral, and ESPHome does not run a TinyUSB CDC stack.
Every serial port we saw all evening (`usbmodem101`, `usbmodem01`) was the ROM
bootloader's, never the application's.

**`CONFIG_ESP_CONSOLE_USB_CDC` is worse than useless here — it panics.** The
crash log from that boot, read back over the API once we finally had a channel:

    Reason: Interrupt wdt - Interrupt wdt timeout on CPU0
      esp_usb_console_flush_internal   (usb_console.c:364)
      esp_usb_console_write_buf        (usb_console.c:416)
      panic_print_char_usb_cdc         (panic.c:104)

With it set, the board enumerates nothing at all over USB — no app port, and no
way back in except holding BOOT while tapping RESET. Reverted; the config now
carries a comment saying why, so it doesn't get "fixed" again.

**mDNS does not resolve on this network.** `castle-benchaudio.local` never
resolved and `dns-sd -B _esphomelib._tcp` found nothing, which looked exactly
like "the device isn't connected". It was connected the whole time. A scan of
the subnet for port 6053 found it in seconds:

    for i in $(seq 1 254); do (nc -G 1 -z 10.27.27.$i 6053 && echo $i) & done

Then `esphome logs <yaml> --device 10.27.27.7` works normally.

**The lesson worth keeping:** two independent "the device is dead" signals —
silent serial and failed mDNS — were both instrumentation failures, and the
firmware was fine throughout. The one reliable signal all evening was Justin
looking at the onboard LED.

---

## 12.12 Build trees moved off the internal disk (2026-08-10)

A compile died with `No space left on device` — the Mac's data volume had
143 MB free. Four firmware variants had accumulated ~300 MB of ESP-IDF objects
each.

Justin asked whether the repo should move to his 512 GB USB-C drive. It should
not, and the sizes say why:

| | size |
|---|---|
| everything git tracks | **2.8 MB** |
| `.venv` | 344 MB |
| `firmware/.esphome` build trees | 1.0 GB |

The source is the small part. Moving the repo would break `.venv`'s absolute
shebang paths and gain almost nothing; the thing worth relocating is the cache.

`firmware/build_path.yaml` sets `esphome: build_path:` to the external drive
and is included as a package by every variant. Source stays put, git stays
sane, `.venv` keeps working. If the drive is unmounted, builds fail with a path
error — comment out the one line to build locally again.

Also worth noting for later: `castle.yaml` already had a `packages:` block, so
adding a second one is a duplicate-key YAML error. The include had to merge
into the existing block.

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
| 2026-08-10 | ~~Previewer arms audio on the first gesture anywhere~~ **Reverted** | Made any stray click blast a scene at full volume, and made the overlay's own text a lie (§12.7) |
| 2026-08-10 | Previewer is muted by default; audio starts only from Play | A preview tool must never make noise you didn't ask for. Unmuting is one click; being ambushed is not recoverable (§12.7) |
| 2026-08-10 | Mute uses `.muted`, never volume 0 | Volume is also written by the fade-in and master slider, so a volume-based mute is one stray write from being undone (§12.7) |
| 2026-08-10 | Seeking replays all `set` cues from zero | Scrubbing otherwise keeps whatever effects were on stage when you grabbed the bar (§12.7) |
| 2026-08-10 | Imported tracks get onset-detected light, not hand-typed cues | Outside audio can't report its own beats; detecting them keeps the "light follows sound" guarantee that generated scenes get for free (§12.8) |
| 2026-08-10 | Onsets detected per frequency band, not once overall | A single onset track makes all three zones blink together; banding gives the bass the door and the melody the towers (§12.8) |
| 2026-08-10 | `tracks/` git-ignored, scene definitions tracked | The audio is Justin's and large; the show should still be reproducible from the repo (§12.8) |
| 2026-08-10 | SD audio built as a separate variant, not folded into castle.yaml | It is unproven on S2; the flash build must keep working with no card in the slot (§12.9) |
| 2026-08-10 | SD reads whole files into PSRAM rather than streaming | `AudioFile` is {ptr,len,type} and `play_file()` is public, so the decoder needs no changes. Streaming would mean writing a MediaSource nobody has written (§12.9) |
| 2026-08-10 | NeoPixel → A0, PIR → A1, I2S DOUT → A3 | The eInk FeatherWing hard-wires D5/D6/D9/D10; NeoPixel data on the SD chip select would have been an intermittent-mount nightmare (§12.10) |
| 2026-08-10 | eInk and SRAM chip selects parked HIGH at boot | They share the card's SPI bus and we use neither; a floating CS is a device that may answer mid-transaction (§12.10) |
| 2026-08-10 | Tracks remember their source in tracks.json | An imported MP3 is otherwise a dead end — no way to rebuild it at different settings without hunting for the link again |
| 2026-08-10 | Build cache moved to external storage, repo stays put | Source is 2.8 MB, cache is 1.3 GB. Moving the repo breaks `.venv` paths to solve nothing (§12.12) |
| 2026-08-10 | Built a decode benchmark before choosing a codec | Nobody has published an ESP32-S2 MP3 measurement; picking a codec on folk wisdom is how you find out in October (§12.13) |
| 2026-08-10 | MP3 stays the codec | Measured on the board: zero underruns, zero decode errors, 32.7 ms average loop. The S2-can't-decode folklore is wrong at 96 kbps mono (§12.13) |
| 2026-08-10 | Never set CONFIG_ESP_CONSOLE_USB_CDC on this board | It doesn't just fail — it panics. Interrupt watchdog timeout inside `esp_usb_console_flush_internal`, captured from the crash log (§12.14) |
| 2026-08-10 | Find the device by IP, not mDNS | mDNS does not resolve on this network; the API on port 6053 does. A subnet scan found it immediately (§12.14) |

---

## 14. Roadmap

Agreed order, 2026-08-10. Each step makes the next one easier, which is why
they are in this order rather than by appetite.

### 1. TypeScript migration — `web/MIGRATION.md`
The previewer is 1892 lines of HTML wrapping ~1400 lines of untyped inline JS.
Eight modules, all under 500 lines, split by responsibility. The inline script
stays authoritative until the final commit flips over, so the page is never
half-migrated. Doing this first because the bundler it introduces is what makes
step 3 a build flag instead of a fork.

### 2. SD card streaming — a `media_source::MediaSource`
The measured decode headroom (§12.13) changed this from a compromise into an
upgrade: streaming means full-length tracks at proper quality rather than
trimming to fit PSRAM. ESPHome 2026.x ships the abstract base class and
`audio_file/media_source` is a working reference that does exactly this from
flash; the SD version reads a `FILE *` instead of a byte array. Build as a
third variant, keep the flash scenes as the safety net.

### 3. A cut-down cue desk served off the device
Flash headroom is ~1.1 MB. The current page is 2.6 MB, almost entirely embedded
audio — and the device *is* the audio, so that goes. Drop the synth and the
Tracks panel too; keep the stage, scene buttons and cue sheet. Plausibly under
100 KB. With step 1 done this is a build target, not a second codebase.

### Standing work
- Split `tools/synth.py` (395 lines, will pass 500 with the next scene) into
  `voices.py` and `pieces.py`
- Verify the remapped pins against hardware with a meter (§12.10 inference flag)
- Re-run the benchmark with a real idle window, and again with pixels and amp
  drawing current (§12.13 caveat)
