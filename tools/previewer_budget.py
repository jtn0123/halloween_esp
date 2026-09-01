"""How heavy the built cue desk may be, and what happens when it is heavier.

Split out of gen_previewer.py (the 500-line rule) on a real seam: everything
here answers one question — *how big is the page allowed to be* — and none of
it knows how a page is built. gen_previewer hands `fit_budget` a closure that
renders the page from a given audio map, and gets back the body it should
write plus the list of scenes that had to give up their inlined audio.

The route the given-up scenes get is the same one the lean rewrite emits, so
it lives here beside the budget that causes it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import build_paths as bp

#: Where a scene's audio lives when it is NOT inlined. The studio serves it,
#: the castle's SD card serves it under its own prefix (see gen_previewer.lean,
#: which rewrites to `/site/<id>.mp3` for the device), and a page opened from
#: disk cannot fetch it at all — which is why the desk falls back to the live
#: synth per scene.
AUDIO_ROUTE = "/studio/scene-audio/"

#: Ceiling for the built page, in KB. It no longer moves, because the page no
#: longer has to grow: scenes are inlined IN SHOW ORDER only while they fit,
#: and the rest get AUDIO_ROUTE links. A scene reached by URL still sounds —
#: web/src/audio.ts falls back to the live synth per scene when a link cannot
#: be fetched, which is what a page opened from disk does — so the portable
#: build is now O(1) in scene count instead of ~1.2 MB per song.
#:
#: History, because the number moved twice and should not move again: 3 MB
#: held the nine-scene rig; scene 10 (the 3-minute Ballad) pushed it past and
#: the line was raised to 4 MB. Twice is a ratchet. The only build that still
#: FAILS here is one whose page is over budget with NOTHING inlined — that is
#: markup and bundle, and no scene can be blamed for it.
PAGE_BUDGET_KB = 4 * 1024


def fit_budget(
    page: Callable[[dict[str, str]], bytes], audio: dict[str, str], budget: int
) -> tuple[bytes, list[str]]:
    """Build the page, giving away scene audio until it fits; (body, linked).

    Inlined audio is what makes the file portable — open it from disk with no
    server and it still plays — so scenes keep their data URI for as long as
    the budget allows, IN SHOW ORDER: the scenes an operator reaches first are
    the ones that stay self-contained. The overflow gets the same
    `/studio/scene-audio/<id>` link the lean rewrite emits, which the studio
    and the card both answer; opened from disk those cannot be fetched, and
    the desk falls back to the live synth for exactly those scenes.

    This is what stops the ratchet (grade report 2026-08-31 G2): before it, one more song
    meant one more megabyte and a raised constant. `linked` is returned so the
    build can SAY which scenes went that way — a silently un-inlined scene
    would be its own quiet surprise.
    """
    keep = dict(audio)
    body = page(keep)
    linked: list[str] = []
    # From the back: the last scenes in the show are the ones given up first.
    for sid in reversed(list(audio)):
        if len(body) <= budget:
            break
        keep[sid] = f"{AUDIO_ROUTE}{sid}"
        linked.append(sid)
        body = page(keep)
    linked.reverse()
    return body, linked


def enforce_budget(body: bytes, audio: dict[str, str], out: Path) -> str | None:
    """The complaint when even a page with NOTHING inlined is over budget.

    fit_budget has already given every scene it could to a URL, so reaching
    this means the weight is the markup and the bundle, and the build stops
    rather than writing a page that cannot be opened. `out` is the page that
    was NOT overwritten, named so the operator knows the tree still has one.
    """
    total_kb = len(body) // 1024
    if total_kb <= PAGE_BUDGET_KB:
        return None
    heavy = sorted(((len(v) // 1024, k) for k, v in audio.items()), reverse=True)[:3]
    worst = ", ".join(f"{sid} ({kb / 1024:.1f} MB)" for kb, sid in heavy)
    return (
        f"page budget FAILED — the portable build is {total_kb / 1024:.2f} MB, "
        f"over its {PAGE_BUDGET_KB // 1024} MB ceiling. Nothing was written; "
        f"{bp.rel(out)} still holds the last good build.\n"
        f"  heaviest inlined audio: {worst}\n"
        "  Every scene that could be un-inlined already was, so this is the "
        "page itself — markup, styles and the bundle. Ways out, best first:\n"
        "   - shrink the bundle (web/src) or the styles, which is where the "
        "growth will be;\n"
        "   - raising PAGE_BUDGET_KB is the second answer, not the first, and "
        "belongs in its own commit that says why."
    )
