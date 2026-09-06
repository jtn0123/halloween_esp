#!/usr/bin/env bash
#
# Boot the ESP32-S3 image in Espressif's QEMU and print what it says.
#
#   tools/qemu_boot.sh                 compile if needed, boot, print the log
#   tools/qemu_boot.sh --seconds 90    give it longer than the default 60
#   tools/qemu_boot.sh --no-build      boot whatever is already compiled
#   tools/qemu_boot.sh --gdb           boot, wait, then dump every backtrace
#
# WHY THIS EXISTS. The S3 carrier board does not exist yet (castle_s3.yaml's
# own header: "NOTHING HERE HAS EVER BEEN ON HARDWARE"), so the only thing
# anyone had ever seen the S3 image do was compile. This gets it as far as a
# running console: the ROM, the second-stage bootloader, the partition table
# it actually reads, the app's segments loading, and ESPHome's own setup().
#
# WHAT IT BOOTS. Not castle_s3.yaml — firmware/castle_s3_qemu.yaml, which
# includes it and overrides the console and the Wi-Fi-at-boot flag, because
# QEMU emulates neither. That file's header says why, one override at a time.
#
# WHERE IT STOPS, AND THAT IS EXPECTED. The boot runs into the SD card mount,
# which polls a SPI controller QEMU does not emulate and never returns. See
# docs/QEMU.md — the log up to that point is the deliverable, not a failure.
#
# WHAT IT NEEDS. Espressif's QEMU fork; mainline/Homebrew qemu-system-xtensa
# has no esp32s3 machine and will not do. Point QEMU_XTENSA at the binary, or
# put it on PATH. Release tarballs (macOS arm64/x86_64, Linux, Windows):
#
#   https://github.com/espressif/qemu/releases
#   e.g. qemu-xtensa-softmmu-esp_develop_<ver>-aarch64-apple-darwin.tar.xz
#
# Verified 2026-09-06 against esp-develop-9.2.2-20260417 on macOS arm64.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YAML="$REPO/firmware/castle_s3_qemu.yaml"
NAME="castle-s3-qemu"

SECONDS_TO_RUN=60
DO_BUILD=1
DO_GDB=0
while [ $# -gt 0 ]; do
  case "$1" in
    --seconds) SECONDS_TO_RUN="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --gdb) DO_GDB=1; shift ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# ── The emulator ─────────────────────────────────────────────────────────
QEMU="${QEMU_XTENSA:-$(command -v qemu-system-xtensa || true)}"
if [ -z "$QEMU" ]; then
  echo "no qemu-system-xtensa found. Set QEMU_XTENSA to Espressif's build" >&2
  echo "(https://github.com/espressif/qemu/releases)." >&2
  exit 1
fi
if ! "$QEMU" -machine help 2>/dev/null | grep -q '^esp32s3 '; then
  echo "$QEMU has no esp32s3 machine — that is mainline QEMU, not Espressif's." >&2
  echo "Set QEMU_XTENSA to an espressif/qemu build." >&2
  exit 1
fi

# ── The image ────────────────────────────────────────────────────────────
# ESPHOME/CASTLE_PY let a worktree borrow the main checkout's venv, the same
# escape hatch CLAUDE.md documents for the studio's children.
ESPHOME="${ESPHOME:-$REPO/.venv/bin/esphome}"
[ -x "$ESPHOME" ] || ESPHOME="esphome"
if [ "$DO_BUILD" = 1 ]; then
  echo "==> compiling $YAML"
  "$ESPHOME" compile "$YAML"
fi

# ESPHome writes here (firmware/build_path.yaml). Ask the config rather than
# hard-coding the drive, so a commented-out build_path still works.
BUILD="$("$ESPHOME" config "$YAML" 2>/dev/null \
  | sed -n 's/^  build_path: //p' | head -1)"
[ -n "$BUILD" ] || BUILD="$REPO/firmware/.esphome/build/$NAME"
FACTORY="$BUILD/build/firmware.factory.bin"
ELF="$BUILD/build/firmware.elf"
if [ ! -f "$FACTORY" ]; then
  echo "no image at $FACTORY — run without --no-build" >&2
  exit 1
fi

# QEMU wants a file the size of the whole flash, not an offset-merged image.
# The factory bin already starts at 0x0 (bootloader, partition table, otadata,
# app), so this is a pad to 8 MB with erased flash — the size castle_s3.yaml
# declares for the WROOM-1-N8R2.
OUT="${TMPDIR:-/tmp}/castle-s3-qemu"
mkdir -p "$OUT"
FLASH="$OUT/flash8m.bin"
PY="${CASTLE_PY:-$REPO/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
"$PY" - "$FACTORY" "$FLASH" <<'EOF'
import sys
src, dst = sys.argv[1], sys.argv[2]
SIZE = 8 * 1024 * 1024
data = open(src, 'rb').read()
if len(data) > SIZE:
    raise SystemExit(f"image is {len(data)} bytes, larger than the {SIZE}-byte flash")
buf = bytearray(b'\xff' * SIZE)
buf[:len(data)] = data
open(dst, 'wb').write(buf)
print(f"==> flash image: {dst} ({len(data)} bytes of image, padded to {SIZE})")
EOF

# ── The run ──────────────────────────────────────────────────────────────
# NOT passed, and each for a reason: no -nic (the S3 machine offers only
# open_eth, which this firmware has no driver for), no -device ssi_psram (the
# machine already instantiates one at CS 0; the guest still reports the chip
# as absent and carries on, which CONFIG_SPIRAM_IGNORE_NOTFOUND allows), no
# SD drive (the card is on GPSPI, which is not emulated — see docs/QEMU.md).
LOG="$OUT/console.log"
rm -f "$LOG"
QEMU_ARGS=(
  -nographic
  -machine esp32s3
  -drive "file=$FLASH,if=mtd,format=raw"
  -serial "file:$LOG"
  -monitor none
  -display none
)
[ "$DO_GDB" = 1 ] && QEMU_ARGS+=(-gdb tcp::3333)

echo "==> booting for ${SECONDS_TO_RUN}s (console -> $LOG)"
"$QEMU" "${QEMU_ARGS[@]}" &
QEMU_PID=$!
# shellcheck disable=SC2064
trap "kill $QEMU_PID 2>/dev/null || true" EXIT
sleep "$SECONDS_TO_RUN"

if [ "$DO_GDB" = 1 ]; then
  GDB="${XTENSA_GDB:-$HOME/.platformio/packages/tool-xtensa-esp-elf-gdb/bin/xtensa-esp32s3-elf-gdb}"
  if [ -x "$GDB" ]; then
    echo "==> where each core is now"
    "$GDB" -batch \
      -ex 'set pagination off' -ex 'set confirm off' \
      -ex 'target remote :3333' -ex 'info threads' \
      -ex 'thread apply all bt 20' -ex detach "$ELF" 2>&1 | sed 's/^/    /'
  else
    echo "==> --gdb asked for, but no gdb at $GDB (set XTENSA_GDB)" >&2
  fi
fi

kill "$QEMU_PID" 2>/dev/null || true
wait "$QEMU_PID" 2>/dev/null || true
trap - EXIT

echo "==> console ($(wc -c < "$LOG" | tr -d ' ') bytes)"
cat "$LOG"

# The one line that says the firmware itself, not just ESP-IDF, is alive.
if grep -q 'Running through setup()' "$LOG"; then
  echo
  echo "==> reached ESPHome setup(). See docs/QEMU.md for what that is worth."
else
  echo
  echo "==> did NOT reach ESPHome setup() — read the log above before trusting" >&2
  echo "    anything else; this is the case docs/QEMU.md does not cover." >&2
  exit 1
fi
