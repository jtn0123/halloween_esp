"""Where a track lives, and what it may be called.

The library half of what used to be `tools/studio_tracks.py`. That file
answered two different questions — "which files are tracks" for the
importer, and "what does the Tracks panel need to know about one" for the
server — and the server half went with `tools/studio.py`
(docs/RETIREMENT.md phase 3; castle-core's `studio_tracks.rs` is what
answers the panel now). What is left is the part the importer and the
converter both need, in one place so the two cannot disagree about where
the library is or what an import may write into it.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The library. Overridable so a test — or a studio started with
#: CASTLE_TRACKS — works against a disposable directory instead of the
#: tracks you actually care about (CLAUDE.md's sandboxing section). Bound
#: at import, which is why tests/helpers.py clears the knob before any
#: tools module is imported.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))

#: Every container import_track.py can write. This list is the reason the
#: format option works at all end to end: globbing "*.mp3" — which is what
#: this did when only MP3 existed — makes a WAV or FLAC import land on disk
#: and then never appear in the panel, which reads as a silent failure.
AUDIO_EXT = ("mp3", "wav", "flac", "opus")

#: Kept originals of dropped/pulled files, beside the library. A track
#: imported with --keep-source can be re-imported later with different
#: options; one imported from a path that has since gone cannot.
SRC_DIR = "_src"
