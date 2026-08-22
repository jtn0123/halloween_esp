# Halloween Castle — Research and architecture (§1–§9)

Part of the design record; the index is [`PROJECT_NOTES.md`](../../PROJECT_NOTES.md). Section numbers are global across the parts, so `§12.9` means the same thing in every file.

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
