"""Where the generators read the show from, and where they write.

Two environment knobs, one story: a SANDBOXED studio must not be able to
touch the real show or its rendered artefacts.

    CASTLE_SCENES   the scenes file (playwright.config.ts sets it for the
                    e2e suite; a UX session on a scratch copy sets it too)
    CASTLE_BUILD    the root the artefacts land under — audio/,
                    firmware/generated/ and previewer/castle-cue-desk.html
                    are written relative to it

The hole this closes (judge B, JB1-12): the studio honoured CASTLE_SCENES
for the splice, then ran render_audio / gen_esphome / gen_previewer, which
read the REAL scenes/scenes.yaml and rewrote the repo's audio/,
firmware/generated/ and previewer page. A sandboxed scenes file with no
CASTLE_BUILD of its own now builds beside itself, in `_build/`, never in
the repo. Unset both and everything is the repo, as before.

Imported by the three generators and the studio; nothing here touches the
filesystem at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def scenes_file() -> Path:
    return Path(os.environ.get("CASTLE_SCENES") or (ROOT / "scenes" / "scenes.yaml"))


def sandboxed() -> bool:
    """True when the scenes file is NOT the repo's own."""
    return scenes_file().resolve() != (ROOT / "scenes" / "scenes.yaml").resolve()


def build_root() -> Path:
    """Where audio/, firmware/generated/ and the previewer page go."""
    env = os.environ.get("CASTLE_BUILD")
    if env:
        return Path(env)
    return scenes_file().parent / "_build" if sandboxed() else ROOT


def track_source(rel: str) -> Path:
    """Resolve a scene's `audio_file:` (e.g. tracks/x.mp3) to a real file.

    The library can be redirected too (CASTLE_TRACKS); a sandboxed scene
    naming tracks/<id>.mp3 means the sandbox's copy, not the repo's.
    """
    lib = os.environ.get("CASTLE_TRACKS")
    if lib and rel.startswith("tracks/"):
        return Path(lib) / rel[len("tracks/") :]
    return ROOT / rel


def rel(p: Path) -> str:
    """For log lines: repo-relative when inside the repo, absolute otherwise."""
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


SCENES = scenes_file()
BUILD = build_root()
AUDIO = BUILD / "audio"
GENERATED = BUILD / "firmware" / "generated"
PREVIEW_HTML = BUILD / "previewer" / "castle-cue-desk.html"
