# Wiring the castle — power and audio

Split from [WIRING.md](WIRING.md) (the 500-line cap; the seam is real:
everything below is about electrons and sound, nothing about data lines).
Sections keep their original numbers so old references still read.

## 4. Power — the part that actually bites

This is where a 21-pixel castle and a 60-pixel castle stop being the same
project.

### What each fixture can draw

Full white, all pixels, 20 mA per LED die:

| Fixture | Pixels | RGB max | RGBW max |
|---|---:|---:|---:|
| NeoPixel Jewel 7 | 7 | 0.42 A | 0.56 A |
| NeoPixel Stick 8 | 8 | 0.48 A | 0.64 A |
| NeoPixel Ring 12 | 12 | 0.72 A | 0.96 A |
| NeoPixel Ring 16 | 16 | 0.96 A | 1.28 A |
| NeoPixel FeatherWing 4×8 | 32 | 1.92 A | — (RGB only) |
| Mini PCB single ×5 | 5 | 0.30 A | — (RGB only) |

Your current rig — two RGBW Jewels and the Ring 12 — peaks at **2.1 A**.
Your heaviest possible rig — FeatherWing + Ring 16 + Ring 12 — peaks at
**4.2 A** if the rings are RGBW. Plus two amplifiers at up to 0.8 A each on bass transients.

**So: a 5 V supply of at least 8 A (40 W).** 10 A if you want to stop thinking
about it. This is not optional, and here is the specific reason:

> The show's average draw is nothing like its peak — candles and embers sit
> around 15–25 % of maximum. But a **lightning strike drives every zone to
> full white simultaneously**, which is precisely the worst case, for about
> 150 ms. On an undersized supply that sag browns out the ESP32 and reboots
> the castle mid-scene. An underpowered rig doesn't dim; it crashes, and it
> crashes only during the good bits.

The app now estimates this for you — see [§7](#7-choosing-the-fixtures-without-soldering-them).

### Distribution

- **Star, not chain.** Run 5 V and GND from the supply to *each* fixture
  separately. Do not feed tower R through the door's pads. Data is already
  three independent runs, so there's nothing tying the fixtures together
  anyway.
- **18–20 AWG for the 5 V runs.** At 2 A over a 3 m run, 20 AWG drops about
  0.12 V; 22 AWG drops nearly 0.2 V and the whites start going pink.
  22–24 AWG is fine for data.
- **1000 µF electrolytic across 5 V and GND at each fixture**, observing
  polarity. This is the reservoir the strike draws from before the supply has
  time to respond.
- **Inline fuse on the 5 V bus**, rated a little above your real peak. 5 A
  for a modest rig, 7.5 A for the heavy one.

### Grounds

**Every ground in this build must be tied together**: the ESP32, the level
shifter, both amplifiers, and the LED supply. A data line is a voltage
*relative to ground*, so two subsystems with unjoined grounds don't have a
shared idea of what "high" means. This is the single most common cause of
"the pixels flicker randomly" and it looks like a software bug.

### Powering the Feather itself

- **On the bench:** Feather on its USB-C, LEDs on the separate 5 V supply,
  grounds tied. You keep the reflash path.
- **Installed:** feed the 5 V bus into the Feather's **`USB` pin**, with
  nothing plugged into the USB-C socket. Don't do both at once.

Do **not** run the pixels off the Feather's `USB` pin. That trace is sized for
the board, not for 4 A of LEDs.

---

## 5. Two speakers, one I2S bus

I2S is a bus. Both MAX98357A boards get the **same three signal wires** —
there's no second set of pins to find, and no second peripheral to configure.

And both play **the same audio**. This is not a stereo pair; it is one mono
show coming out of two boxes, one per tower. That is deliberate — the scenes
are rendered mono, and the castle is a single sound source that happens to be
several feet wide.

### The wire table

Listed in the order the pins actually sit on the Adafruit breakout, so you can
read straight down the header with the board in your hand:

| Pin | Connect to | Why |
|---|---|---|
| `LRC` | GPIO12 (D12) — **both amps** | word clock, shared |
| `BCLK` | GPIO11 (D11) — **both amps** | bit clock, shared |
| `DIN` | GPIO15 (A3) — **both amps** | data, shared |
| `GAIN` | leave unconnected | the default 9 dB |
| `SD` | **leave unconnected** | selects (L+R)/2 — which is full level here |
| `GND` | common ground | — |
| `VIN` | 5 V bus | 0.8 A peak each — see [Power at the amps](#power-at-the-amps) |
| `+` / `−` | speaker terminals | bridge-tied — neither one is ground |

Those three GPIOs come from `firmware/castle.yaml` (`pin_i2s_dout`,
`pin_i2s_bclk`, `pin_i2s_lrclk`). If they ever move, they move there.

### The SD pin — leave it empty

`SD` is not just a shutdown pin: its *voltage* selects which channel the amp
plays. The chip has an internal 100 kΩ pulldown and the Adafruit breakout adds
a 1 MΩ pullup to VIN, so with nothing else attached the pin sits at
5 V × 100k/1.1M = **0.45 V**.

| SD voltage | Amp plays |
|---|---|
| < 0.16 V | shut down |
| 0.16 – 0.77 V | (L+R)/2 — **where it sits with nothing attached** |
| 0.77 – 1.4 V | right channel |
| > 1.4 V | left channel |

So out of the bag, both amps average the two I2S slots. **That is the right
setting for this firmware, it is full level, and you should leave it alone.**

The reason is what ESPHome does with `channel: mono`:

> `channel: mono` compiles to `slot_mode = I2S_SLOT_MODE_MONO` together with
> `slot_mask = I2S_STD_SLOT_BOTH` (ESPHome 2026.7.4,
> `components/i2s_audio/__init__.py` — anything that isn't `stereo` gets mono
> slot mode, and anything that isn't `left`/`right` gets both slots). In
> ESP-IDF's standard-mode transmitter, mono with both slots enabled
> **duplicates the sample into both slots**. The right slot carries the same
> audio as the left, not silence.

Average two identical slots and you get the original back: (L+L)/2 = L. Full
level, no resistor, nothing to solder.

**The 6 dB trap is real — it just isn't this configuration.** Set `channel:
left` or `channel: right` and `slot_mask` narrows to that one slot while the
other transmits zeros; an amp averaging them then plays at half amplitude,
6 dB down. If the castle ever goes quiet after someone edits the `speaker:`
block, that is the first line to read.

### If you want to pin it anyway

100 kΩ from `SD` to 5 V forces **left** on both amps. That is also full level,
and it keeps working if someone later changes `channel:`. Insurance, not a
requirement — two resistors against a config edit you would notice within one
scene.

The arithmetic, for whichever value you reach for: the external resistor sits
in parallel with the breakout's onboard 1 MΩ, and the pair divides against the
chip's internal 100 kΩ pulldown.

| `SD` → 5 V | Pin sits at | Amp plays |
|---|---:|---|
| nothing | 0.45 V | (L+R)/2 — **the default, and correct here** |
| 1 MΩ | 0.83 V | right, but only 60 mV clear of the boundary |
| 760 kΩ | 0.94 V | right, mid-band |
| 100 kΩ | 2.62 V | left |

Adafruit publishes the thresholds but not the resistor values — their guide
says to experiment. The table above is that experiment done once, on paper.

### If you later want real stereo

Three changes, and they only work as a set:

- Left amp: 100 kΩ from `SD` to 5 V (lands at 2.62 V).
- Right amp: 760 kΩ from `SD` to 5 V — 330 k + 330 k + 100 k in series
  (lands at 0.94 V, near the middle of the right-channel band).
- Firmware: `channel: stereo` on `castle_speaker` **and** `num_channels: 2` in
  the media player's `announcement_pipeline`, then re-render the scene audio
  as 2-channel. That roughly doubles the audio size, so check the budget card
  before committing.

Change the amps without the firmware and one speaker goes silent. Change the
firmware without the amps and both play half the mix.

### Power at the amps

Each amp peaks near **0.8 A** on bass transients — 1.6 A for the pair, and it
arrives as a spike rather than a level. Put a **100–470 µF electrolytic across
`VIN` and `GND` at each amp**, close to the board, observing polarity. The
breakout's own decoupling is sized for the chip, not for a shared rail that is
also feeding pixels; without it the bass hits show up as pixel flicker, which
then gets debugged as a data fault for an hour.

Feed both amps from the **5 V bus**. Not from the Feather's 3V3 pin — the
MAX98357A will run at 3.3 V, but output power scales with the square of the
supply, so you lose more than half the volume and gain nothing.

### Speaker wiring

The MAX98357A output is **bridge-tied**. Both terminals swing; neither is
ground.

- Never connect a speaker `−` to ground. You will short half the bridge.
- Never join the two amps' outputs.
- 4 Ω or 8 Ω, rated 3 W or better. 4 Ω is louder (3.2 W); 8 Ω runs cooler
  (1.8 W).
- Too quiet? `GAIN` straight to GND is 12 dB; 100 kΩ from `GAIN` to GND is
  15 dB.
- Too loud? `GAIN` straight to VIN is 6 dB; 100 kΩ from `GAIN` to VIN is 3 dB.

### Testing the speakers

Desk → **🏰 Castle** → **speaker test**: five tones through both amps at
25 / 50 / 80 %, each asking one question — the **sweep** (200 Hz → 10 kHz)
for static that comes and goes with pitch, **1 kHz** as the reference that
should be a smooth whistle, **200 Hz** vs **4 kHz** to split the 5 V rail
(bass pulls the current) from data and wiring (4 kHz pulls almost none), and
**silence** for hiss or hum from ground or supply. `make audio` renders them
into `audio/test/`; `tools/sd_sync.py <ip> tones` puts them on the card.
The bare 4 Ω 3 W drivers measured clean only to 80 % on the porch (CanaKit
5 A, 2026-08-22) — `scenes.yaml`'s `hardware.audio.max_volume` is the cap
for a speaker like that; the shrouded pair that replaced them run the full
range, so it sits at 1.0 today.

