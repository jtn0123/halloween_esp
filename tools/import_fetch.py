"""Getting the audio in the first place: yt-dlp, and what counts as a link.

Split out of import_track.py at the seam between FETCHING a source and
CONVERTING one (the 500-line rule; the rest of that file is options, ffmpeg
and the manifest). Nothing here knows about tracks/ or scenes — it is handed
a link and a scratch directory, and hands back a file.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path


def _ytdlp() -> str:
    """The venv's yt-dlp when present, else the system one.

    YouTube deliberately breaks stale clients (403s, SABR-only sessions), and
    Homebrew's formula trails releases by weeks — pip does not. So the venv
    copy, updated with `pip install -U yt-dlp`, wins when it exists.
    """
    local = Path(sys.executable).with_name("yt-dlp")
    if local.exists():
        return str(local)
    if not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp not installed — `brew install yt-dlp`")
    return "yt-dlp"


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


def fetch_url(url: str, dest: Path) -> tuple[Path, str]:
    """Download audio only. Returns (file, title as the source named it)."""
    if not is_web_url(url):
        raise SystemExit(f"not a link this can fetch: {url!r} — http(s) only")
    print(f"fetching {url}")
    try:
        r = subprocess.run(
            # `--` closes the option list: whatever the URL turns out to look
            # like, yt-dlp reads it as the thing to download.
            [_ytdlp(), "-x", "--audio-format", "mp3", "--audio-quality", "0",
             "--no-playlist", "-o", str(dest / "%(title)s.%(ext)s"), "--", url],
            capture_output=True, text=True, check=False,  # handled below
            timeout=900,   # a hung download must not wedge the studio's lock
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("gave up after 15 minutes — the download stalled. "
                         "Try the link again, or a different source.") from None
    if r.returncode != 0:
        # yt-dlp's own last lines say WHY ("Video unavailable", a bot check…).
        # check=True here dumped a raw CalledProcessError traceback into the
        # studio's red banner, which no one can act on (round-3 user test).
        tail = [ln for ln in (r.stderr or r.stdout or "").splitlines()
                if ln.strip()][-3:]
        raise SystemExit("could not fetch that link:\n" + "\n".join(tail))
    got = sorted(dest.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not got:
        raise SystemExit("yt-dlp produced no audio file")
    return got[-1], got[-1].stem
