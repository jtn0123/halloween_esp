#!/usr/bin/env python3
"""Guard the OTA slot budget — and be the one place that FINDS a built image.

    tools/check_image.py [castle-feather-s3|castle-s3] [--require] [--path]

Flash is the tight resource on the 4 MB Feather in the yard: v5.67 measured
67.9% of its 1,835,008-byte OTA slot with RAM at 35.1% — the card show gave
back 65 KB of flash and 25 KB of dram0 by taking twelve generated scene
scripts out of the image. OTA is also the only
way onto that board, so an image that outgrows its slot is not a build error —
it is a device nobody can reach. ESPHome does fail the build when the binary
literally will not fit, but that is a cliff: the build before it passes at 99%
and tells you nothing.

This warns on the approach. Run it after a build; it is wired into `make
check` (a missing image is "nothing to check" there) and into the weekly CI
compile with --require (there a missing image is a failure, because the
compile just ran — grade report 2026-09-06 D1).

`--path` prints the image and nothing else, which is how `make ota` names the
binary it just built. sd_sync.py used to glob for the newest
`firmware/.esphome/build/**/firmware.bin` instead, and firmware/build_path.yaml
has put every tree on an external volume since 2026-09-16 — so that glob found
either nothing or something stale, and "flash what `make build` produced" was
not what happened. One derivation, used by the guard and the flasher both.

The slot is READ, not typed: two builds, two flash sizes (the Feather's 4 MB
gives 1.75 MB slots, the S3 carrier's 8 MB gives 3.75 MB), and ESPHome writes
the table it used as partitions.csv beside the build (grade report
2026-09-06 J5).

The margin matters more here than in most projects because the fallback is
physical access, and the whole point of the OTA work was to stop needing that.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WARN_AT = 0.90  # start complaining here
FAIL_AT = 0.97  # refuse to call this shippable

#: The default target: the castle in the yard.
DEFAULT_NAME = "castle-feather-s3"


#: Where a build tree can be, best first. firmware/build_path.yaml points at
#: the external volume (a tree is ~300 MB of ESP-IDF objects) and keys it on
#: the CHECKOUT's directory name, so a worktree's image is looked for under
#: the worktree's name and not main's. CI rewrites that file to the second
#: path, which is also ESPHome's own default.
def _roots() -> tuple[Path, ...]:
    return (
        Path("/Volumes/512Flash/esphome-builds") / ROOT.name,
        ROOT / "firmware" / ".esphome" / "build",
    )


#: What the app image is called inside a build directory, best first. All
#: three are the same bytes today (verified 2026-09-17: `firmware.ota.bin` and
#: `castle-feather-s3.bin` compared equal), but `firmware.ota.bin` is the one
#: ESPHome hands its own OTA and the one that was successfully PUT to
#: /api/ota, so it leads — if a future platform ever makes them differ, the
#: flasher and this guard should both be looking at the flashable one.
#: `firmware.factory.bin` is deliberately NOT here: it carries the bootloader
#: and the partition table and is not what an OTA slot takes.
IMAGE_NAMES = ("firmware.ota.bin", "{name}.bin", "firmware.bin")


def find_image(name: str) -> Path | None:
    """The built image, wherever build_path.yaml is pointing today."""
    for base in _roots():
        build = base / name / "build"
        for pattern in IMAGE_NAMES:
            candidate = build / pattern.format(name=name)
            if candidate.exists():
                return candidate
    return None


def slot_size(img: Path) -> int:
    """app0's size from the partition table ESPHome wrote beside the build
    (<build_path>/partitions.csv, two levels up from the image). Loud when
    it is missing: a guessed slot is the number this guard exists to not
    guess."""
    csv = img.parent.parent / "partitions.csv"
    if not csv.exists():
        raise SystemExit(f"no partition table beside {img}: expected {csv}")
    for line in csv.read_text().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if cols and cols[0] == "app0":
            return int(cols[4], 0)
    raise SystemExit(f"{csv} has no app0 row")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = sys.argv[1:]
    require = "--require" in flags
    want_path = "--path" in flags
    name = args[0] if args else DEFAULT_NAME
    img = find_image(name)
    if img is None:
        # To stderr under --path: the caller is substituting stdout into
        # another command's arguments, and a sentence there would be flashed
        # to the castle.
        msg = f"no built image for {name!r} — run `make build` first"
        print(msg, file=sys.stderr if want_path else sys.stdout)
        # --path has no half-answer: there is no image to name, so it is
        # always a failure, --require or not.
        return 1 if require or want_path else 0
    if want_path:
        print(img)
        return 0

    slot = slot_size(img)
    size = img.stat().st_size
    used = size / slot
    spare = slot - size
    bar = "=" * int(used * 30)
    print(f"OTA slot  [{bar:<30}] {used * 100:.1f}%  ({name})")
    print(f"  image {size:,} B of {slot:,} B — {spare:,} B spare")

    if used >= FAIL_AT:
        print(f"\nFAIL — over {FAIL_AT * 100:.0f}% of the slot.")
        print("  An OTA that does not fit means physical access to recover,")
        print("  which is the thing OTA existed to avoid. Options:")
        print("    - trim a component (castle.yaml's sdkconfig notes list what is off)")
        print("    - the WROOM carrier's 8 MB flash gives 3.75 MB (castle_s3.yaml)")
        return 1
    if used >= WARN_AT:
        print(f"\nWARNING — over {WARN_AT * 100:.0f}%. Still flashable, but the next")
        print("  feature or scene may not be. Plan the audio move to SD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
