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
import re
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
MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "opus": "audio/ogg",
}


def track_files() -> list[Path]:
    """Every imported track, whatever container it landed in, by id."""
    return sorted(
        (p for e in AUDIO_EXT for p in TRACKS.glob(f"*.{e}")), key=lambda p: p.stem
    )


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


SRC_DIR = "_src"  # kept originals of dropped/pulled files, beside the library


def source_copies(tid: str) -> list[Path]:
    """The kept original(s) for a track — what Delete must take with it."""
    return sorted((TRACKS / SRC_DIR).glob(f"{tid}.*"))


#: What an id may contain, matching what import_track will WRITE (letters,
#: digits, underscore — see its explicit-id guard). Spelled here as well so
#: the read path is guarded at its own choke point rather than relying on
#: every caller to have stripped the name first: an id becomes a filename,
#: and a filename with a separator or a parent hop in it is not an id.
ID_RE = re.compile(r"^\w{1,64}$", re.ASCII)


def valid_id(tid: str) -> bool:
    """Is `tid` a track id, rather than something wearing one's clothes?"""
    return bool(ID_RE.match(tid))


def track_path(tid: str) -> Path | None:
    """Resolve a bare track id to the file that holds it.

    The id is the contract everywhere else — scenes, the manifest, the panel —
    and the extension is an import detail. Callers pass the id and get back
    whichever container it happens to live in, or None. An id that is not one
    resolves to nothing at all, whatever it was hoping to reach.
    """
    if not valid_id(tid):
        return None
    for e in AUDIO_EXT:
        p = TRACKS / f"{tid}.{e}"
        if p.exists():
            return p
    return None


def source_missing(source: str) -> bool:
    """True for a file: source whose file is no longer there."""
    return source.startswith("file:") and not Path(source[len("file:") :]).exists()


def track_infos(paths: list[Path]) -> list[dict]:
    """track_info for a whole listing, reading tracks.json ONCE.

    /api/tracks used to load and parse the manifest once per track; that is
    nothing at two tracks and a file read per row at twenty. The route
    should call this rather than mapping track_info over the listing."""
    data = mf.load()
    return [track_info(p, data.get(p.stem) or {}) for p in paths]


def track_info(p: Path, meta: dict | None = None) -> dict:
    """Everything the Tracks panel needs, including where the file came from.

    The manifest is the cheap part — read it even if decoding fails, so a
    broken file still shows its source and can be re-imported. `meta` is
    this track's manifest entry when the caller already holds the whole
    file (track_infos); left None, it is looked up here as before.
    """
    if meta is None:
        meta = mf.get(p.stem) or {}
    info = {
        "id": p.stem,
        # The panel needs this to write `audio_file:` into a scene. Without it
        # it guessed ".mp3", which produced a scene pointing at a file that
        # does not exist for every non-MP3 import.
        "ext": p.suffix.lstrip("."),
        "kb": p.stat().st_size // 1024,
        # Exact size, so the desk can tell a CURRENT card copy from a STALE
        # one (re-imported since it was sent) by comparing against /api/files.
        "bytes": p.stat().st_size,
        "source": meta.get("source", ""),
        # A dropped file's original lives in tracks/_src/ (import_track
        # --keep-source); one that was imported from a path that has since
        # gone cannot be re-imported, and the panel must say so instead of
        # offering a button that fails with an absolute path (JB1-3).
        "source_missing": source_missing(meta.get("source", "")),
        "title": meta.get("title", ""),
        "imported": meta.get("imported", ""),
        "opts": meta.get("opts", {}),
        "notes": meta.get("notes", ""),
    }
    cached = _from_manifest(meta, p.stat().st_size)
    if cached is not None:
        info.update(cached)
        return info
    try:
        x = ana.load_audio(p)
        marks = ana.analyze(x)
    except Exception as e:
        info["error"] = str(e)
        return info
    info["dur"] = round(len(x) / ana.SR, 2)
    info["onsets"] = {k: len(v) for k, v in marks.items()}
    # Remember the answer beside the provenance, so the next /api/tracks
    # reads it instead of decoding the library again (it was linear in the
    # number of tracks, every call). `bytes` is the staleness check.
    mf.patch(
        p.stem,
        audio={
            **meta.get("audio", {}),
            "duration": info["dur"],
            "bytes": info["bytes"],
        },
        onsets=info["onsets"],
    )
    return info


def _from_manifest(meta: dict, size: int) -> dict | None:
    """The decode-free answer, if the manifest has one for THIS file.

    import_track.py records duration, byte size and per-band onset counts
    at import time; the size doubles as the staleness check, so a file
    replaced out of band (same id, different bytes) is decoded afresh. The
    import's level_* entries (envelope points for beatless bands) are not
    onsets and are left out, matching what a live analysis reports.
    """
    audio = meta.get("audio") or {}
    onsets = meta.get("onsets")
    if "duration" not in audio or not isinstance(onsets, dict):
        return None
    if audio.get("bytes") != size:
        return None
    return {
        "dur": round(float(audio["duration"]), 2),
        "onsets": {k: int(v) for k, v in onsets.items() if k.startswith("onset_")},
    }
