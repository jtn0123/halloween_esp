#!/usr/bin/env python3
"""The record of where each imported track came from.

An imported MP3 is a dead end on its own: once it's converted you can't tell
what it was made from, what the source was, or what settings produced it. So
every import writes its provenance here, and `--refresh` can rebuild a track
from the same source with different settings without you going to find the
link again.

tracks/tracks.json — one entry per track id:

    {
      "chant": {
        "source":   "https://…"  or  "file:/Users/…/thing.wav",
        "title":    "whatever the source called itself",
        "imported": "2026-08-10T18:22:03",
        "opts":     {"start": "0:12", "take": 24, "bitrate": 96, …},
        "audio":    {"duration": 24.0, "bytes": 288000, "channels": 1, …},
        "onsets":   {"onset_low": 40, "onset_mid": 88, "onset_high": 31},
        "notes":    "free text"
      }
    }

Tracked in git even though the audio isn't: the manifest is small, it's the
part that makes an import reproducible, and it costs nothing to keep.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The manifest lives beside the tracks it describes, so it follows the same
# override — otherwise a test pointed at a scratch directory would still
# rewrite the real tracks.json when it deletes something.
PATH = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks")) / "tracks.json"


@contextmanager
def _locked():
    """Cross-process lock for read-modify-write.

    The studio server (forget) and its import_track children (record) both
    rewrite this file; without the lock, two concurrent imports could each
    load, mutate and save — and one import's provenance would silently
    vanish."""
    PATH.parent.mkdir(exist_ok=True)
    with open(PATH.with_suffix(".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load() -> dict:
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text())
    except json.JSONDecodeError:
        # A half-written manifest used to read as "no tracks were ever
        # imported" — and the NEXT save then persisted that empty dict,
        # destroying every track's provenance. Move the damaged file aside
        # (nothing is lost, it can be hand-repaired) and say so loudly.
        aside = PATH.with_name(f"tracks.json.corrupt-{int(time.time())}")
        PATH.rename(aside)
        print(f"WARNING: {PATH.name} was not valid JSON — moved to "
              f"{aside.name}; starting from an empty manifest")
        return {}


def save(data: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    # Write-then-rename so a crash mid-write can never truncate the real
    # file: os.replace is atomic on the same filesystem.
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, PATH)


def record(tid: str, *, source: str, title: str = "", opts: dict | None = None,
           audio: dict | None = None, onsets: dict | None = None,
           notes: str = "") -> dict:
    with _locked():
        data = load()
        prev = data.get(tid, {})
        entry = {
            "source": source,
            "title": title or prev.get("title", ""),
            # Local wall clock on purpose: single-machine library, human-read
            # stamps. A tz-aware stamp would churn every entry for no reader.
            "imported": datetime.now().replace(microsecond=0).isoformat(),  # noqa: DTZ005
            "opts": opts or {},
            "audio": audio or {},
            "onsets": onsets or {},
            # Notes are the user's; never clobber them on a refresh.
            "notes": notes or prev.get("notes", ""),
        }
        data[tid] = entry
        save(data)
    return entry


def get(tid: str) -> dict | None:
    return load().get(tid)


def forget(tid: str) -> None:
    with _locked():
        data = load()
        if tid in data:
            del data[tid]
            save(data)
