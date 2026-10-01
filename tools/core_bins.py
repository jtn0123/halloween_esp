"""castle-core's tool binaries, built on demand — one home for the cargo
dance every crate-calling tool repeats (scene_render for render_audio,
analyze_track for the importer).

The crate is the production implementation; its fixed arithmetic is what
makes a render or an analysis the same bytes on every machine. So a
missing binary with no cargo to build it is a hard stop with a sentence,
never a silent fall-back to the machine-dependent Python reference.

The build is skipped when the binary is already newer than every source
it is built from (grade report 2026-08-31 B6). The studio spawns these children per
job, and cargo's own "is it fresh?" pass is tens of milliseconds of
process start and manifest parse on every one of them — cheap once,
noticeable per render. A stat() sweep over core/src answers the same
question without launching anything, and any drift falls through to the
real build.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

import exe_paths

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"


def _is_fresh(exe: Path) -> bool:
    """True when exe exists and predates nothing under core/src (or the
    manifest, the toolchain pin, the lock — a profile or edition change is
    a rebuild too). Conservative on purpose: any doubt returns False and
    cargo gets the last word."""
    try:
        built = exe.stat().st_mtime
    except OSError:
        return False
    inputs = [
        CORE / "Cargo.toml",
        CORE / "Cargo.lock",
        CORE / "rust-toolchain.toml",
        *(CORE / "src").rglob("*"),
    ]
    for src in inputs:
        try:
            if src.is_file() and src.stat().st_mtime > built:
                return False
        except OSError:
            return False
    return True


#: Where a packaged install keeps its prebuilt castle-core binaries — the
#: desktop app sets it to its sidecar folder. A named folder is the whole
#: answer: no cargo, no freshness sweep (an app bundle has no core/src).
BIN_DIR_ENV = "CASTLE_CORE_BIN_DIR"


def _from_bin_dir(name: str, folder: str) -> Path:
    exe = Path(folder) / exe_paths.exe(name)
    if not exe.is_file():
        raise SystemExit(
            f"{BIN_DIR_ENV} names {folder}, which has no {exe.name} — "
            "reinstall the desktop tools, or unset it to build from core/"
        )
    return exe


@functools.cache
def core_bin(name: str) -> Path:
    """core/target/release/<name>, rebuilt when it is stale and cargo is
    here to do it — once per process (the cache), not once per call. A
    packaged install names its own folder with CASTLE_CORE_BIN_DIR."""
    folder = os.environ.get(BIN_DIR_ENV)
    if folder:
        return _from_bin_dir(name, folder)
    exe = CORE / "target" / "release" / exe_paths.exe(name)
    cargo = shutil.which("cargo")
    if cargo and not _is_fresh(exe):
        # From core/, not with --manifest-path: rustup finds the toolchain pin
        # (core/rust-toolchain.toml) by working directory, and this build is
        # the one whose float codegen the "same bytes everywhere" claim rests
        # on — it is the last place to let the compiler float.
        subprocess.run(
            [cargo, "build", "--release", "--quiet", "--bin", name],
            cwd=CORE,
            check=True,
        )
    if not exe.exists():
        # Still the hard stop — with one hole, deliberately: a binary built
        # elsewhere (CI's dedicated rust job, a prebuilt image) satisfies the
        # freshness check above and never reaches here, so cargo is required
        # to BUILD the crate, not to run a tool that already has it.
        raise SystemExit(
            f"core/target/release/{name} is missing and there is no cargo "
            "to build it — install rust, or build the binary elsewhere "
            "(cd core && cargo build --release)"
        )
    return exe
