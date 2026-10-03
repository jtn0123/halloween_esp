"""Where the programs this repo runs live — on macOS and on Windows.

Three kinds of answer, one module:

- `exe(name)`: a binary's file name on this system (`analyze_track` here,
  `analyze_track.exe` on Windows) — for paths the repo builds itself, like
  core/target/release/<bin>.
- `venv_python(venv)` / `venv_script(venv, name)`: a virtualenv lays out
  `bin/python` on POSIX and `Scripts\\python.exe` on Windows; `npm_bin`
  is node_modules/.bin's equivalent (`esbuild` / `esbuild.cmd`).
- `ffmpeg()`, `ffprobe()`, `ytdlp()`: the media tools, which a desktop
  install bundles beside itself rather than on PATH. `CASTLE_FFMPEG` and
  `CASTLE_YTDLP` name the bundled binaries; ffprobe is looked for beside
  `CASTLE_FFMPEG` (they ship as a pair). Unset, ffmpeg and ffprobe stay the
  bare names — subprocess finds them on PATH, adding `.exe` itself on
  Windows — and yt-dlp prefers this interpreter's own copy. One copy of
  yt-dlp beats even `CASTLE_YTDLP`: the managed one the owner updated
  (`downloader_dir`, tools/ytdlp_update.py).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
ROOT = Path(__file__).resolve().parent.parent
#: Castle Radio's data dir in a checkout (radio_paths.py), when
#: CASTLE_RADIO_DATA does not name another.
RADIO_DATA = ROOT / "demo" / "castle-radio" / ".radio-data"


def exe(name: str) -> str:
    """`name` as an executable's file name on this system."""
    return f"{name}.exe" if WINDOWS and not name.endswith(".exe") else name


def venv_script(venv: Path, name: str) -> Path:
    """A console script (pip, yt-dlp, python) inside a virtualenv."""
    return venv / ("Scripts" if WINDOWS else "bin") / exe(name)


def venv_python(venv: Path) -> Path:
    """The interpreter of the virtualenv at `venv`."""
    return venv_script(venv, "python")


def npm_bin(bin_dir: Path, name: str) -> Path:
    """A tool npm linked into node_modules/.bin. On Windows that is a
    `.cmd` shim beside a POSIX shell script CreateProcess cannot run."""
    return bin_dir / (f"{name}.cmd" if WINDOWS else name)


def _env_path(var: str) -> str | None:
    value = os.environ.get(var, "").strip()
    return value or None


def ffmpeg() -> str:
    """CASTLE_FFMPEG when set, else `ffmpeg` from PATH."""
    return _env_path("CASTLE_FFMPEG") or "ffmpeg"


def ffprobe() -> str:
    """The ffprobe beside CASTLE_FFMPEG when that names one, else PATH's."""
    if bundled := _env_path("CASTLE_FFMPEG"):
        path = Path(bundled)
        sibling = path.with_name(path.name.replace("ffmpeg", "ffprobe", 1))
        if sibling != path and sibling.exists():
            return str(sibling)
    return "ffprobe"


def downloader_dir() -> Path | None:
    """Where the managed yt-dlp lives — the copy "Update the downloader"
    fetches and replaces (tools/ytdlp_update.py). `CASTLE_DOWNLOADER_DIR`
    when set (the installer's launcher names its own bin/), set-but-empty
    meaning there is none (the test suites); else `downloader/` in Castle
    Radio's data dir, which is per-user app data in both desktop builds —
    so the radio, the studio and the importer they spawn find one copy."""
    named = os.environ.get("CASTLE_DOWNLOADER_DIR")
    if named is not None:
        return Path(named) if named.strip() else None
    data = _env_path("CASTLE_RADIO_DATA")
    return (Path(data).expanduser() if data else RADIO_DATA) / "downloader"


def managed_ytdlp() -> str | None:
    """The managed yt-dlp, when one has been fetched."""
    home = downloader_dir()
    found = home / exe("yt-dlp") if home else None
    return str(found) if found and found.is_file() else None


def ytdlp() -> str | None:
    """The managed copy, else CASTLE_YTDLP, else this interpreter's own
    yt-dlp, else PATH's — None when there is none.

    YouTube deliberately breaks stale clients (403s, SABR-only sessions), and
    Homebrew's formula trails releases by weeks. The managed copy is the one
    the owner can update from Castle Radio, so once it exists it wins over a
    bundled copy that can never change (a signed app's) and everything else.
    Without one, the venv copy, updated with `pip install -U yt-dlp`, wins
    over PATH. On Windows the interpreter lives in `Scripts\\` beside it, as
    `yt-dlp.exe`.
    """
    if managed := managed_ytdlp():
        return managed
    if bundled := _env_path("CASTLE_YTDLP"):
        return bundled
    local = Path(sys.executable).with_name(exe("yt-dlp"))
    if local.exists():
        return str(local)
    return shutil.which("yt-dlp")


def which(command: str) -> str | None:
    """The full path `command` resolves to, or None: shutil.which, with the
    same `.exe` and PATHEXT rules subprocess uses."""
    if os.sep in command or (os.altsep and os.altsep in command):
        return command if Path(command).is_file() else None
    return shutil.which(command)
