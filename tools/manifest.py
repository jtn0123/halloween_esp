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

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "tracks" / "tracks.json"


def load() -> dict:
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text())
    except json.JSONDecodeError:
        return {}


def save(data: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def record(tid: str, *, source: str, title: str = "", opts: dict | None = None,
           audio: dict | None = None, onsets: dict | None = None,
           notes: str = "") -> dict:
    data = load()
    prev = data.get(tid, {})
    entry = {
        "source": source,
        "title": title or prev.get("title", ""),
        "imported": datetime.now().replace(microsecond=0).isoformat(),
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
    data = load()
    if tid in data:
        del data[tid]
        save(data)
