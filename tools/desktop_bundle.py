#!/usr/bin/env python3
"""The desktop app's bundled `castle/` folder — staged by the release build,
read by the app.

    desktop_bundle.py stage TAG TARGET CORE_ZIP_DIR OUT    writes OUT/castle/

The app carries no Python, no PyTorch, no ffmpeg and no Demucs model: those
are fetched on the buyer's own machine on the app's first launch, by the
same installer option A runs (tools/desktop_install.py), so none of them is
redistributed (docs/LICENSING.md, open decisions 4-6). What it carries is
what that first launch needs:

  castle/app/          this tree's files — the installer's own file list
                       (desktop_tree.source_files), the --source it runs from
  castle/bin/          castle-core: studio, analyze_track, scene_render and
                       their notices — the release's own castle-core zip
  castle/uv/uv[.exe]   Astral's uv, pinned below by version and sha256: it
                       installs Python 3.13 and the locked packages
  castle/bundle.json   what this bundle is, and the stamp the app compares
                       with the runtime it last set up (a new stamp, after
                       an update, re-runs the installer over it)

desktop/src-tauri/src/bundle.rs reads the same names, and
tests/test_desktop_bundle.py holds the two equal and stages a bundle the way
release.yml does. Bumping uv: the GitHub API reports each release asset's
`digest`; download the two archives once, check them, then change the pin.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_release as rel
import desktop_tree as dt
import release_assets as ra
from desktop_install import VERSION_FILE
from desktop_thirdparty import Pin

ROOT = Path(__file__).resolve().parent.parent
CASTLE = "castle"
APP, BIN, UV, ABOUT = "app", "bin", "uv", "bundle.json"
#: bundle.json's layout; the app treats any other number as a damaged bundle.
SCHEMA = 1
UV_VERSION = "0.12.22"
_UV = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
#: Per desktop target (release_assets.DESKTOP_TARGETS): the archive, its
#: sha256, and uv's path inside it.
UV_PINS: dict[str, Pin] = {
    "aarch64-apple-darwin": Pin(
        f"{_UV}/uv-aarch64-apple-darwin.tar.gz",
        "5d714de09501a59393ceca78f4bc232a50478729640d251907160299b2a93ddd",
        ("uv-aarch64-apple-darwin/uv",),
    ),
    "x86_64-pc-windows-msvc": Pin(
        f"{_UV}/uv-x86_64-pc-windows-msvc.zip",
        "ea1397797a0ca15f63516dd0f49c2dde9776db9be5861cab152ebe8ad199894d",
        ("uv.exe",),
    ),
}


def exe(target: str, name: str) -> str:
    return f"{name}.exe" if "windows" in target else name


def _member(archive: Path, name: str) -> bytes:
    """One named member's bytes — never a path taken from the archive."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            return zf.read(name)
    with tarfile.open(archive) as tf:
        fh = tf.extractfile(name)
        if fh is None:
            raise rel.ReleaseError(f"{archive.name}: {name} is not a file")
        return fh.read()


def fetch_uv(target: str, dest: Path, fetch: rel.Fetch, scratch: Path) -> Path:
    """The pinned uv for `target`, verified, as dest/uv[.exe]."""
    pin = UV_PINS[target]
    archive = rel.download(
        pin.url, scratch / pin.url.rsplit("/", 1)[1], fetch, pin.sha256
    )
    try:
        body = _member(archive, pin.members[0])
    except KeyError as exc:
        raise rel.ReleaseError(f"{pin.url} has no {pin.members[0]}") from exc
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / exe(target, "uv")
    out.write_bytes(body)
    out.chmod(0o755)
    return out


def stamp(castle: Path) -> str:
    """One digest over every file the bundle carries (its path and bytes):
    equal stamps are the same bundle, so the app knows when to set up again."""
    h = hashlib.sha256()
    for top in (APP, BIN, UV):
        for f in sorted(p for p in (castle / top).rglob("*") if p.is_file()):
            h.update(f.relative_to(castle).as_posix().encode("utf-8") + b"\0")
            h.update(rel.sha256(f).encode("ascii") + b"\n")
    return h.hexdigest()


def stage(
    tag: str,
    target: str,
    core_zips: Path,
    out: Path,
    repo: Path = ROOT,
    fetch: rel.Fetch = rel.http_fetch,
) -> Path:
    """Write out/castle/ for one desktop target; returns that folder."""
    ra.check_tag(tag)
    if target not in ra.DESKTOP_TARGETS or target not in UV_PINS:
        raise SystemExit(
            f"{target!r} is not a desktop target: {', '.join(ra.DESKTOP_TARGETS)}"
        )
    castle = out / CASTLE
    shutil.rmtree(castle, ignore_errors=True)
    castle.mkdir(parents=True)
    dt.copy_files(repo, dt.source_files(repo), castle / APP)
    # What `git archive`'s export-subst writes into a release zip's copy, so
    # the bundled tree names its release the way an option-A one does.
    version = castle / APP / VERSION_FILE
    if version.is_file():
        version.write_text(f"{tag}\n", encoding="utf-8")
    core = core_zips / ra.core_zip_name(target, tag)
    if not core.is_file():
        raise SystemExit(f"no castle-core zip for {target} {tag}: {core}")
    files = {p.name for p in rel.safe_extract(core, castle / BIN)}
    missing = [b for b in ra.CORE_BINS if exe(target, b) not in files]
    if missing:
        raise SystemExit(f"{core.name} lacks {', '.join(missing)}")
    with tempfile.TemporaryDirectory(prefix="castle-uv-") as scratch:
        fetch_uv(target, castle / UV, fetch, Path(scratch))
    about = {
        "schema": SCHEMA,
        "tag": tag,
        "target": target,
        "uv": UV_VERSION,
        "stamp": stamp(castle),
    }
    (castle / ABOUT).write_text(json.dumps(about, indent=2) + "\n", encoding="utf-8")
    return castle


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5 or args[0] != "stage":
        print(__doc__, file=sys.stderr)
        return 2
    try:
        castle = stage(args[1], args[2], Path(args[3]), Path(args[4]))
    except rel.ReleaseError as exc:
        print(f"desktop_bundle: {exc}", file=sys.stderr)
        return 1
    about = json.loads((castle / ABOUT).read_text(encoding="utf-8"))
    count = sum(1 for p in (castle / APP).rglob("*") if p.is_file())
    print(f"{castle}: {count} app files, castle-core, uv {about['uv']}")
    print(f"stamp {about['stamp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
