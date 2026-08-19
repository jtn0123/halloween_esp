# Wiring the castle: three pixel zones and two speakers

Everything here is for the board this project already runs on — an **Adafruit
ESP32-S2 Feather** with the **2.13" eInk FeatherWing** (which is what carries
the microSD slot) already seated on it. That wing has claimed a lot of pins,
so the pin choices below are not arbitrary; see [Pin budget](#pin-budget).

Two claims up front, because they shape the whole build:

1. **Each zone gets its own data pin.** Not one daisy chain.
2. **Both amplifiers hang off the same three I2S wires.** Two speakers cost
   zero extra GPIO.

---

## 0. First light tonight — with only the parts on hand

The rig as declared in `scenes/scenes.yaml` right now: **Jewel 7** in each
tower, **Ring 12** in the doorway — 26 pixels, all three zones RGBW. Your
parts box: pixels, two MAX98357A amps, speakers, the Feather with the eInk/SD
wing, and an SN74AHCT125. That is genuinely enough. Here is what each missing
"best practice" part actually does, and why you can start without it:

**The 470 Ω data resistors — skip for now.** They damp the reflection that
comes back off a pixel's data input when the cable between shifter and DIN
gets long. Reflections corrupt the first pixel's data and, over months,
can damage its input stage. At bench lengths they do nothing you can see.
Skip them while every data lead is **under ~30 cm**; buy three before the
fixtures move into the castle proper and the leads grow past half a metre.
(Some newer Adafruit pixel boards build a small series resistor in — treat
it as absent rather than checking die markings.)

**The 100 kΩ on each amp's SD pin — skip for now.** Leave SD unconnected and
the MAX98357A defaults to playing **(L+R)/2**, the average of both channels
— that is what the breakout's onboard divider selects. Both amps make the
same mono sound, which is exactly what this show sends. The only cost: if
ESPHome fills just one I2S slot, the average halves the level (−6 dB). If
it seems quiet, that is why, and the 100 kΩ to 5 V from §5 is the fix — not
a requirement for first light.

**The 1000 µF capacitor — skip while on USB power.** It absorbs the inrush
spike when a stiff 5 V supply is connected to a long run of pixels. A USB
port can't deliver a spike like that in the first place. The moment a real
5 V supply enters the picture (§4), the capacitor goes in with it.

**The SN74AHCT125 — do NOT skip.** This is the one part in the box that is
load-bearing, and it is the *right* part: a real 5 V-swing push-pull buffer
with TTL input thresholds, which is precisely what an 800 kHz pixel data
line wants. (If what you have is instead one of the little 4-channel
BSS138 "bidirectional level converter" boards: those are built for slow
open-drain buses like I2C, and at NeoPixel speeds their lazy edges are the
first suspect for glitching pixels. It may work with short leads. The
AHCT125 always works. §3 is wired for the AHCT125.)

So the whole no-new-parts build is:

1. Wire the shifter per [§3](#3-level-shifter), **without** the 470 Ω —
   `1Y/2Y/3Y` go straight to each fixture's `DIN`.
2. Power everything from the Feather's **USB pin** (that is the 5 V bus at
   bench scale): shifter VCC, all three fixture 5 V pads, both amp VINs.
   All grounds common, including the Feather's. Yes, §4 says never do this —
   that rule is for the full castle. 26 pixels on a 2 A brick stays inside
   what the USB trace handles; a 60-pixel rig does not.
3. Amps per [§5](#5-two-speakers-one-i2s-bus), SD pins left unconnected.
4. Feed the Feather from a **2 A+ USB-C wall brick**, not a laptop port.
   Two Jewels + the Ring 12 peak at 2.1 A on paper; the show's real draw is
   a fraction of that, but a laptop port will brown out on the lightning
   strikes and reboot the board mid-scene.
5. One thing to solder either way: header pins or leads onto the Jewels and
   the ring — and a breadboard for the shifter if yours is a bare DIP.

What actually breaks without resistors is nothing tonight and reliability
later: when the leads leave the bench and stretch into the towers, add the
three 470 Ω (fixture end) and move to a real supply with the capacitor.

---

## 1. Why three data lines instead of one chain

The firmware today drives 21 pixels as one chain: `towerL` is 0–6, `towerR` is
7–13, `door` is 14–20. That works because all three fixtures are identical
RGBW Jewels.

It stops working the moment you mix your inventory, for a reason that has no
software fix:

> **An RGBW pixel takes 32 bits. An RGB pixel takes 24.** A chain is one long
> shift register with no framing, so a single RGB fixture halfway down an RGBW
> chain shifts every downstream bit by 8 and everything past it turns to
> garbage. `esp32_rmt_led_strip` has exactly one `is_rgbw` setting per chain,
> and there is no per-fixture override, because the hardware has no concept of
> one.

Your **NeoPixel FeatherWing 4x8 is RGB only** — Adafruit #2945 is 24-bit, and
no RGBW version of it exists. The mini PCB singles are RGB too. Your Jewels,
Sticks and Rings each come in both flavours, so check the ones you actually
have. But with at least two RGB-only fixtures in the box, a single chain means
you can never put the FeatherWing in one window and an RGBW Jewel in another.

Three chains also mean swapping the door fixture doesn't renumber the towers,
which is the difference between "change one number" and "re-derive every index".

The costs are small and you have room for all of them:

| Cost | Have you got it? |
|---|---|
| 2 more GPIOs | Yes — A2 and A4 are unused |
| 2 more level-shifter channels | Yes — the 74AHCT125 is a **quad** buffer; you're using 1 of 4 |
| 3 of the ESP32-S2's RMT channels | Yes — the S2 has 256 RMT symbols in 64-symbol blocks, so 4 strips fit |

If all three of your fixtures happen to be the same type, one chain still
works and the app will generate that config too. Three is the one that lets
you stop thinking about it.

---

## 2. Pin budget

Everything already spoken for on this board:

| GPIO | Silk | Used by |
|---|---|---|
| 5 | D5 | microSD chip select (eInk wing) |
| 6 | D6 | SRAM chip select (eInk wing) |
| 9 | D9 | eInk chip select |
| 10 | D10 | eInk data/command |
| 35 / 36 / 37 | MOSI / SCK / MISO | SPI, shared by eInk + SD |
| 11 | D11 | I2S BCLK |
| 12 | D12 | I2S LRCLK |
| 15 | A3 | I2S DOUT |
| 17 | A1 | PIR sensor |
| 18 | A0 | **Pixel data, zone 1** (existing) |
| 13 | D13 | onboard red LED — avoid, it flickers with anything you put here |
| 33 / 21 | — | onboard NeoPixel and its power rail |
| 38 / 39 | RX / TX | left free deliberately: the only serial path on a board with no USB console |

That leaves **GPIO16 (A2)**, **GPIO14 (A4)**, **GPIO8 (A5)**, and the I2C pair
GPIO3/GPIO4 if you never add an I2C device.

> A5 is **GPIO8**, not GPIO7. Some third-party pinout tables have those two
> swapped. GPIO7 is not a header pin at all on this board — it switches power
> and the I²C pull-ups for the Stemma QT connector, per Adafruit's own pinout
> diagram. Do not plan a signal onto it.

### New assignments

| GPIO | Silk | Job |
|---|---|---|
| 18 | A0 | Zone 1 data — **tower L** (unchanged) |
| 16 | A2 | Zone 2 data — **door** |
| 14 | A4 | Zone 3 data — **tower R** |
| 8 | A5 | spare |

GPIO15 and GPIO16 are the chip's `XTAL_32K` pins. This board has no 32 kHz
crystal fitted — GPIO15 has been carrying I2S data reliably since v5.x, which
is the proof — so GPIO16 is equally free.

---

## 3. Level shifter

WS2812-family pixels running on 5 V want a logic high of at least
0.7 × VDD = **3.5 V**. The ESP32 puts out 3.3 V. It often works and then
mysteriously doesn't when the run gets longer or the room gets warmer, which
is why the 74AHCT125 is already in this build. You need three of its four
channels now.

**74AHCT125, 14-pin DIP:**

```
   ┌───────∪───────┐
1OE│1            14│VCC ── 5V
 1A│2            13│4OE ── 5V   (unused channel, disabled)
 1Y│3            12│4A  ── GND
2OE│4            11│4Y     n/c
 2A│5            10│3OE ── GND
 2Y│6             9│3A
GND│7             8│3Y
   └───────────────┘
```

| Wire | From | To |
|---|---|---|
| power | 5 V bus | pin 14 (VCC) |
| ground | common ground | pin 7 (GND) |
| enable ×3 | GND | pins 1, 4, 10 (`1OE`, `2OE`, `3OE` — active low) |
| zone 1 in | GPIO18 | pin 2 (`1A`) |
| zone 1 out | pin 3 (`1Y`) | → 470 Ω → tower L `DIN` |
| zone 2 in | GPIO16 | pin 5 (`2A`) |
| zone 2 out | pin 6 (`2Y`) | → 470 Ω → door `DIN` |
| zone 3 in | GPIO14 | pin 9 (`3A`) |
| zone 3 out | pin 8 (`3Y`) | → 470 Ω → tower R `DIN` |
| unused | pin 13 → 5 V, pin 12 → GND | leaves channel 4 off rather than floating |

The **300 Ω–500 Ω series resistor goes at the fixture end** of the cable, not
at the shifter. It's there to damp reflections coming back off the pixel's
input, and it can only do that if it's next to the pixel. 470 Ω is what this
build already uses.

> The 74AHCT125 must be powered from **5 V**, not 3.3 V. Its whole function is
> that it reads 3.3 V-compatible TTL input thresholds while driving a 5 V
> output swing. Powered from 3.3 V it is an expensive piece of wire.

---

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

## 6. Build order

Do it in this order and each step proves the one before it.

1. **Grounds first.** Supply, Feather, shifter, both amps. Confirm continuity
   with a meter before anything is powered.
2. **Power the shifter alone.** 5 V on 14, GND on 7, the three `OE` pins to
   GND. Check pin 14 reads 5 V.
3. **One zone.** Wire tower L only — GPIO18 → `1A`, `1Y` → 470 Ω → `DIN`, plus
   5 V/GND and the capacitor. Boot it. You already know what a working Jewel
   looks like, so this is the honest test of the shifter.
4. **The other two zones.** Same pattern on GPIO16 and GPIO14. Set the rig in
   the app first so the firmware knows the counts (§7).
5. **One amp.** `SD` to 5 V through 100 kΩ. Play a scene. Confirm full volume,
   not a quiet one.
6. **The second amp.** Same three signal wires. Both should now be equally
   loud.
7. **Measure the real peak.** Run the Storm scene with a clamp meter or an
   inline USB meter on the 5 V bus. Compare it against the app's estimate
   before you close the enclosure.

## 7. Choosing the fixtures without soldering them

The cue desk carries the rig as data now rather than as an assumption. The
**Rig** panel (right-hand column, under Output) has a row per spot:

- Pick a fixture and the stage, the per-pixel view and the channel strip all
  change on the next frame. A chase really does walk sixteen pixels round a
  Ring 16; a meteor really does fall down the FeatherWing's four rows.
- The RGBW tickbox is only offered where the part comes both ways. The
  FeatherWing and the mini PCBs are RGB and say so.
- It totals the peak draw as you go, and warns when the mix needs separate
  data lines or the supply needs to be bigger. The 8 A figure in §4 is that
  calculation.
- **Copy firmware config** emits both halves of the change: the `zones:` block
  for `scenes/scenes.yaml` and the substitutions for `firmware/castle.yaml`.

Then, once you like it:

```bash
make generate && make validate
```

`make generate` rewrites `firmware/generated/lights.yaml` (one strip per zone)
and `firmware/generated/rig.h` (the geometry tables the render loop indexes),
then `make upload` puts it on the castle.

The preview is instant; the castle needs that reflash. Pixel counts are
compiled in, so there is no way around it — but it does mean the only thing
you flash is a rig you have already looked at.

> **The two files move together.** `scenes.yaml` is what the cue generators
> and the desk read; the substitutions are what the chip clocks out. Change
> one without the other and you get a castle whose cues are aimed at pixels it
> does not have. That is why the button emits both.
