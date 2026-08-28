"""castle-core's tool binaries, built on demand — one home for the cargo
dance every crate-calling tool repeats (scene_render for render_audio,
analyze_track for the importer).

The crate is the production implementation; its fixed arithmetic is what
makes a render or an analysis the same bytes on every machine. So a
missing binary with no cargo to build it is a hard stop with a sentence,
never a silent fall-back to the machine-dependent Python reference.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@functools.cache
def core_bin(name: str) -> Path:
    """core/target/release/<name>, rebuilt when cargo is here to do it —
    once per process (the cache), not once per call."""
    exe = ROOT / "core" / "target" / "release" / name
    cargo = shutil.which("cargo")
    if cargo:
        subprocess.run(
            [
                cargo,
                "build",
                "--release",
                "--quiet",
                "--bin",
                name,
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            check=True,
        )
    if not exe.exists():
        raise SystemExit(
            f"core/target/release/{name} is missing and there is no cargo "
            "to build it — install rust, or build the binary elsewhere "
            "(cd core && cargo build --release)"
        )
    return exe
