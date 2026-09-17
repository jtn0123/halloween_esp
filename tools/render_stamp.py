"""Skip a scene render whose inputs have not changed since the last one.

`make audio` fronts every firmware build, and a full render is ~9 s of
crate synthesis and LAME even when scenes.yaml has not moved. A render is
a pure function of a few inputs — the scene's own entry, the audio config,
its imported song (if any), the scene_render binary and the renderer's
own source — so each scene's last render is remembered under a key made
of exactly those, beside the output (`audio/.rendered.json`, gitignored,
and under CASTLE_BUILD like everything else in audio/). Same key, outputs
still on disk → skip; anything else → render, as before. `--force`
ignores the stamps. The markers a skipped scene reported last time are
kept in the stamp, so markers.json is written whole either way.

Byte-identical mp3s are NOT how "unchanged" is decided: LAME is
deterministic but the render is the 9 s, and the comparison would need
it. Inputs are what changed or did not; files are identified by size and
mtime, sources by content (a checkout touches mtimes).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import build_paths as bp
import core_bins

STAMP = ".rendered.json"
TOOLS = Path(__file__).resolve().parent
#: The renderer's own sources: a change to how a scene is rendered or
#: encoded is a change to every scene.
SOURCES = (TOOLS / "render_audio.py", TOOLS / "render_stamp.py")


def file_id(p: Path) -> str:
    """Size and mtime, or 'absent' — an absent song is an input state too:
    a scene rendered without it must render again when it turns up."""
    try:
        st = p.stat()
    except OSError:
        return "absent"
    return f"{st.st_size}:{st.st_mtime_ns}"


def source_id(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def inputs(scene: dict) -> list[Path]:
    """The files a scene's render reads besides scenes.yaml: the crate
    binary that renders it and its song, when it has one."""
    files = [core_bins.CORE / "target" / "release" / "scene_render"]
    if scene.get("audio_file"):
        files.append(bp.track_source(scene["audio_file"]))
    return files


def key(scene: dict, cfg: dict) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([scene, cfg], sort_keys=True, default=str).encode())
    for p in inputs(scene):
        h.update(f"file {p}={file_id(p)}\n".encode())
    for p in SOURCES:
        h.update(f"src {p.name}={source_id(p)}\n".encode())
    return h.hexdigest()


class Stamps:
    """The per-stem record of the last render, in `out`/.rendered.json.
    Until load() it is inert: nothing reused, nothing recorded — which is
    also what --force asks for."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.entries: dict[str, dict] = {}

    def load(self, out: Path, enabled: bool = True) -> None:
        self.path = out / STAMP if enabled else None
        self.entries = {}
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self.entries = data if isinstance(data, dict) else {}

    def reuse(
        self, stem: str, scene: dict, cfg: dict, outputs: list[Path]
    ) -> dict | None:
        """The stamp entry when `stem` was last rendered from these same
        inputs and every output it should have left is still there."""
        e = self.entries.get(stem)
        if (
            self.path is None
            or not isinstance(e, dict)
            or e.get("key") != key(scene, cfg)
        ):
            return None
        return e if all(p.exists() for p in outputs) else None

    def record(
        self, stem: str, scene: dict, cfg: dict, markers: dict, not_here: bool
    ) -> None:
        """Keyed now, AFTER the render: rendering may have rebuilt the crate."""
        if self.path is not None:
            self.entries[stem] = {
                "key": key(scene, cfg),
                "markers": markers,
                "not_here": not_here,
            }

    def save(self) -> None:
        if self.path is not None:
            self.path.write_text(json.dumps(self.entries, sort_keys=True))
