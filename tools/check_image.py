#!/usr/bin/env python3
"""Guard the OTA slot budget.

    tools/check_image.py [castle]

Once OTA is the only way onto this board, an image that outgrows its slot is
not a build error — it is a device nobody can reach. ESPHome does fail the
build when the binary literally will not fit, but that is a cliff: the build
before it passes at 99% and tells you nothing.

This warns on the approach. Run it after a build; it is wired into `make
build`.

The margin matters more here than in most projects because the fallback is
physical access, and the whole point of the OTA work was to stop needing that.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Two 1.75 MB app slots is ESPHome's default layout on 4 MB flash.
SLOT = 1_835_008
WARN_AT = 0.90          # start complaining here
FAIL_AT = 0.97          # refuse to call this shippable


def find_image(name: str) -> Path | None:
    """The built image, wherever build_path.yaml is pointing today.

    ESPHome names the app binary after the device (`castle.bin`), not
    `firmware.bin` — that one is the factory/OTA wrapper. Checking both means
    this keeps working if that ever changes.
    """
    for base in (Path("/Volumes/512Flash/esphome-builds/halloween_esp"),
                 ROOT / "firmware" / ".esphome" / "build"):
        build = base / name / "build"
        for candidate in (build / f"{name}.bin", build / "firmware.bin"):
            if candidate.exists():
                return candidate
    return None


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "castle"
    img = find_image(name)
    if img is None:
        print(f"no built image for {name!r} — run `make build` first")
        return 0                      # nothing to check is not a failure

    size = img.stat().st_size
    used = size / SLOT
    spare = SLOT - size
    bar = "=" * int(used * 30)
    print(f"OTA slot  [{bar:<30}] {used*100:.1f}%")
    print(f"  image {size:,} B of {SLOT:,} B — {spare:,} B spare")

    # 40 kbps mono is 5 KB/s, the rate the scenes are rendered at.
    print(f"  headroom is worth ~{spare/5000:.0f}s more audio, or a feature")

    if used >= FAIL_AT:
        print(f"\nFAIL — over {FAIL_AT*100:.0f}% of the slot.")
        print("  An OTA that does not fit means physical access to recover,")
        print("  which is the thing OTA existed to avoid. Options:")
        print("    - move scenes to the SD card (firmware/castle_sd.yaml)")
        print("    - drop `bitrate` in scenes/scenes.yaml")
        print("    - shorten the longest looping scenes")
        return 1
    if used >= WARN_AT:
        print(f"\nWARNING — over {WARN_AT*100:.0f}%. Still flashable, but the next")
        print("  feature or scene may not be. Plan the audio move to SD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
