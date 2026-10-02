"""The sandbox a Castle Radio process runs in, set before tools/ is read.

The radio drives this repo's importer, splitter and cue renderer, and those
read CLAUDE.md's sandbox knobs — some of them ONCE, at import:
tools/track_lib.py binds `TRACKS` the first time it is loaded. So the knobs
have to be in the environment before the first tools/ module is, and that
cannot be left to import order. It was: server.py imported desktop_tools,
which pulled in rich_show → render_cues → track_lib a line before radio_jobs
set CASTLE_TRACKS, and `track_lib.TRACKS` was the repo's own tracks/ while
the manifest beside it was sandboxed (grade report 2026-09-24 B7).

So this module is the radio's ONE door to tools/: it sets the knobs, and only
then puts tools/ on sys.path. A radio module that imports a tools/ name
imports this first — and cannot do otherwise, because without it that name
does not resolve. test_radio_env.py holds every module to it.

    import radio_env  # noqa: F401 — the sandbox, then tools/ on the path

    # isort: split
    import portable_fs
"""

import os
import sys
from pathlib import Path

import radio_paths

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
#: The radio's library root — `.radio-data/` beside the server, or wherever
#: CASTLE_RADIO_DATA points (the desktop app's per-user data dir).
DATA = radio_paths.data_dir()
#: The track library the importer writes, under DATA — never the repo's.
LIBRARY = DATA / "tracks"

DATA.mkdir(parents=True, exist_ok=True)
LIBRARY.mkdir(exist_ok=True)
os.environ.update(
    CASTLE_TRACKS=str(LIBRARY),
    CASTLE_HOST="",
    CASTLE_SCENES=str(DATA / "scenes.yaml"),
    CASTLE_BUILD=str(DATA / "build"),
)
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
