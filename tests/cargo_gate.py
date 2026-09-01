"""Cargo for the parity gates — run the way the toolchain pin requires.

rustup resolves `rust-toolchain.toml` by WORKING DIRECTORY, not by manifest.
A `cargo build --manifest-path core/Cargo.toml` typed from the repo root
therefore builds the crate with whatever rustc happens to be the machine's
default, silently ignoring `core/rust-toolchain.toml` — and the bit-exact
parity gates that compare Rust float output against numpy/scipy/C++ would
then be gating a compiler nobody chose. `Makefile:rust*` and
`tools/core_bins.py` already `cd core` for exactly this reason; the test
suites used to be the one place that didn't (grade report 2026-09-01 D1).

Every suite that shells out to cargo goes through here, so there is one
spelling of the invocation and one place the working directory is right.
`TestCargoIsPinned` in tests/test_castle_core.py asserts that the cargo
these helpers reach really does report the pinned version.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
TOOLCHAIN = CORE / "rust-toolchain.toml"

CARGO = shutil.which("cargo")


def pinned_channel() -> str:
    """The channel core/rust-toolchain.toml pins, e.g. "1.88.0"."""
    m = re.search(r'^channel\s*=\s*"([^"]+)"', TOOLCHAIN.read_text(), re.MULTILINE)
    assert m is not None, f"no [toolchain] channel in {TOOLCHAIN}"
    return m.group(1)


def cargo(
    *args: str, check: bool = False, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """`cargo <args>` from core/ — the pin's working directory."""
    assert CARGO is not None, "no cargo on PATH"
    return subprocess.run(
        [CARGO, *args],
        cwd=CORE,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def build(
    *extra: str, check: bool = False, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """The release build the dump binaries are read from.

    Returns the completed process by default rather than raising, because
    most callers want the captured stderr in their own failure message."""
    return cargo("build", "--release", "--quiet", *extra, check=check, timeout=timeout)
