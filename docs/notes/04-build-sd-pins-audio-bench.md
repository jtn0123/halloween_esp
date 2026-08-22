# Halloween Castle — Build — microSD, eInk pins, audio capacity, decode benchmark, logs (§12.9–§12.14)

Part of the design record; the index is [`PROJECT_NOTES.md`](../../PROJECT_NOTES.md). Section numbers are global across the parts, so `§12.9` means the same thing in every file.

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
