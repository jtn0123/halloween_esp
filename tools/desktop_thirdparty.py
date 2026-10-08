"""ffmpeg for the desktop installer: found, installed, or fetched.

ffmpeg, in order: one already on PATH; else the platform's package manager
(winget on Windows, Homebrew on macOS) when it is there; else a PINNED
static build whose sha256 is written below — a moving "latest" download
would make the same installer fetch different bytes on different days.

yt-dlp is not here: it is the managed song downloader, fetched, verified
and replaced by tools/ytdlp_update.py — the same code Castle Radio's Update
the downloader button runs — so the installer and the button keep one copy
in `<install>/bin` the one way.

Bumping a pin: the GitHub API reports each asset's `digest`; for
ffmpeg.martin-riedl.de the `.sha256` file sits beside the zip. Download it
once and check the digest locally before committing a new pin.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import desktop_release as rel


@dataclass(frozen=True)
class Pin:
    """One archive of a pinned static build and the members to take from it."""

    url: str
    sha256: str
    members: tuple[str, ...]


#: ffmpeg 9.0.2, static, per (system, machine). GPL builds; downloaded by
#: the user's own installer from their publishers, never redistributed here.
FFMPEG_PINS: dict[tuple[str, str], tuple[Pin, ...]] = {
    ("Windows", "x86_64"): (
        Pin(
            "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
            "ffmpeg-9.0.2-essentials_build.zip",
            "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba",
            ("ffmpeg.exe", "ffprobe.exe"),
        ),
    ),
    ("Darwin", "arm64"): (
        Pin(
            "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip",
            "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
            ("ffmpeg",),
        ),
        Pin(
            "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffprobe.zip",
            "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
            ("ffprobe",),
        ),
    ),
}

#: shutil.which, injectable.
Which = Callable[[str], str | None]


def machine_key(machine: str) -> str:
    m = machine.lower()
    if m in ("amd64", "x86_64"):
        return "x86_64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def ffmpeg_on_path(which: Which, exe: Callable[[str], str]) -> tuple[str, str] | None:
    """(ffmpeg, ffprobe) when BOTH are on PATH — the importer needs both."""
    ff, probe = which(exe("ffmpeg")), which(exe("ffprobe"))
    return (ff, probe) if ff and probe else None


def package_manager_command(system: str, which: Which) -> list[str] | None:
    """How this machine's package manager installs ffmpeg, if it has one."""
    if system == "Windows" and which("winget"):
        return [
            "winget", "install", "--exact", "--id", "Gyan.FFmpeg",
            "--silent", "--accept-package-agreements", "--accept-source-agreements",
        ]  # fmt: skip
    if system == "Darwin" and which("brew"):
        return ["brew", "install", "ffmpeg"]
    return None


def after_package_manager(system: str, environ_home: Path) -> list[Path]:
    """Where a just-installed ffmpeg lands that this process's PATH (read
    before the install) cannot see yet."""
    if system == "Windows":
        return [environ_home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links"]
    if system == "Darwin":
        return [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return []


def find_in(dirs: list[Path], name: str) -> str | None:
    for d in dirs:
        p = d / name
        if p.is_file():
            return str(p)
    return None


def fetch_pinned_ffmpeg(
    system: str, machine: str, bin_dir: Path, fetch: rel.Fetch, scratch: Path
) -> tuple[str, str]:
    """Download, verify and unpack the pinned build into `bin_dir`."""
    pins = FFMPEG_PINS.get((system, machine_key(machine)))
    if not pins:
        raise rel.ReleaseError(
            f"no pinned ffmpeg for {system}/{machine}: install ffmpeg yourself "
            "and re-run the installer"
        )
    bin_dir.mkdir(parents=True, exist_ok=True)
    for i, pin in enumerate(pins):
        archive = rel.download(pin.url, scratch / f"ffmpeg-{i}.zip", fetch, pin.sha256)
        unpacked = scratch / f"ffmpeg-{i}"
        files = rel.safe_extract(archive, unpacked)
        archive.unlink()  # the zip and its other members are ~100 MB of scratch
        by_name = {p.name: p for p in files}
        for member in pin.members:
            if member not in by_name:
                raise rel.ReleaseError(f"{pin.url} has no {member}")
            out = bin_dir / member
            shutil.move(by_name[member], out)
            out.chmod(0o755)
        shutil.rmtree(unpacked, ignore_errors=True)
    ext = ".exe" if system == "Windows" else ""
    return str(bin_dir / f"ffmpeg{ext}"), str(bin_dir / f"ffprobe{ext}")
