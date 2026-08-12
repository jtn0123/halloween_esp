#!/usr/bin/env python3
"""What a track is, on disk.

Split out of studio.py at the 500-line cap, along the seam that was already
there: none of this knows about HTTP. It answers "which files are tracks",
"where does this id live", and "what does the panel need to know about it" —
and the server above it does the routing.

The id is the contract everywhere else in the project: scenes reference it,
the manifest keys on it, the panel displays it. Which container it happens to
live in is an import detail that stops here.
"""

from __future__ import annotations

import os
from pathlib import Path

import analyze as ana
import manifest as mf

ROOT = Path(__file__).resolve().parent.parent
# Overridable so a test can drive the real server against a disposable
# directory instead of the tracks you actually care about. Nothing else reads
# it; leave it unset and this is the repo's own tracks/.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))

# Every container import_track.py can write. This list is the reason the format
# option works at all end to end: globbing "*.mp3" — which is what this file did
# when only MP3 existed — makes a WAV or FLAC import land on disk and then never
# appear in the panel, which reads as the import having silently failed.
AUDIO_EXT = ("mp3", "wav", "flac", "opus")
MIME = {"mp3": "audio/mpeg", "wav": "audio/wav",
        "flac": "audio/flac", "opus": "audio/ogg"}


def track_files() -> list[Path]:
    """Every imported track, whatever container it landed in, by id."""
    return sorted((p for e in AUDIO_EXT for p in TRACKS.glob(f"*.{e}")),
                  key=lambda p: p.stem)


def parse_sensitivity(q: dict) -> float | dict:
    """Read `?sensitivity=` plus any per-band `?sens_low=` overrides.

    The three bands routinely want different thresholds — a track can have a
    crisp kick under a wash of cymbals — so the editor sends one per band. A
    bare `sensitivity` still means "all three", which is what every older
    caller sends.
    """
    base = 1.1
    try:
        base = float((q.get("sensitivity") or ["1.1"])[0])
    except ValueError:
        pass
    per = {}
    for short in ("low", "mid", "high"):
        raw = (q.get(f"sens_{short}") or [None])[0]
        if raw is None:
            continue
        try:
            per[f"onset_{short}"] = float(raw)
        except ValueError:
            continue
    if not per:
        return base
    # Any band the caller did not name keeps the shared value.
    for short in ("low", "mid", "high"):
        per.setdefault(f"onset_{short}", base)
    return per


def track_path(tid: str) -> Path | None:
    """Resolve a bare track id to the file that holds it.

    The id is the contract everywhere else — scenes, the manifest, the panel —
    and the extension is an import detail. Callers pass the id and get back
    whichever container it happens to live in, or None.
    """
    for e in AUDIO_EXT:
        p = TRACKS / f"{tid}.{e}"
        if p.exists():
            return p
    return None


def track_info(p: Path) -> dict:
    """Everything the Tracks panel needs, including where the file came from.

    The manifest is the cheap part — read it even if decoding fails, so a
    broken file still shows its source and can be re-imported.
    """
    meta = mf.get(p.stem) or {}
    info = {
        "id": p.stem,
        # The panel needs this to write `audio_file:` into a scene. Without it
        # it guessed ".mp3", which produced a scene pointing at a file that
        # does not exist for every non-MP3 import.
        "ext": p.suffix.lstrip("."),
        "kb": p.stat().st_size // 1024,
        "source": meta.get("source", ""),
        "title": meta.get("title", ""),
        "imported": meta.get("imported", ""),
        "opts": meta.get("opts", {}),
        "notes": meta.get("notes", ""),
    }
    try:
        x = ana.load_audio(p)
        marks = ana.analyze(x)
    except Exception as e:                       # noqa: BLE001
        info["error"] = str(e)
        return info
    info["dur"] = round(len(x) / ana.SR, 2)
    info["onsets"] = {k: len(v) for k, v in marks.items()}
    return info
