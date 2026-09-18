"""The emulated castle's command line — `tools/castle_emu.py [port]`.

castle_emu.py is the castle itself (state, mailbox, the 200 ms tick); this is
only the front door: the flags that pick a card directory, a show, an OTA
slot and the two rehearsal modes, plus the placeholder songs a temp card is
seeded with. Split off when the emulator passed the 500-line cap — the seam
is the one the firmware has too, between the device and the bench that
drives it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from castle_emu import CastleEmu
from castle_emu_http import OTA_SLOTS
from castle_emu_scenes import show_scene_ids


def seed(card: Path) -> None:
    """Two placeholder 'songs' so the desk has something to list and play."""
    for name, kb in (("wicked_winds.mp3", 280), ("ghostbusters.mp3", 960)):
        f = card / name
        if not f.exists():
            f.write_bytes(b"\xff\xfb" + b"\x00" * (kb * 1024 - 2))
    (card / "logs").mkdir(exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", type=int, default=8093)
    ap.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="directory that plays the SD card (default: temp, seeded)",
    )
    ap.add_argument(
        "--wedge",
        action="store_true",
        help="replay the pre-v5.22 wedge: stall requests while playing",
    )
    ap.add_argument("--no-sd", action="store_true", help="pretend the card is missing")
    ap.add_argument(
        "--board",
        choices=sorted(OTA_SLOTS),
        default="feather",
        help="whose OTA slot /api/ota measures against: the 4 MB Feather in "
        "the yard or the 8 MB WROOM carrier (default: feather)",
    )
    ap.add_argument(
        "--serial",
        action="store_true",
        help="one request at a time, like the device's single httpd task",
    )
    ap.add_argument(
        "--scenes",
        default=None,
        help="scene ids: a comma list, or a scenes.yaml "
        "(default: $CASTLE_SCENES, else scenes/scenes.yaml)",
    )
    args = ap.parse_args()
    scenes: list[str] | None = None
    if args.scenes:
        scenes = (
            show_scene_ids(Path(args.scenes))
            if args.scenes.endswith(".yaml")
            else [x.strip() for x in args.scenes.split(",") if x.strip()]
        )
    emu = CastleEmu(
        port=args.port,
        sd_dir=args.dir,
        wedge=args.wedge,
        sd_mounted=not args.no_sd,
        serial=args.serial,
        scenes=scenes,
        ota_slot=OTA_SLOTS[args.board],
    )
    if args.dir is None:
        seed(emu.sd_dir)
    print(
        f"castle emulator on http://127.0.0.1:{emu.port}  card={emu.sd_dir}"
        + ("  [WEDGE MODE]" if args.wedge else "")
        + ("  [SERIAL]" if args.serial else "")
    )
    print(f"  scenes: {', '.join(emu.scenes)}")
    print(f"  point the studio at it:  CASTLE_HOST=127.0.0.1:{emu.port}")
    emu.serve_forever()


if __name__ == "__main__":
    main()
