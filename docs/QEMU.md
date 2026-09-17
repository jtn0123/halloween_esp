# Booting the S3 image in QEMU

`firmware/castle_s3.yaml` has never been on hardware — its own header says so,
and `firmware/pending/README.md` carries the bring-up list that will change
that. Until the carrier board arrives, the only thing anyone had watched the
S3 image do was compile. This is the next-cheapest thing: run it in
Espressif's ESP32-S3 QEMU and read the console.

    tools/qemu_boot.sh

That compiles `firmware/castle_s3_qemu.yaml`, pads the factory image to the
8 MB the WROOM-1-N8R2 declares, boots it, and prints the UART. `--gdb` adds a
backtrace of both cores at the end, which is how everything below was found.

It needs **Espressif's** QEMU fork. Homebrew's `qemu-system-xtensa` is
mainline and has no `esp32s3` machine at all; the script checks and says so.
Release tarballs — macOS arm64 and x86_64, Linux, Windows, ~3.8 MB — are at
<https://github.com/espressif/qemu/releases>. Point `QEMU_XTENSA` at the
binary or put it on `PATH`. Everything here was verified 2026-09-06 against
`esp-develop-9.2.2-20260417` on macOS 27 arm64.

## What it boots, and why not castle_s3.yaml itself

`firmware/castle_s3_qemu.yaml` includes `castle_s3.yaml` whole and overrides
two things, each because the run is useless without it. The file's own header
argues both at length; in short:

- **`logger: hardware_uart: UART0`.** QEMU emulates the UARTs and does not
  emulate USB Serial/JTAG, which is what the carrier logs over. With the real
  setting you get the ROM's UART0 chatter and then silence from the
  second-stage bootloader onward.
- **`wifi: enable_on_boot: false`.** QEMU emulates no Wi-Fi for the S3.
  `esp_wifi_start()` never returns: the PHY calibration ROM routine spins on
  registers that read back as zeros, inside `ppTask` at priority 23, which
  starves the priority-1 main task on the same core.

It also renames the build so a QEMU image can never land in the directory
`make upload-s3` flashes from.

## What a run actually shows

Trimmed of the segment table; this is a real run.

    ESP-ROM:esp32s3-20210327
    rst:0x1 (POWERON),boot:0x4 (SPI_FLASH_BOOT)
    I (15) boot: ESP-IDF v5.5.5 2nd stage bootloader
    I (24) boot.esp32s3: SPI Flash Size : 8MB
    I (28) boot: ## Label            Usage          Type ST Offset   Length
    I (28) boot:  0 otadata          OTA data         01 00 00009000 00002000
    I (29) boot:  1 phy_init         RF data          01 01 0000b000 00001000
    I (29) boot:  2 app0             OTA app          00 10 00010000 003c0000
    I (30) boot:  3 app1             OTA app          00 11 003d0000 003c0000
    I (30) boot:  4 nvs              WiFi data        01 02 00790000 00070000
    I (31) boot: No factory image, trying OTA 0
    I (102) boot: Loaded app from partition at offset 0x10000
    E (115) quad_psram: PSRAM chip is not connected, or wrong PSRAM line mode
    E cpu_start: Failed to init external RAM; continuing without it.
    [I][logger:122]: Log initialized
    [I][app:060]: Running through setup()
    [I][i2c.idf:205]: Performing bus recovery
    [E][i2c.idf:242]: Recovery failed: SCL is held LOW on the bus
    [I][health:067]: boot #1 (0 crashes so far), reset: power-on
    [I][esp-idf:000]: E (11648) i2c.master: I2C software timeout
    [E][component:204]: ina219.sensor was marked as failed
    [I][speaker_media_player:098]: Set up speaker media player
    [W][castle:021]: no SD card — playing the fallback chirp
    <nothing more, ever>

### Things in there that are worth having

- The dual-OTA partition table the S3 build asks for is the one the
  bootloader reads: **two 0x3C0000 app slots** and a 0x70000 nvs, at the
  offsets `partitions.csv` names. This is the layout `flash_size: 8MB` was
  supposed to buy and now visibly does.
- The app's six segments load and it starts — the image links AND runs, which
  compiling never proved.
- `castle_health` runs and its boot counter works: `boot #1 (0 crashes so
  far), reset: power-on`. Boot into the same flash image repeatedly and it
  counts up, so the NVS-backed side of it is exercised, not just compiled.
- `[E][component:204]: ina219.sensor was marked as failed` — the INA219 the
  carrier adds is the one component in the S3 build with no counterpart on
  the Feather, and QEMU is the first place it has ever been asked to run. It
  fails the way a missing chip should: the I2C bus recovery reports SCL low,
  the driver times out, ESPHome marks the component failed, and **the rest of
  setup() carries on**. That was worth knowing before the board exists.

### Things in there that are QEMU, not the firmware

- **PSRAM "not connected".** The machine instantiates an `ssi_psram` at CS 0
  but the guest's quad-mode detection does not find it. Harmless here only
  because `CONFIG_SPIRAM_IGNORE_NOTFOUND=y`; the run therefore says nothing
  about whether the carrier's PSRAM works, and everything the show puts in
  PSRAM was silently in internal RAM for this boot.
- **The I2C failure**, above. Real information about the failure path, no
  information about the INA219 itself.
- **`chip revision: v0.0`** — QEMU's, not a WROOM-1's.

## Where it stops

At the SD card mount, in `on_boot` priority -200
(`firmware/castle_sd_common.yaml`), and it never comes back:

    castle_sd::mount            sd_audio.h:93
      esp_vfs_fat_sdspi_mount
        sdmmc_card_init -> sdmmc_io_reset -> sdmmc_send_cmd
          sdspi_host_start_command -> poll_busy
            spi_device_polling_transmit(portMAX_DELAY)
              spi_hal_usr_is_done()          <- spins forever

The card is an SD in **SPI mode on SPI2_HOST**. QEMU's esp32s3 machine
emulates the SPI1 flash controller — it prints "Adding SPI flash device" and
nothing else — so GPSPI2's registers read as zeros, transaction-done never
sets, and a poll with `portMAX_DELAY` is exactly as patient as it sounds.
That runs on the main loop task, so nothing after it happens.

This is not reachable around from config. Moving `sd_cs`/`sd_sck`/`sd_mosi`/
`sd_miso` to a nonexistent GPIO99 to make `spi_bus_initialize()` refuse the
bus was tried: IDF accepts the config and the mount hangs one call later,
identically. Skipping the mount would mean editing `castle_sd_common.yaml`,
which is the shared show — not something to bend for an emulator.

Note the shape of the failure, because it is a trap for reading these logs:
`sd_audio.h`'s own "no SD card mounted (…) — scenes will play the chirp, not
the show" **never prints**. The line that does appear, `[W][castle:021]: no SD
card — playing the fallback chirp`, is a different message from a different
moment — `tools/gen_esphome_audio.py` emits it into `generated/audio_sd.yaml`
for the play path. Seeing it is not evidence the mount returned.

## So what does a green run prove, and what can it not

**Proves.** The image boots. The bootloader reads the intended dual-OTA
partition table on an 8 MB flash. The app loads and starts. ESPHome reaches
`setup()` and gets through the logger, I2C, `castle_health`, the INA219's
failure path, the I2S speaker and the media player without a panic, an abort,
or a watchdog. For a build whose header reads "NOTHING HERE HAS EVER BEEN ON
HARDWARE", that is a real step and it cost one afternoon.

**Cannot.** Everything that makes it this castle:

- **No Wi-Fi**, so no API, no OTA, no `sd_web.h`, no `sd_sync`, nothing the
  cue desk talks to. And the override that gets past it means even the
  association attempt is not exercised.
- **No SD card**, so no scene audio, no `manifest_check`, no
  `castle_health::log_boot_to_sd`, no `castle_web::start()` — the boot ends
  before all four.
- **No RMT strips.** The lights are never reached, so the S3's 48-word RMT
  block budget (`castle_s3.yaml` §13.4, `tools/gen_rig.py`) — arguably the
  single riskiest untested thing about the port — is exactly as untested
  after a QEMU run as before it.
- **No I2S out**, so no audio, and no answer to `docs/ISSUE-scene-start-audio.md`.
- **No PSRAM**, so no answer about the memory the show actually leans on.
- **Nothing about timing**, current draw, RF, or the `ring flicker` in
  `docs/ISSUE-ring-flicker.md`. QEMU is not that kind of instrument.

## Should this be CI?

**No — not as a gate.** The weekly compile job already catches what changes
week to week (the framework bump that overflows a segment, a generator that
emits something that will not build). A QEMU step could only assert "these
log lines appeared within N seconds", and the run it would assert about is a
DIFFERENT BUILD from the one shipped — different console, Wi-Fi off — that
stops before every component this repo actually worries about. Two hangs deep
in vendor code is also a bad thing to own in CI: the next IDF or QEMU release
moves them, and the failure looks like a firmware regression.

**Yes as a bring-up tool, kept and run by hand.** It answered a question that
had no other answer before the carrier ships, it is one script and one
30-line YAML, and it is the fastest way to see a change in `setup()` ordering
or a new component's failure path actually happen. Run it when the S3 build
changes shape; do not make anything depend on it.

Revisit if Espressif's QEMU grows GPSPI or SDMMC for the S3 — with the card
mounting, the run would reach `castle_web::start()`, `manifest_check` and the
main loop, and the argument above changes.
