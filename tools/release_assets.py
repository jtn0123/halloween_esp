#!/usr/bin/env python3
"""Name, stage and checksum a GitHub Release's assets — the release contract.

    release_assets.py check-tag TAG            prints "true" for a pre-release
    release_assets.py stage-firmware TAG OTA_BIN OUT
    release_assets.py zip-core TAG TARGET BIN_DIR OUT
    release_assets.py finish TAG OUT

.github/workflows/release.yml calls every one of these; pages.yml relies on
what `finish` writes. The asset names are a CONTRACT other code is written
against (the desktop app's updater and firmware check, the web flasher, the
owner's guide), so they are spelled once, here, and nowhere else in the
repo's own tooling:

  castle-fw-feather-s3-4m2p-<tag>.factory.bin   full image, offset 0 — the
                                                web flasher writes this
  castle-fw-feather-s3-4m2p-<tag>.ota.bin       app image — PUT /api/ota
  castle-core-<rust-target>-<tag>.zip           analyze_track, scene_render,
                                                studio (+ .exe on Windows)
  flasher-manifest.json                         esp-web-tools manifest
  SHA256SUMS                                    sha256sum format, every
                                                other asset

`feather-s3-4m2p` is the board identifier the firmware reports as `board`
in /api/status (ESP32-S3 Feather #5477: 4 MB flash, 2 MB PSRAM), so the app
can match a castle to its image by string equality. Stdlib only: the publish
job runs it on a bare runner python.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

BOARD = "feather-s3-4m2p"
CORE_TARGETS = (
    "x86_64-pc-windows-msvc",
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
)
CORE_BINS = ("analyze_track", "scene_render", "studio")
MANIFEST = "flasher-manifest.json"
SUMS = "SHA256SUMS"

#: vMAJOR.MINOR.PATCH, optionally `-suffix` (which makes it a pre-release).
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z][0-9A-Za-z.-]*)?$")


def check_tag(tag: str) -> bool:
    """True when TAG is a pre-release. Refuses anything that is not a tag
    the release contract can name assets after."""
    m = TAG_RE.match(tag)
    if m is None:
        raise SystemExit(f"{tag!r} is not a release tag (vMAJOR.MINOR.PATCH[-suffix])")
    return m.group(4) is not None


def factory_name(tag: str) -> str:
    return f"castle-fw-{BOARD}-{tag}.factory.bin"


def ota_name(tag: str) -> str:
    return f"castle-fw-{BOARD}-{tag}.ota.bin"


def core_zip_name(target: str, tag: str) -> str:
    return f"castle-core-{target}-{tag}.zip"


def expected_assets(tag: str) -> list[str]:
    """Every asset a complete release carries, SHA256SUMS last."""
    names = [factory_name(tag), ota_name(tag)]
    names += [core_zip_name(t, tag) for t in CORE_TARGETS]
    return [*names, MANIFEST, SUMS]


def stage_firmware(tag: str, ota_bin: Path, out: Path) -> list[Path]:
    """Copy ESPHome's two images under their release names. OTA_BIN is what
    `tools/check_image.py --path` printed; ESPHome writes the factory image
    beside it, and a build without one is not a release."""
    factory = ota_bin.with_name("firmware.factory.bin")
    for src in (ota_bin, factory):
        if not src.is_file():
            raise SystemExit(f"missing firmware image: {src}")
    out.mkdir(parents=True, exist_ok=True)
    staged = [out / factory_name(tag), out / ota_name(tag)]
    shutil.copyfile(factory, staged[0])
    shutil.copyfile(ota_bin, staged[1])
    return staged


def zip_core(tag: str, target: str, bin_dir: Path, out: Path) -> Path:
    """The three castle-core binaries, flat, executable bit kept."""
    if target not in CORE_TARGETS:
        raise SystemExit(
            f"{target!r} is not a release target: {', '.join(CORE_TARGETS)}"
        )
    exe = ".exe" if "windows" in target else ""
    out.mkdir(parents=True, exist_ok=True)
    dest = out / core_zip_name(target, tag)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in CORE_BINS:
            src = bin_dir / f"{name}{exe}"
            if not src.is_file():
                raise SystemExit(f"missing castle-core binary: {src}")
            info = zipfile.ZipInfo(src.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o755 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, src.read_bytes())
    return dest


def flasher_manifest(tag: str) -> dict[str, object]:
    """esp-web-tools' manifest: one build, the factory image at offset 0
    (it carries the bootloader and partition table, so nothing else is
    written). `improv` lets the page hand the castle its Wi-Fi over the same
    USB cable; `new_install_prompt_erase` offers a clean flash, which is what
    a recovery wants."""
    return {
        "name": "Halloween Castle",
        "version": tag,
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": "ESP32-S3",
                "improv": True,
                "parts": [{"path": factory_name(tag), "offset": 0}],
            }
        ],
    }


def sha256sums(out: Path) -> str:
    """`sha256sum` text format over every file in OUT except the sums."""
    lines = []
    for p in sorted(out.iterdir()):
        if p.is_file() and p.name != SUMS:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.name}\n")
    return "".join(lines)


def finish(tag: str, out: Path) -> list[str]:
    """Write the manifest and the sums, then insist the directory is
    exactly the contract: nothing missing, nothing extra."""
    check_tag(tag)
    (out / MANIFEST).write_text(
        json.dumps(flasher_manifest(tag), indent=2) + "\n", encoding="utf-8"
    )
    (out / SUMS).write_text(sha256sums(out), encoding="utf-8", newline="\n")
    want = expected_assets(tag)
    have = sorted(p.name for p in out.iterdir() if p.is_file())
    if sorted(want) != have:
        missing = sorted(set(want) - set(have))
        extra = sorted(set(have) - set(want))
        raise SystemExit(
            f"release assets do not match the contract: missing {missing}, extra {extra}"
        )
    return want


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    if cmd == "check-tag" and len(rest) == 1:
        print("true" if check_tag(rest[0]) else "false")
    elif cmd == "stage-firmware" and len(rest) == 3:
        check_tag(rest[0])
        for p in stage_firmware(rest[0], Path(rest[1]), Path(rest[2])):
            print(p)
    elif cmd == "zip-core" and len(rest) == 4:
        check_tag(rest[0])
        print(zip_core(rest[0], rest[1], Path(rest[2]), Path(rest[3])))
    elif cmd == "finish" and len(rest) == 2:
        print("\n".join(finish(rest[0], Path(rest[1]))))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
