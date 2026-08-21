"""The studio's scenes.yaml editor: splice one scene block in, re-render.

Split out of studio.py at the 500-line cap, along the seam that was
already there: nothing here knows about HTTP. It answers "put this scene
into the show" and "which scenes are in the show", and the server above
it does the routing and the JSON.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

import yaml

Runner = Callable[[list[str]], tuple[bool, str]]


def scene_ids(scenes: Path) -> list[str]:
    try:
        doc = yaml.safe_load(scenes.read_text())
        return [s["id"] for s in doc.get("scenes", [])]
    except Exception as e:
        # But it must not be SILENT either: "no scenes" and "the file is
        # broken, here's the parse error" are very different situations.
        print(f"WARNING: could not parse {scenes}: {e}")
        return []


def rebuild(lock: threading.Lock, run: Runner, py: str,
            root: Path) -> tuple[bool, str]:
    """audio → firmware cues → previewer, serialised with the encode jobs."""
    with lock:
        ok1, o1 = run([py, str(root / "tools" / "render_audio.py")])
        ok2, o2 = run([py, str(root / "tools" / "gen_esphome.py")])
        ok3, o3 = run([py, str(root / "tools" / "gen_previewer.py")])
    return ok1 and ok2 and ok3, (o1 + o2 + o3)[-4000:]


def splice(scenes: Path, req: dict, lock: threading.Lock, run: Runner,
           py: str, root: Path) -> tuple[dict, int]:
    """Insert or replace a scene block in scenes.yaml; (body, http code).

    Text splicing, not a YAML round-trip: scenes.yaml is a hand-authored
    file full of comments that carry the reasoning behind the show, and
    dumping it back through a YAML serialiser would erase all of them.
    """
    block = (req.get("yaml") or "").rstrip()
    sid = (req.get("id") or "").strip()
    if not block or not sid:
        return {"error": "need id and yaml"}, 400
    # The block must PARSE, and must be the one scene it claims to be —
    # scenes.yaml is the hand-authored source of truth for the whole
    # show, and a malformed splice used to corrupt it permanently (the
    # UI then showed "no scenes" instead of an error).
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError as e:
        return {"error": f"scene is not valid YAML: {e}"}, 400
    if (not isinstance(parsed, list) or len(parsed) != 1
            or not isinstance(parsed[0], dict) or parsed[0].get("id") != sid):
        return {"error": f"expected exactly one scene with id {sid!r}"}, 400
    pat = re.compile(rf"^  - id: {re.escape(sid)}\n(?:.*\n)*?(?=^  - id: |\Z)",
                     re.MULTILINE)
    with lock:
        # Read-modify-write under the lock: two concurrent saves used to
        # interleave here and one silently lost.
        before = scenes.read_text()
        replaced = bool(pat.search(before))
        if replaced:
            # lambda: a plain-string replacement treats backslashes as
            # group escapes and mangles the block (gen_previewer.py
            # documents this exact trap).
            raw = pat.sub(lambda _: block + "\n\n", before)
        else:
            raw = before.rstrip() + "\n\n" + block + "\n"
        # Exactly one trailing newline either way. Replacing the last
        # scene in the file leaves a blank line behind otherwise — stable
        # rather than growing, but it shows up as a diff on a write that
        # changed nothing.
        # Keep the pre-edit text, then replace atomically: a crash
        # mid-write must never be able to truncate the show.
        scenes.with_suffix(".yaml.bak").write_text(before)
        tmp = scenes.with_suffix(".yaml.tmp")
        tmp.write_text(raw.rstrip() + "\n")
        os.replace(tmp, scenes)
    ok, log = rebuild(lock, run, py, root)
    # `replaced` and `scenes` are what let the panel say what actually
    # happened instead of "written", which is indistinguishable from
    # nothing having happened at all.
    return ({"ok": ok, "id": sid, "replaced": replaced,
             "scenes": scene_ids(scenes), "log": log}, 200 if ok else 500)
