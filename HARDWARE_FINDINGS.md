# Hardware findings — ESP32-S2

Measured facts about the boards this project runs on, kept separate from
PROJECT_NOTES so it stays useful to other ESP32-S2 projects. Everything here
was observed on real hardware or read out of installed source, not inferred
from documentation or forum wisdom. Where something is inference, it says so.

Applies to the **ESP32-S2-MINI-1** module — the castle Feather and the garage
device are the same chip.

---

## 1. The chip

| | |
|---|---|
| Cores | **1** (Xtensa LX7 @ 240 MHz) — the ESP32 and S3 are the dual-core ones |
| Internal SRAM | 320 KB |
| PSRAM | 2 MB, of which **1713 KB free** with the castle firmware running |
| Flash | 4 MB |
| WiFi | 2.4 GHz only, WiFi 4. No 5 GHz, no 6 GHz, no MLO |
| PSRAM DMA | **Not supported** by ESP-IDF on the S2. I2S buffers must live in internal SRAM, costing a copy per frame that the S3 avoids |
| SDMMC host | **None.** SPI is the only way to talk to an SD card |
| USB Serial/JTAG | **None.** This is the root of §3 |

---

## 2. MP3 decode — measured, 2026-08-10

The headline: **it works, comfortably.** No published ESP32-S2 MP3 benchmark
appears to exist, and both well-known ESP32 audio libraries decline to support
the chip, so this was measured directly with `make bench-audio`.

Conditions: full ESPHome audio pipeline (MP3 decode → resample → I2S out) at
96 kbps mono / 44.1 kHz, with WiFi associated and the `esp32_rmt_led_strip`
driver updating 21 pixels every 16 ms. I2S pins unconnected. 105 samples over
~3.5 minutes.

| Metric | Result |
|---|---|
| Loop time, average | 32.7 ms |
| Loop time, max | 60 ms |
| Free internal SRAM | 91 KB |
| Free PSRAM | 1713 KB |
| Buffer underruns | **0** |
| Decode errors | **0** |
| Blocking-loop warnings | **0** |

**Caveat:** every sample reported `playing=yes`, because the `vigil` scene loops
and had already started before the log connection was made. There is therefore
**no idle baseline**, and the idle-vs-playing delta the benchmark was designed
to show was not captured. The absolute figures stand; the comparison does not
exist yet. Worth re-running with a deliberate idle window, and again once real
pixels and a real amp are drawing current.

### Capacity implied by 1713 KB free PSRAM

For the whole-file-into-PSRAM playback design (`firmware/sd_audio.h`):

| Format | Bytes/sec | Max length |
|---|---|---|
| 96 kbps mono | 12.0 KB/s | 2:26 |
| 64 kbps mono | 8.0 KB/s | 3:39 |
| 48 kbps mono | 6.0 KB/s | 4:52 |
| 128 kbps stereo | 16.0 KB/s | 1:49 |

Streaming from the card removes this ceiling entirely.

### Codec notes

| Codec | Decode cost | 4 min mono | Verdict |
|---|---|---|---|
| WAV | ~zero (memcpy to I2S) | 21 MB | Streaming only. The safe fallback if decode ever becomes marginal |
| MP3 | measured fine above | 2.8 MB @96k | **Current choice** |
| FLAC | moderate | ~10 MB | Streaming only, and saves nothing audible through a small speaker |
| Opus | highest | 0.96 MB @32k | Best quality per byte, only codec that fits 4 min in RAM at quality — but CPU unproven on S2 |

`AudioFileType`'s members are compiled in **conditionally**, per the pipeline's
declared `format:`. Referencing `WAV` in an MP3-only build is a compile error.

---

## 3. Getting logs off an S2 — read this before debugging anything

Three findings that cost a full evening.

**There is no USB serial console, and there never was.** The S2 has no USB
Serial/JTAG peripheral. Its only USB is the OTG peripheral, and ESPHome does not
run a TinyUSB CDC stack, so the application never enumerates a serial port.
Every port that appeared — `usbmodem101`, `usbmodem01` — was the ROM
bootloader's. `esphome logs --device /dev/cu.*` cannot work here.

**`CONFIG_ESP_CONSOLE_USB_CDC` panics the chip.** Setting it looks like the
obvious fix and is actively harmful. Crash log, recovered over the API:

    Reason: Interrupt wdt - Interrupt wdt timeout on CPU0
      esp_usb_console_flush_internal   (usb_console.c:364)
      esp_usb_console_write_buf        (usb_console.c:416)
      panic_print_char_usb_cdc         (panic.c:104)

With it set the board enumerates **nothing** over USB — no app port, no
bootloader port — and the only way back in is holding BOOT while tapping RESET.

**mDNS does not resolve on this network.** `castle-benchaudio.local` never
resolved, and `dns-sd -B _esphomelib._tcp` returned nothing, while the device
was connected and healthy the entire time. Find it by scanning for the ESPHome
API port instead:

```bash
for i in $(seq 1 254); do (nc -G 1 -z 10.27.27.$i 6053 2>/dev/null && echo "10.27.27.$i") & done; wait
```

Then `esphome logs <config>.yaml --device <ip>` works normally, and will also
dump any crash from the previous boot with a decoded backtrace — which is how
the USB console panic above was found.

**The meta-lesson:** silent serial and failed mDNS were *both* instrumentation
failures, and looked exactly like a dead device while the firmware ran fine.
The only reliable signal all evening was looking at the onboard LED. When a
board seems dead, verify it is dead before changing anything.

---

## 4. Flashing

The board drops into ROM download mode for flashing and does not reliably leave
it — `Hard resetting via RTS pin` and an explicit `esptool run` both left it in
the bootloader. **Press RESET physically** after flashing.

To force download mode (needed if the app doesn't enumerate): hold **BOOT**,
tap **RESET**, release BOOT.

Identify the state from the USB descriptor — the ROM bootloader enumerates as:

    USB Product Name = "ESP32_S2",  idProduct = 2,  USB Serial Number = "0"

A running ESPHome app enumerates *nothing*, so "no USB device" means the app is
running, and "ESP32_S2 / PID 2" means it is in the bootloader. This is inverted
from most boards and worth remembering.

---

## 5. Pin conflicts with the 2.13" eInk FeatherWing

The wing (products 4128 / 4195 / 4814) hard-wires four header positions and
Adafruit uses one convention across the family:

| Wing signal | Header | GPIO | Cuttable? |
|---|---|---|---|
| SD chip select | D5 | 5 | yes |
| SRAM chip select | D6 | 6 | yes |
| eInk chip select | D9 | 9 | no |
| eInk data/command | D10 | 10 | no |

Three of the castle's five signals collided. Current map:

| Signal | GPIO | Note |
|---|---|---|
| NeoPixel data | 18 (A0) | moved off 5 — data on the SD chip select at 800 kHz would have caused intermittent card mounts |
| PIR input | 17 (A1) | moved off 6 |
| I2S DOUT | 15 (A3) | moved off 10. Avoided D13, which drives the onboard red LED and would flicker with audio |
| I2S BCLK | 11 | untouched by the wing |
| I2S LRCLK | 12 | untouched by the wing |

*Inference flag:* Adafruit documents the wing's connections positionally and
never names the ESP32-S2 Feather. The D5/D6/D9/D10 → GPIO 5/6/9/10 mapping is
forced by the silkscreen order plus the CircuitPython board definition, but it
is not a published table. **Check with a continuity meter before soldering.**

Also: the eInk and SRAM chip selects share the card's SPI bus and we use
neither. Both are driven HIGH at boot in `castle_sd.yaml` — a floating chip
select is a device that may answer mid-transaction, which is the difference
between "the card works" and "the card works most of the time".

### Free GPIOs on this board

Usable: 8 (A5, the only WiFi-safe ADC), 13 (D13, drives the red LED), 14–18
(A4–A0, ADC2 so unusable as ADCs while WiFi is on, fine as digital), 38/39
(RX/TX, free unless you need a hardware UART).

Unusable: 19/20 (native USB), 26–32 (SPI flash + PSRAM inside the module),
7 (`I2C_POWER`, must stay high), 21 (`NEOPIXEL_POWER`), 33 (onboard NeoPixel),
45/46 (strapping), 0 (BOOT button, strapping).

---

## 6. Confirmed working

- **NeoPixel output** — verified on real hardware. The effect engine, per-pixel
  seeding and cue timing all render as designed.
- **WiFi association** to a dual-band 2.4/5 GHz SSID.
- **ESPHome API** over the network, including crash-log retrieval with decoded
  backtraces from the previous boot.
- **MP3 playback pipeline** end to end, per §2.

## 7. Not yet verified

- Audio through a real amp and speaker (I2S pins have only ever been unconnected)
- The remapped pins against physical hardware (see the inference flag in §5)
- microSD — no card has been mounted yet; `firmware/castle_sd.yaml` compiles but
  has never run
- Idle-vs-playing loop time delta (see the caveat in §2)
