"""Publish the authored show to the castle — the pipeline's missing last mile.

studio_scenes.rebuild() renders audio, firmware cues and the page, and used
to stop at the Mac: three correct local artifacts and a board still running
last week's show, with /api/status reporting nothing wrong (grade report A1
— the Ballad-of-the-Witches failure of 2026-08-22). This module pushes the
result out through tools/sd_sync.py (scene tracks, then the lean desk page)
and reports the one thing a push cannot fix: scenes the RUNNING firmware was
not built with, read from /api/status `scenes` (B1), which need a rebuild
and an OTA before a pick stops answering "unknown scene".

Sandbox rules apply unchanged: castle_link honours CASTLE_HOST, and a
set-but-empty value means "explicitly no castle" — publish then reports
"no castle answered" and touches nothing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import build_paths as bp
import castle_link as cl
import studio_scenes as ss

ROOT = Path(__file__).resolve().parent.parent
Runner = Callable[[list[str]], tuple[bool, str]]


def needs_firmware(status: dict) -> list[str]:
    """Scene ids in scenes.yaml that the castle's firmware does not know.

    Empty when nothing is missing — and also when the firmware predates the
    `scenes` field (pre-v5.42), because guessing would be worse than silence;
    the caller can see the field's absence in the raw status if it cares.
    """
    fw = [s for s in str(status.get("scenes") or "").split(",") if s]
    if not fw:
        return []
    return [s for s in ss.scene_ids(bp.SCENES) if s not in fw]


def publish(run: Runner) -> tuple[dict, int]:
    """Push scene tracks and the lean page to the castle; (body, http code).

    sd_sync skips files the card already holds at the same size, so the
    steady-state cost of running this after every scene save is one page
    upload, not a ten-track resend.
    """
    st = cl.status()
    if st is None or st.get("studio"):
        return {
            "ok": False,
            "pushed": False,
            "error": "no castle answered — nothing pushed",
        }, 502
    host = str(st.get("bridged") or cl.castle_host() or "")
    log = ""
    for cmd in ("scenes", "site"):
        ok, out = run([sys.executable, str(ROOT / "tools" / "sd_sync.py"), host, cmd])
        log += out
        if not ok:
            return {
                "ok": False,
                "pushed": False,
                "log": log[-4000:],
                "error": f"sd_sync {cmd} failed",
            }, 500
    stale = needs_firmware(st)
    return {
        "ok": True,
        "pushed": True,
        "log": log[-4000:],
        "needs_firmware": stale,
        "note": (
            f"{len(stale)} scene(s) missing from the running "
            "firmware — make sd-build, stop audio, then OTA"
            if stale
            else ""
        ),
    }, 200
