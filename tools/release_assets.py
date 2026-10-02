#!/usr/bin/env python3
"""Name, stage and checksum a GitHub Release's assets — the release contract.

    release_assets.py check-tag TAG            prints "true" for a pre-release
    release_assets.py stage-firmware TAG OTA_BIN OUT
    release_assets.py zip-core TAG TARGET BIN_DIR OUT
    release_assets.py stage-desktop TAG TARGET BUNDLE_DIR OUT
    release_assets.py finish TAG OUT [--desktop OWNER/REPO]

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

and, once desktop/ exists and the release builds the Tauri app (`finish
--desktop`), per DESKTOP_TARGETS:

  castle-tools-<rust-target>-<tag>.dmg          macOS installer
  castle-tools-<rust-target>-<tag>.app.tar.gz   macOS updater bundle (+ .sig)
  castle-tools-<rust-target>-<tag>-setup.exe    Windows NSIS per-user
                                                installer = updater bundle
                                                (+ .sig)
  latest.json                                   the Tauri updater's pointer;
                                                uploaded LAST, because it
                                                names every bundle above

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
from collections.abc import Callable
from datetime import UTC, datetime
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
LATEST = "latest.json"
#: Rust target -> the Tauri updater's platform key in latest.json.
DESKTOP_TARGETS = {
    "aarch64-apple-darwin": "darwin-aarch64",
    "x86_64-pc-windows-msvc": "windows-x86_64",
}

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


def desktop_names(target: str, tag: str) -> dict[str, str]:
    """`updater` is the bundle latest.json points at (its signature is
    `updater` + ".sig"); `installer` is what a first-time owner downloads.
    On Windows they are the same NSIS file, as Tauri 2 signs the installer
    itself."""
    stem = f"castle-tools-{target}-{tag}"
    if "windows" in target:
        return {"installer": f"{stem}-setup.exe", "updater": f"{stem}-setup.exe"}
    return {"installer": f"{stem}.dmg", "updater": f"{stem}.app.tar.gz"}


def expected_assets(tag: str, desktop: bool = False) -> list[str]:
    """Every asset a complete release carries, SHA256SUMS last (and, with
    the desktop app, latest.json after it — the upload order)."""
    names = [factory_name(tag), ota_name(tag)]
    names += [core_zip_name(t, tag) for t in CORE_TARGETS]
    if not desktop:
        return [*names, MANIFEST, SUMS]
    for t in DESKTOP_TARGETS:
        d = desktop_names(t, tag)
        names += sorted({d["installer"], d["updater"], d["updater"] + ".sig"})
    return [*names, MANIFEST, SUMS, LATEST]


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


def _one(bundle: Path, pattern: str) -> Path:
    found = sorted(bundle.glob(pattern))
    if len(found) != 1:
        raise SystemExit(f"expected one {pattern} under {bundle}, found {found}")
    return found[0]


def stage_desktop(tag: str, target: str, bundle: Path, out: Path) -> list[Path]:
    """Copy one target's Tauri bundles (`target/<target>/release/bundle`)
    under their release names. A build without an updater signature is not
    a release: installed apps could never take the next one."""
    if target not in DESKTOP_TARGETS:
        raise SystemExit(
            f"{target!r} is not a desktop target: {', '.join(DESKTOP_TARGETS)}"
        )
    names = desktop_names(target, tag)
    if "windows" in target:
        srcs = {"installer": _one(bundle, "nsis/*-setup.exe")}
        srcs["updater"] = srcs["installer"]
    else:
        srcs = {
            "installer": _one(bundle, "dmg/*.dmg"),
            "updater": _one(bundle, "macos/*.app.tar.gz"),
        }
    sig = srcs["updater"].with_name(srcs["updater"].name + ".sig")
    if not sig.is_file():
        raise SystemExit(
            f"missing updater signature: {sig} (is TAURI_SIGNING_PRIVATE_KEY set?)"
        )
    out.mkdir(parents=True, exist_ok=True)
    pairs = {names["installer"]: srcs["installer"], names["updater"]: srcs["updater"]}
    pairs[names["updater"] + ".sig"] = sig
    for name, src in pairs.items():
        shutil.copyfile(src, out / name)
    return [out / n for n in sorted(pairs)]


def latest_json(tag: str, out: Path, repo: str, pub_date: str) -> dict[str, object]:
    """The Tauri updater's static manifest. `version` is the tag without its
    `v` (the updater compares semver); each platform's `signature` is the
    .sig file's text, which is what the app verifies against the public key
    baked into it."""
    base = f"https://github.com/{repo}/releases/download/{tag}"
    platforms: dict[str, dict[str, str]] = {}
    for target, key in DESKTOP_TARGETS.items():
        updater = desktop_names(target, tag)["updater"]
        sig = out / (updater + ".sig")
        if not sig.is_file():
            raise SystemExit(f"missing updater signature: {sig}")
        platforms[key] = {
            "signature": sig.read_text(encoding="utf-8").strip(),
            "url": f"{base}/{updater}",
        }
    return {
        "version": tag.removeprefix("v"),
        "notes": f"Castle Tools {tag}",
        "pub_date": pub_date,
        "platforms": platforms,
    }


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


def finish(tag: str, out: Path, repo: str | None = None) -> list[str]:
    """Write the manifest, latest.json when REPO names the desktop app's
    home, and the sums; then insist the directory is exactly the contract:
    nothing missing, nothing extra."""
    check_tag(tag)
    (out / MANIFEST).write_text(
        json.dumps(flasher_manifest(tag), indent=2) + "\n", encoding="utf-8"
    )
    if repo is not None:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc = latest_json(tag, out, repo, now)
        (out / LATEST).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (out / SUMS).write_text(sha256sums(out), encoding="utf-8", newline="\n")
    want = expected_assets(tag, desktop=repo is not None)
    have = sorted(p.name for p in out.iterdir() if p.is_file())
    if sorted(want) != have:
        missing = sorted(set(want) - set(have))
        extra = sorted(set(have) - set(want))
        raise SystemExit(
            f"release assets do not match the contract: missing {missing}, extra {extra}"
        )
    return want


class Usage(Exception):
    """The command line is not one this script takes: print the usage."""


def _check_tag(tag: str) -> None:
    print("true" if check_tag(tag) else "false")


def _stage_firmware(tag: str, src: str, out: str) -> None:
    check_tag(tag)
    for p in stage_firmware(tag, Path(src), Path(out)):
        print(p)


def _zip_core(tag: str, target: str, src: str, out: str) -> None:
    check_tag(tag)
    print(zip_core(tag, target, Path(src), Path(out)))


def _stage_desktop(tag: str, target: str, src: str, out: str) -> None:
    check_tag(tag)
    for p in stage_desktop(tag, target, Path(src), Path(out)):
        print(p)


def _finish(tag: str, out: str, *desktop: str) -> None:
    repo = None
    if desktop:
        if len(desktop) != 2 or desktop[0] != "--desktop":
            raise Usage
        repo = desktop[1]
    print("\n".join(finish(tag, Path(out), repo)))


#: subcommand -> (handler, the argument counts it accepts)
COMMANDS: dict[str, tuple[Callable[..., None], tuple[int, ...]]] = {
    "check-tag": (_check_tag, (1,)),
    "stage-firmware": (_stage_firmware, (3,)),
    "zip-core": (_zip_core, (4,)),
    "stage-desktop": (_stage_desktop, (4,)),
    "finish": (_finish, (2, 4)),
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    handler, counts = COMMANDS.get(cmd, (None, ()))
    if handler is None or len(rest) not in counts:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        handler(*rest)
    except Usage:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
