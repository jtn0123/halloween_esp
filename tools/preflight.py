#!/usr/bin/env python3
"""What `make check` needs that pip and cargo do not install, named before
it is needed, one line each.

    python tools/preflight.py [--warn]

On a clean Linux box `make setup` and then `make check` ended in 19 failures
and an error, and not one of them named its cause: a missing `lame` read as a
render failure, a missing `web/node_modules` as a tsc crash, a missing
`yt-dlp` as a failed assertion (grade report 2026-09-24 I1). This lists every
gap at once, each with the command that closes it on this platform.

Exit 1 when a REQUIRED tool is missing (`make check` runs this first, so the
run stops on the machine rather than on the code); `--warn` prints the same
lines and exits 0 (`make setup` ends with it, because the setup itself
worked and only the machine is short). The OPTIONAL rows never fail anything:
without cargo, `make check` is still green and the Rust gates say they were
skipped; without ccache, a firmware build is slower.

Stdlib-only, like tools/run_checks.py, so it can speak before the venv does.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import exe_paths

ROOT = Path(__file__).resolve().parent.parent

#: CI's node major (`.nvmrc`, and web/package.json's `engines`).
NODE_MIN = int((ROOT / ".nvmrc").read_text(encoding="utf-8").strip())


@dataclass(frozen=True)
class Row:
    name: str
    #: What fails without it — the part of the error the old run never said.
    why: str
    #: None when it is there; otherwise what is wrong, in a few words.
    probe: Callable[[], str | None]
    #: The one-line fix, by `sys.platform`; "" is every other platform.
    fix: dict[str, str]
    required: bool = True

    def fix_for(self, platform: str) -> str:
        return self.fix.get(platform) or self.fix[""]


def on_path(name: str) -> Callable[[], str | None]:
    return lambda: None if shutil.which(name) else "not on PATH"


def node_probe() -> str | None:
    node = shutil.which("node")
    if node is None:
        return "not on PATH"
    out = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    try:
        major = int(out.lstrip("v").split(".")[0])
    except ValueError:
        return f"answered {out!r} to --version"
    return None if major >= NODE_MIN else f"{out} is older than {NODE_MIN}"


def node_modules_probe() -> str | None:
    tsc = ROOT / "web" / "node_modules" / ".bin" / "tsc"
    return None if tsc.exists() else "web/node_modules is not installed"


def ytdlp_probe() -> str | None:
    return None if exe_paths.ytdlp() else "not in the venv or on PATH"


ROWS = (
    Row(
        "lame",
        "`make audio` encodes every scene through it",
        on_path("lame"),
        {"darwin": "brew install lame", "": "sudo apt-get install lame"},
    ),
    Row(
        "ffmpeg",
        "the analysis and import suites decode through it",
        on_path("ffmpeg"),
        {"darwin": "brew install ffmpeg", "": "sudo apt-get install ffmpeg"},
    ),
    Row(
        "node",
        f"tsc, web's suites and Castle Radio's browser half run on node {NODE_MIN}+",
        node_probe,
        {
            "darwin": f"brew install node@{NODE_MIN}",
            "": f"nvm install (reads .nvmrc), or node {NODE_MIN} from nodejs.org",
        },
    ),
    Row(
        "web/node_modules",
        "tsc and the esbuild bundle the generator tests build come from it",
        node_modules_probe,
        {"": "cd web && npm ci --ignore-scripts"},
    ),
    Row(
        "yt-dlp",
        "the importer fetches through it, and test_import asserts it is there",
        ytdlp_probe,
        {"": "make setup (it is pinned in requirements.lock)"},
    ),
    Row(
        "cargo",
        "castle-core cannot build, so `make audio`, the importer and the Rust "
        "gates will not run",
        on_path("cargo"),
        {"": "install rustup: https://rustup.rs"},
        required=False,
    ),
    Row(
        "ccache",
        "ESPHome compiles through it when it is on PATH, so a cold firmware "
        "build after the first is mostly cache hits",
        on_path("ccache"),
        {"darwin": "brew install ccache", "": "sudo apt-get install ccache"},
        required=False,
    ),
)


def report(rows: tuple[Row, ...], platform: str) -> tuple[list[str], int]:
    """The lines to print and how many REQUIRED rows failed."""
    lines: list[str] = []
    short = 0
    for row in rows:
        wrong = row.probe()
        if wrong is None:
            continue
        short += row.required
        kind = "missing" if row.required else "note: optional"
        lines.append(
            f"{kind}: {row.name} ({wrong}) — {row.why}. Fix: {row.fix_for(platform)}"
        )
    return lines, short


def main(argv: list[str] | None = None, rows: tuple[Row, ...] = ROWS) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    warn = "--warn" in args
    lines, short = report(rows, sys.platform)
    for line in lines:
        print(f"preflight: {line}", file=sys.stderr)
    if short and not warn:
        print(
            f"preflight: {short} required tool(s) missing — `make check` would "
            "fail on this machine, not on the code",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
