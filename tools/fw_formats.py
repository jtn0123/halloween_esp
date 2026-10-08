"""Which castle firmware reads which card format — the publish handshake.

The show is card data (tools/scene_manifest.py's `show.man`, v5.67;
tools/cue_file.py's `.cue`, v5.63, version 2 since v5.71), and each file
carries its format's version in byte 4 of its header. A castle that does not
read that version does not misread it: castle_scenes.h and castle_cues.h
refuse the file whole, and the castle plays the song dark — which an owner
sees as "the app broke my castle", hours after the push that did it.

The firmware does not report the format versions it reads, so its VERSION
stands in for them, through ONE table — READERS, below: the first firmware
that reads each (format, version). tests/test_fw_formats.py holds it to the
writers' version constants and to the newest version each firmware header
reads, so a format bump that forgets this table is a red suite, not a dark
castle.

`refusal` is the one decision, and every path that puts a format file on a
castle asks it before the first byte goes: tools/sd_sync.py (`scenes`,
`cues` — so `make publish` and the studio's auto-publish, which spawns it)
and Castle Radio's sync (demo/castle-radio/remote_library.py). Its answer is
a sentence for the owner that ends by telling them to update the castle
first; the studio finds it in sd_sync's output by UPDATE_FIRST
(core/src/studio_publish.rs holds a copy, docs/PARITY.md).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cue_file
import scene_manifest

if TYPE_CHECKING:
    from sd_ota import Api

ROOT = Path(__file__).resolve().parent.parent

#: format -> {version byte -> the first castle firmware that reads it}.
#: firmware/pending/README.md is the history: show.man arrived with v5.67,
#: the song .cue with v5.63 and its version 2 with v5.71.
READERS: dict[str, dict[int, tuple[int, int]]] = {
    "show.man": {scene_manifest.VERSION: (5, 67)},
    "cue": {cue_file.VERSION: (5, 63), cue_file.VERSION_2: (5, 71)},
}
#: What the owner reads for each format.
KINDS = {"show.man": "scene list", "cue": "light-show"}
MAGIC = {"show.man": scene_manifest.MAGIC, "cue": cue_file.MAGIC}
#: The words every refusal ends with — the studio's marker for the line.
UPDATE_FIRST = "update the castle first"

_VERSION = re.compile(r"^\s*v?(\d+)\.(\d+)")


def parse_version(text: object) -> tuple[int, int] | None:
    """`5.75` (or `v5.75`, `5.75-dev`) as (5, 75); None when it is not one."""
    m = _VERSION.match(text) if isinstance(text, str) else None
    return (int(m[1]), int(m[2])) if m else None


def shown(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def this_firmware(root: Path = ROOT) -> str:
    """The version the device build compiles in: firmware/castle.yaml's
    `version:` — what /api/status reports once that build is on a castle."""
    text = (root / "firmware" / "castle.yaml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise SystemExit("firmware/castle.yaml has no version: line")


def kind_of(name: str) -> str | None:
    """The format a card file of this name is in, if the table governs it."""
    return (
        "show.man" if name == "show.man" else "cue" if name.endswith(".cue") else None
    )


def format_of(name: str, head: bytes) -> tuple[str, int] | None:
    """(format, version byte) of a card file the table governs, else None.
    A file with the wrong magic is version 0, which nothing reads."""
    kind = kind_of(name)
    if kind is None:
        return None
    if len(head) < 5 or head[:4] != MAGIC[kind]:
        return kind, 0
    return kind, head[4]


def refusal(status: Mapping[str, Any], files: Mapping[str, bytes]) -> str | None:
    """Why the castle answering `status` cannot be sent `files` (name ->
    bytes, or just their first 16), or None when it reads every one."""
    worst: tuple[tuple[int, int], str, str, int] | None = None
    for name, head in sorted(files.items()):
        found = format_of(name, head)
        if found is None:
            continue
        kind, version = found
        need = READERS[kind].get(version)
        if need is None:
            return (
                f"{name} is {KINDS[kind]} format {version}, which no castle "
                "firmware reads — rebuild the show with this app and send it again."
            )
        if worst is None or need > worst[0]:
            worst = (need, name, kind, version)
    if worst is None:
        return None
    need, name, kind, version = worst
    have = parse_version(status.get("version"))
    if have is not None and have >= need:
        return None
    runs = (
        f"this castle runs {shown(have)}"
        if have
        else "this castle does not say which it runs"
    )
    return (
        f"{name} ({KINDS[kind]} format {version}) needs castle firmware "
        f"{shown(need)} or newer, and {runs} — {UPDATE_FIRST}, then send the "
        "show again."
    )


def heads(paths: Iterable[Path]) -> dict[str, bytes]:
    """The first 16 bytes of every file the table governs, by name."""
    out = {}
    for p in paths:
        if kind_of(p.name) is not None:
            with p.open("rb") as fh:
                out[p.name] = fh.read(16)
    return out


def check_publish(ip: str, api: Api, paths: Iterable[Path]) -> None:
    """sd_sync's door: SystemExit with the refusal before anything is sent.
    Asks the castle only when there is a format file to send."""
    files = heads(paths)
    if not files:
        return
    try:
        status = json.loads(api(ip, "GET", "/api/status", timeout=10))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"the castle did not say which firmware it runs ({exc}) — nothing sent"
        ) from None
    why = refusal(status if isinstance(status, dict) else {}, files)
    if why:
        raise SystemExit(why)
