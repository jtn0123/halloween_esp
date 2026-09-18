"""Which scenes the emulated castle knows — read from the card, then the show.

Since v5.67 the firmware seeds its scene list from the CARD: /sd/scenes/show.man,
the manifest tools/scene_manifest.py writes, falling back to the ids the image
was compiled with (generated/fallback_scenes.h) when there is no manifest it can
read. That is the whole point of the change — adding a scene is a publish, not
an OTA — so the emulator reads the same two sources in the same order and 404s
exactly the ids the real castle would.

`card_scene_ids` is the first; `show_scene_ids` is the second, standing in for
the compiled-in list, because a scenes.yaml is what the emulator has instead of
a build.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def a_show_file(src: Path) -> Path | None:
    """`src`, if it is plausibly a show to read — else None.

    The path arrives from the environment (CASTLE_SCENES) or the command
    line, so it is checked BEFORE it is opened rather than after: resolved,
    so `..` cannot point somewhere else halfway through, and required to be
    a regular file with a YAML name. A directory, a device node or a socket
    is not a show, and the caller already knows what to do with None.
    """
    try:
        resolved = src.expanduser().resolve()
    except (OSError, RuntimeError):  # loops, unreadable parents
        return None
    if resolved.suffix.lower() not in (".yaml", ".yml"):
        return None
    return resolved if resolved.is_file() else None


def card_scene_ids(sd_dir: Path) -> list[str] | None:
    """Scene ids from the emulated card's own manifest, or None when there is
    no manifest this reader believes — the four refusals are
    scene_manifest.decode's, and they are castle_scenes::open_manifest's.

    "stop" is appended here for the same reason the firmware appends it to the
    seeded list: it is a name /api/scene must accept and no manifest lists it.
    """
    import scene_manifest

    ids = scene_manifest.scene_ids(sd_dir / "scenes" / "show.man")
    return [*ids, "stop"] if ids else None


def show_scene_ids(path: Path | None = None) -> list[str] | None:
    """Scene ids from a scenes.yaml — CASTLE_SCENES, else the repo's — or
    None when there is no readable show to seed from."""
    import yaml

    src = a_show_file(
        path or Path(os.environ.get("CASTLE_SCENES") or ROOT / "scenes" / "scenes.yaml")
    )
    if src is None:
        return None
    try:
        doc = yaml.safe_load(src.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    # "scenes:" with nothing under it parses to None — an empty sandbox
    # show (the e2e suite's) must seed the defaults, not crash the emulator.
    ids = [str(sc["id"]) for sc in (doc.get("scenes") or []) if "id" in sc]
    return [*ids, "stop"] if ids else None
