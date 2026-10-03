#!/usr/bin/env python3
"""The shipped show: the yard's scenes.yaml minus every scene that needs a song.

A castle that leaves this house carries NO songs (docs/LICENSING.md, the
"Shipped music" section): the two imported tracks in the yard's show are
somebody else's recordings, and a scene that plays one cannot ship. What can
ship is every scene whose sound the renderer makes itself — the synthesised
wind, thunder, organ and heartbeat — and that is the shipped show.

    scenes/scenes.yaml   the yard's show, songs and all (the source)
    scenes/shipped.yaml  THIS, derived from it: the same hardware, zones,
                         palette and show block, and the track-free scenes,
                         byte for byte as they are in the source

Everything that hands a show to somebody else reads the second file:

    desktop/src-tauri/src/runtime.rs  seed_scenes — the app's first run
    tools/desktop_env.py              SHIPPED_SCENES — the uv installer's
    tools/buyer_card.py               the SD card image for a sold castle

and tests/test_shipped_show.py is the gate that keeps a song out of it: no
`audio_file`, no `track_*` key, no onset pulse (onsets are read from a track),
no `tracks/` path anywhere in the file, and every scene the firmware starts
by name present.

It is DERIVED by text, not re-dumped through a YAML writer, so the comments
that explain the show survive and a tuned scene arrives exactly as it was
tuned. gen_esphome.py rewrites it whenever it builds the repo's own show, the
way it rewrites firmware/generated/, so a desk edit to `vigil` reaches the
shipped show with the same rebuild that reaches the yard.

    tools/shipped_show.py           # write scenes/shipped.yaml
    tools/shipped_show.py --check   # exit 1 when it is not what the source says
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scenes" / "scenes.yaml"
SHIPPED = ROOT / "scenes" / "shipped.yaml"

#: A scene list item, at the indentation scenes.yaml writes them.
_ITEM = re.compile(r"^  - id:\s*(\S+)\s*$")

BANNER = """\
# THE SHIPPED SHOW — the scenes a castle leaves this house with.
#
# GENERATED in the repo by tools/shipped_show.py from scenes/scenes.yaml: the
# same file minus every scene that needs an imported song (no song ships —
# docs/LICENSING.md). Edit the source, not this; gen_esphome.py rewrites it.
#
# On a castle that has been sold, it is the opposite: the desktop app and
# the installer copy this file ONCE, on first run, and from then on the copy
# is the owner's own show to edit.
#
"""


def needs_track(scene: Mapping[str, Any]) -> list[str]:
    """Why `scene` cannot ship without a song — empty when it can.

    Three ways a scene leans on an imported track: it plays one
    (`audio_file`), it tunes one (`track_gain`, `track_at`, ...), or its
    lights pulse on onsets that are detected FROM one (`onset_low` & co.)
    and would be a dark scene without it.
    """
    why = []
    if "audio_file" in scene:
        why.append(f"audio_file: {scene['audio_file']}")
    why += [f"{k}:" for k in sorted(scene) if str(k).startswith("track_")]
    for p in scene.get("pulse") or []:
        synth = str(p.get("synth", "")) if isinstance(p, Mapping) else ""
        if synth.startswith("onset"):
            why.append(f"pulse synth {synth}")
    return why


def shipped_ids(doc: Mapping[str, Any]) -> list[str]:
    """The ids of the scenes that ship, in show order."""
    return [str(s["id"]) for s in doc["scenes"] if not needs_track(s)]


def _show_block(doc: Mapping[str, Any], keep: set[str]) -> str | None:
    """The `show:` block re-emitted with dropped ids taken out of `order`,
    or None when there is no `order` to fix and the source's block stands."""
    show = doc.get("show") or {}
    order = show.get("order")
    if not order or all(sid in keep for sid in order):
        return None
    fixed = dict(show, order=[sid for sid in order if sid in keep])
    return "show:\n" + "".join(
        f"  {line}\n" for line in yaml.safe_dump(fixed, sort_keys=False).splitlines()
    )


def _replace_block(text: str, key: str, block: str) -> str:
    """Swap the top-level `key:` mapping in `text` for `block`."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{key}:"))
    end = start + 1
    while end < len(lines) and (
        lines[end].startswith((" ", "\n")) or not lines[end].strip()
    ):
        end += 1
    return "".join(lines[:start]) + block + "\n" + "".join(lines[end:])


def derive(text: str) -> str:
    """scenes.yaml's text -> the shipped show's text.

    Everything above `scenes:` is kept verbatim (hardware, show, zones,
    palette and the comments that explain them); below it, each `  - id:`
    block is kept or dropped whole. Raises SystemExit when nothing would
    ship — a castle with an empty show is not a product.
    """
    doc = yaml.safe_load(text)
    keep = set(shipped_ids(doc))
    if not keep:
        raise SystemExit("shipped show: every scene needs a song — nothing ships")
    lines = text.splitlines(keepends=True)
    at = next(i for i, ln in enumerate(lines) if ln.rstrip() == "scenes:")
    head = "".join(lines[: at + 1])
    body: list[str] = []
    keeping = True
    for line in lines[at + 1 :]:
        m = _ITEM.match(line)
        if m:
            keeping = m.group(1) in keep
        if keeping:
            body.append(line)
    out = head + "".join(body)
    block = _show_block(doc, keep)
    if block is not None:
        out = _replace_block(out, "show", block)
    out = BANNER + out
    # The text cut must mean what the YAML says: same scenes, same order, and
    # each one the very mapping the source holds.
    got = yaml.safe_load(out)
    want = [s for s in doc["scenes"] if s["id"] in keep]
    if got["scenes"] != want:
        raise SystemExit(
            "shipped show: the text cut does not round-trip — a scene block "
            "is not where `  - id:` says it starts"
        )
    return out.rstrip("\n") + "\n"


def write(src: Path = SOURCE, dst: Path = SHIPPED) -> bool:
    """Write `dst` from `src`; True when its bytes changed."""
    text = derive(src.read_text(encoding="utf-8"))
    if dst.exists() and dst.read_text(encoding="utf-8") == text:
        return False
    dst.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true", help="fail when stale")
    ap.add_argument("--src", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, default=SHIPPED)
    args = ap.parse_args(argv)
    if args.check:
        want = derive(args.src.read_text(encoding="utf-8"))
        have = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if have != want:
            print(f"{args.out} is stale — run tools/shipped_show.py", file=sys.stderr)
            return 1
        print(f"{args.out}: current")
        return 0
    changed = write(args.src, args.out)
    ids = shipped_ids(yaml.safe_load(args.out.read_text(encoding="utf-8")))
    print(f"{'wrote' if changed else 'unchanged'} {args.out}: {', '.join(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
