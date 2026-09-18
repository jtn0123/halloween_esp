# Halloween Castle

A store-bought decorative castle with three lit apertures — two tower windows and
a doorway — driven by an ESP32-S3 Feather running ESPHome. Addressable RGBW pixels,
pre-rendered spooky audio, and a cue engine that keeps the two in step.

See [PROJECT_NOTES.md](PROJECT_NOTES.md) for the design record and the hardware
research behind every choice here — it is an index; the record itself lives in
parts under [`docs/notes/`](docs/notes/). The trust model — local-only, two
permanently accepted risks — is [docs/SECURITY.md](docs/SECURITY.md). For the night-to-night view —
adding a song end to end, pushing to the castle, what to check when a scene
will not play — see [docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## How it fits together

```
scenes/scenes.yaml            ← THE SOURCE OF TRUTH
        │
        ├── tools/render_audio.py ─▶ core/ scene_render ─▶ audio/NN_<id>.mp3
        │                            (Rust: synths, reverb, master chain)
        ├── tools/gen_esphome.py  ──▶ firmware/generated/     (light cue scripts)
        └── previewer/            ──▶ browser cue desk        (tuning tool)
```

One file defines every scene: its light cues, its audio score, its length and its
playback level. Everything else is generated. Cue timings tuned in the previewer
cannot drift away from the ones on the device, because both come from here.

`core/` is castle-core, the project's zero-dependency Rust crate. It renders the
scene audio (`scene_render`) and analyses imported tracks (`analyze_track`) —
those are the production paths, not experiments; the Python originals survive
only as the parity references the Rust is checked against. It also holds a
WASM face the cue desk loads and the studio server itself — the twin became
the default on 2026-09-01, with the Python one behind it. Everything else
reaches the crate through
`tools/core_bins.py`, as a subprocess: no cargo means a hard stop with a
sentence, never a quiet fall-back to arithmetic that differs per machine.

### Why the audio is pre-rendered

The MAX98357A plays one stream, and mixing on the board itself is not worth
fighting (it was never an option at all on the single-core ESP32-S2 this show
was written for). So each scene is rendered offline into a single mixed file — where
convolution reverb, ducking and crossfades are free — and the firmware's only job
is to play it. This raises the quality ceiling rather than lowering it.

---

## Hardware

| | |
|---|---|
| MCU | Adafruit ESP32-S3 Feather [#5477](https://www.adafruit.com/product/5477) — 240 MHz, 4 MB flash, 2 MB PSRAM, on castle-carrier v3.3a. An ESP32-S2 Feather ran the porch until 2026-09-17 ([docs/notes/03-build.md](docs/notes/03-build.md) §12.20) |
| Audio | MAX98357A I2S class-D amp ([adafruit 3006](https://www.adafruit.com/product/3006)) → 4 Ω speaker |
| Light | 2 × NeoPixel Jewel 7 RGBW (towers) + NeoPixel Ring 12 RGB (door) — three zones, 26 pixels; see [docs/WIRING.md](docs/WIRING.md) |
| Sensor | AM312 PIR on the walkway |

I2S and RMT are separate peripherals, so audio and pixels never contend for
hardware.

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

**Why not D5/D6/D10, which would be the obvious choices?** History: until
v5.44 the microSD slot came on a 2.13" eInk FeatherWing, which hard-wires SD
chip select on D5, SRAM chip select on D6, eInk chip select on D9 and eInk
data/command on D10 — and putting 800 kHz NeoPixel data on the SD card's
chip select is not a mistake you find quickly. The wing and its status
panel are gone (the page does that job now), the carrier board's own card
socket kept D5, and D6/D10 went to the carrier's 5 V sense and wired button
instead. The signals stayed where they were soldered.

Put a 1000 µF capacitor across 5 V/GND at the pixels. With 26 pixels the
worst case (a full-white lightning strike) is ~2 A, so **split the 5 V supply
before the Feather** rather than drawing it all through its USB trace.

---

## Getting started

You need Python 3.13 and **a Rust toolchain** (`rustup`, which brings cargo).
`make setup` does not install Rust, and the step after it does not work
without one: `make audio` renders through castle-core and stops with a
sentence rather than falling back to Python. Node 22+ is needed only for the
cue desk's own build and tests.

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # once
make setup      # venv + esphome + render deps + the commit hook
make audio      # render the scene audio (builds core/ on first use)
make validate   # check the config without a toolchain
make build      # compile firmware/castle_feather_s3.yaml — the castle in the yard
make build-s3   # compile firmware/castle_s3.yaml — the ESP32-S3-WROOM-1 carrier
make build-fs3  # an alias for build; the castle IS the S3 Feather
make upload     # flash over USB
make publish    # push the rendered show to the castle's microSD card
```

Copy `firmware/secrets.yaml.example` to `firmware/secrets.yaml` and set real
WiFi credentials before flashing. `make help` lists every target.

There are two targets because there are two boards. `castle_feather_s3.yaml`
is the ESP32-S3 Feather (#5477, the 2 MB PSRAM one) in the castle-carrier
v3.3a the S2 was drawn for — it is the castle in the yard, and has run the
show since 2026-09-17. `castle_s3.yaml` (2026-09-05) is the
ESP32-S3-WROOM-1 carrier board (castle-carrier v5), written from that
project's spec with no board in hand; it has never been on hardware and is
compiled weekly by CI so it cannot rot. Both share every line of the show and
not one GPIO number differs between them — see `firmware/pending/README.md`
for what has run on what, and the bring-up list.

The scene audio lives on the card, not in the image — `make publish` is what
puts it there, and a board flashed without it chirps instead of playing. That
used to be a choice between two builds; the show's real songs weigh 2.2 MB
and an OTA slot holds 1.75 MB, so on 2026-09-01 the all-in-flash build was
retired and the card became the only way the castle plays (`docs/notes/03-build.md`
§12.15).

When both `site/index.html.gz` and `site/index.html` are on the card, `/`
serves the gzipped copy. `make publish` / `sd_sync site` writes both; a hand
copy must include the `.gz` or a newer plain file is ignored. A PUT whose
`Content-Length` is larger than the body that arrives is a known ESP-IDF
httpd limit (the socket closes on the short read). The API server has four
open sockets (`firmware/sd_web.h`); that is the board's pool, not a desk bug.

---

## Castle Radio on your desktop

Run `./tools/install_castle_tools.sh` once on an Apple Silicon Mac. Existing
installations can double-click **Enable Website Startup.command** once instead.
Then use **Import music → Start Mac tools → Connect Mac tools** on the castle
website. The helper runs in the background without Terminal; keep the small
connection window open. Startup checks tools without installing anything. [Desktop setup and limitations](demo/castle-radio/README.md).

## The cue desk

```bash
make studio     # http://127.0.0.1:8765 — the previewer plus a local server
```

The previewer is one static HTML file (`previewer/castle-cue-desk.html`, built
by `make preview` from `web/src/`). Behind it, the studio server adds what a
static page cannot do: the **Tracks** panel imports audio (a file, or a link via
yt-dlp), shows onsets and waveforms, auditions clips, writes scenes into
`scenes/scenes.yaml`, and sends files to the castle's SD card when one answers.
`--lan` opens it to the phone/iPad remote; leave it off otherwise — a LAN
visitor has the whole desk, not a read-only view: they can import and delete
tracks, rewrite `scenes/scenes.yaml`, send files to the castle and stop the
server (`POST /studio/server/stop`), with no login. The route
table — what the studio owns (`/studio/…`) and what it relays to the castle
(`/api/…`) — is [docs/API.md](docs/API.md).

The studio is `core/src/bin/studio.rs`: `make studio` runs
`tools/studio_launch.sh`, which builds the binary when cargo is present and
execs it, and says why it cannot when there is no cargo and no build. There
was a second studio in Python until 2026-09-06, kept as the reference the
Rust one was measured against; [docs/RETIREMENT.md](docs/RETIREMENT.md) is
the plan that retired it and the tag `python-studio-final` is the last tree
carrying it. The server is Rust; the toolchain it spawns for every rebuild,
import and push is still Python, and that is the design rather than a
leftover — [docs/PARITY.md](docs/PARITY.md) says what is held equal to what.

Four environment variables sandbox it: `CASTLE_TRACKS` (track library
directory), `CASTLE_SCENES` (the scenes file it may write), `CASTLE_HOST`
(the castle's address; set-but-empty means "no castle") and `CASTLE_BUILD`
(where a rebuild's audio, generated firmware and previewer page land). The
tests set them so a run can never touch the real show. `tools/castle_emu.py 8093` plus
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

The five implementations of the show's arithmetic (Python generators, TS
effects, C++ firmware, host-compiled dump, and castle-core in `core/`) are kept
bit-exact by seeded fuzz — [docs/PARITY.md](docs/PARITY.md) is the contract, the
list of every duplicated copy, and what to do when one of them goes red.
`make rust` builds the crate's binaries; `make rust-test` and `make rust-lint`
(fmt --check + clippy -D warnings) are the crate's own gates, and the same
checks ride inside `tests/test_castle_core.py` so `make check` covers `core/`
too — but only on a machine that has cargo and a host C++ compiler.
`make rust-coverage` is the crate's `cargo llvm-cov` summary, non-gating like
`make coverage`.

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
| Crypt | ambient, loops | 24 s | Heartbeat at 48 bpm, tritone drone, whispers; near-darkness is the effect |

Imported songs take the rest of the budget: the board holds **12 scenes**
(`SCENE_LIMIT` in `tools/check_loc.py`, and the desk refuses the thirteenth),
so the eight written above leave four slots for tracks brought in through the
Tracks panel, whose cues are onset-detected from the audio itself. Two of
those four are filled in `scenes/scenes.yaml` today.

All musical material written for the castle is original, written in the haunted-parlour idiom
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
