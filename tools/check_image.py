#!/usr/bin/env python3
"""Guard the OTA slot budget.

    tools/check_image.py [castle-sd|castle-s3] [--require]

OTA is the only way onto this board, so an image that outgrows its slot is
not a build error — it is a device nobody can reach. ESPHome does fail the
build when the binary literally will not fit, but that is a cliff: the build
before it passes at 99% and tells you nothing.

This warns on the approach. Run it after a build; it is wired into `make
check` (a missing image is "nothing to check" there) and into the weekly CI
compile with --require (there a missing image is a failure, because the
compile just ran — grade report 2026-09-06 D1).

The slot is READ, not typed: two builds, two flash sizes (the S2's 4 MB
gives 1.75 MB slots, the S3 carrier's 8 MB gives 3.75 MB), and ESPHome
writes the table it used as partitions.csv beside the build (grade report
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


def find_image(name: str) -> Path | None:
    """The built image, wherever build_path.yaml is pointing today.

    ESPHome names the app binary after the device (`castle.bin`), not
    `firmware.bin` — that one is the factory/OTA wrapper. Checking both means
    this keeps working if that ever changes.
    """
    for base in (
        Path("/Volumes/512Flash/esphome-builds/halloween_esp"),
        ROOT / "firmware" / ".esphome" / "build",
    ):
        build = base / name / "build"
        for candidate in (build / f"{name}.bin", build / "firmware.bin"):
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
    require = "--require" in sys.argv[1:]
    name = args[0] if args else "castle-sd"
    img = find_image(name)
    if img is None:
        print(f"no built image for {name!r} — run `make build` first")
        return 1 if require else 0  # nothing to check is not a failure, locally

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
        print("    - the S3 carrier's 8 MB flash gives 3.75 MB slots (castle_s3.yaml)")
        return 1
    if used >= WARN_AT:
        print(f"\nWARNING — over {WARN_AT * 100:.0f}%. Still flashable, but the next")
        print("  feature or scene may not be. Plan the audio move to SD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
