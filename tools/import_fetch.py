"""Getting the audio in the first place: yt-dlp, and what counts as a link.

Split out of import_track.py at the seam between FETCHING a source and
CONVERTING one (the 500-line rule; the rest of that file is options, ffmpeg
and the manifest). Nothing here knows about tracks/ or scenes — it is handed
a link and a scratch directory, and hands back a file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

import exe_paths
import import_reason as ir
from import_convert import MAX_IMPORT_SECONDS, longest

#: What the owner reads when the link's video is over the limit, or a live
#: stream with no end — yt-dlp's match filter refused it before downloading.
TOO_LONG_LINK = (
    f"That video is longer than {longest()}, or is a live stream — choose a "
    "shorter one, or set a start and length to import just part of it."
)
DOWNLOAD_STALLED = (
    "The download took too long and was stopped — try the link again later, "
    "or try a different link."
)


def detail(lines: list[str]) -> None:
    """A tool's own words, for whoever helps: indented, so the verdict scan
    (import_reason) never mistakes one for the sentence the owner reads."""
    for ln in lines:
        print("    " + ln.rstrip(), file=sys.stderr)


def _ytdlp() -> str:
    """The yt-dlp to run (exe_paths.ytdlp says which, and why the managed
    copy beats the rest), or the owner's sentence saying there is none."""
    found = exe_paths.ytdlp()
    if found is None:
        detail(
            [
                "yt-dlp was not found: no managed copy (Update the downloader),",
                "no CASTLE_YTDLP, none beside this Python, none on PATH",
            ]
        )
        raise SystemExit(ir.DOWNLOADER_MISSING)
    return found


def is_web_url(source: str) -> bool:
    """Is this a link we would hand to yt-dlp?

    `"://" in source` was the old test, and it let through anything with
    those three characters — including a value that STARTS WITH A DASH, which
    yt-dlp then reads as one of its own options rather than as a link
    (`--config-location=http://…` is the sharp end of that). The studio passes
    this string straight from the browser, so the check is a real one: a
    parseable http(s) URL with a host, and nothing else.
    """
    try:
        u = urllib.parse.urlsplit(source)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.netloc)


def _argv(url: str, dest: Path, whole: bool, stream: bool) -> list[str]:
    """yt-dlp's arguments. `whole`: the import keeps the entire video, so
    one over the length limit — or a live stream, which has no end — is
    refused by yt-dlp before a byte is downloaded (`<=?` lets a site that
    reports no duration through; the converted file is measured anyway)."""
    limit = f"!is_live & duration <=? {MAX_IMPORT_SECONDS}" if whole else "!is_live"
    return [
        _ytdlp(),
        *(["--newline"] if stream else []),
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--no-playlist",
        "--match-filters",
        limit,
        "-o",
        str(dest / "%(title)s.%(ext)s"),
        # `--` closes the option list: whatever the URL turns out to look
        # like, yt-dlp reads it as the thing to download.
        "--",
        url,
    ]


def fetch_url(url: str, dest: Path, whole: bool = True) -> tuple[Path, str]:
    """Download audio only. Returns (file, title as the source named it)."""
    if not is_web_url(url):
        detail([f"not an http(s) link: {url!r}"])
        raise SystemExit(ir.NOT_A_LINK)
    # Bracketed like yt-dlp's own chatter: a progress line, never a verdict.
    print(f"[fetch] {url}")
    stream = os.environ.get("CASTLE_PROGRESS_STREAM") == "1"
    try:
        if stream:
            from progress_process import run_progress

            r = run_progress(_argv(url, dest, whole, stream), 900)
        else:
            r = subprocess.run(
                _argv(url, dest, whole, stream),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,  # handled below
                timeout=900,  # a hung download must not wedge the studio's lock
            )
    except subprocess.TimeoutExpired:
        raise SystemExit(DOWNLOAD_STALLED) from None
    said = f"{r.stdout or ''}\n{r.stderr or ''}"
    if r.returncode != 0:
        # yt-dlp's last lines say WHY (a private video, a bot check); the
        # owner reads what that means, the lines go to the log beneath it.
        # check=True here once dumped a raw CalledProcessError traceback
        # into the studio's red banner (round-3 user test).
        detail([ln for ln in said.splitlines() if ln.strip()][-6:])
        raise SystemExit(ir.recognised(said) or ir.DOWNLOAD_FAILED)
    got = sorted(dest.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not got:
        if "does not pass filter" in said:
            raise SystemExit(TOO_LONG_LINK)
        detail([ln for ln in said.splitlines() if ln.strip()][-6:])
        raise SystemExit(ir.NO_AUDIO)
    # The title is the file's name, and a Mac may hand back a decomposed
    # one (é as e + U+0301): the same song must read the same everywhere.
    return got[-1], unicodedata.normalize("NFC", got[-1].stem)
