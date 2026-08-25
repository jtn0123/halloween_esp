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

| MAX98357A pin | Connect to |
|---|---|
| `VIN` | 5 V bus |
| `GND` | common ground |
| `DIN` | GPIO15 (A3) — **both amps** |
| `BCLK` | GPIO11 (D11) — **both amps** |
| `LRC` | GPIO12 (D12) — **both amps** |
| `SD` | 100 kΩ to 5 V — **both amps** (see below) |
| `GAIN` | leave unconnected for the default 9 dB |
| `+` / `−` | speaker terminals |

### The SD pin, and why 100 kΩ

`SD` is not just a shutdown pin — its *voltage* selects which channel the amp
plays. The chip has an internal 100 kΩ pulldown, and the Adafruit breakout
adds a 1 MΩ pullup to VIN, so the pin sits at about 0.45 V by default:

| SD voltage | Amp plays |
|---|---|
| < 0.16 V | shut down |
| 0.16 – 0.77 V | (L+R)/2 — **the factory default** |
| 0.77 – 1.4 V | right channel |
| > 1.4 V | left channel |

Adding **100 kΩ from `SD` to 5 V** pulls the pin to roughly 2.6 V, which
selects **left** on both amps.

That looks wrong for stereo, and it is deliberate. The firmware renders and
plays **mono** (`channel: mono`, `num_channels: 1`). Depending on how a mono
frame gets packed into the I2S slots, the right slot may carry silence — in
which case an amp in the default (L+R)/2 mode plays at **half amplitude, 6 dB
down**. Pinning both amps to *left* gives full level whichever way the frame
is packed, so it is the choice that can't be wrong.

If you later want real stereo, that's two changes together — the amps and the
firmware, never one without the other:

- Left amp: 100 kΩ from `SD` to 5 V.
- Right amp: ~760 kΩ from `SD` to 5 V (330 k + 330 k + 100 k in series is the
  combination Adafruit suggests), landing the pin near 1.0 V.
- Firmware: `channel: stereo` on the speaker, and re-render the scene audio
  as 2-channel — which roughly doubles the audio size, so check the budget
  card before committing.

### Speaker wiring

The MAX98357A output is **bridge-tied**. Both terminals swing; neither is
ground.

- Never connect a speaker `−` to ground. You will short half the bridge.
- Never join the two amps' outputs.
- 4 Ω or 8 Ω, rated 3 W or better. 4 Ω is louder; 8 Ω runs cooler.
- If it's too quiet, tie `GAIN` to GND for 12 dB, or 100 kΩ to GND for 15 dB.

---

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

