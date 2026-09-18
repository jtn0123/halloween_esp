#!/usr/bin/env python3
"""Inject generated scene data (and the rendered audio) into the previewer.

The previewer began life with a hand-coded scene list. Now that scenes.yaml is
the source of truth, this script splices a generated data block into the HTML
between two markers, so the browser cue desk and the firmware can no longer
drift apart:

    // @GEN-DATA-START ... // @GEN-DATA-END

The block carries:
  - every scene: cues, score, base effects, volume, blurb — previewer-shaped
  - the per-scene slice of scenes.yaml, verbatim, for the source panel
  - the rendered audio/NN_<id>.mp3 files as data URIs, so the previewer can
    play EXACTLY what will ship in flash, not just its live approximation

Run after `make audio`:  tools/gen_previewer.py
"""

from __future__ import annotations

import base64
import json
import re

# `as` marks the explicit seam: the build tests swap gen_previewer.subprocess
# for a double, so the binding is part of this module's surface.
import subprocess as subprocess
import sys
from pathlib import Path

# The pulse dynamics helpers (tempo, accent, pan threshold) are shared with
# the firmware generator rather than duplicated a third time — one pair of
# implementations (Python here, TS in track_lights.ts) is a parity burden;
# three was how the last drift happened.
from typing import Any

import build_paths as bp

# The page's weight budget and the un-inlining that keeps it: one question,
# one module (previewer_budget.py). Imported as a module, not by name, so a
# test that moves the ceiling moves it in one place.
import previewer_budget as pgb

# What a cue MEANS to the desk — the other half of this file's old job.
import previewer_cues as pc
import scene_schema
import yaml
from effect_vocab import KNOWN_EFFECTS as KNOWN_EFFECTS  # one vocabulary, re-exported

ROOT = Path(__file__).resolve().parent.parent
# What is read from the show and what is written follow build_paths.py, so
# a sandboxed studio's rebuild lands beside its own scenes file. The
# template, styles and bundle are the repo's — they are inputs, not show.
SRC = bp.SCENES
MARKERS_FILE = bp.AUDIO / "markers.json"
TEMPLATE = ROOT / "previewer" / "template.html"
HTML = bp.PREVIEW_HTML
AUDIO = bp.AUDIO
WEB = ROOT / "web"
BUNDLE = WEB / "dist" / "bundle.js"
STYLES = ROOT / "previewer" / "styles.css"
# The overflow room: styles.css sits at the 500-line cap, and pushing new
# rules into cssText strings was costing panels their theming.
PANELS = ROOT / "previewer" / "panels.css"
MOBILE = ROOT / "previewer" / "mobile.css"

START = "// @GEN-DATA-START"
END = "// @GEN-DATA-END"
BUNDLE_MARK = "/* @BUNDLE"
STYLE_MARK = "/* @STYLES"

# Must match firmware/castle_effects.h and tools/gen_esphome.py.


def scene_yaml_slice(text: str, sid: str) -> str:
    """The scene's verbatim block from scenes.yaml, for the source panel."""
    # (?:.*\n)*? — `.*` not `.+`, so blank lines inside a block don't end it.
    m = re.search(rf"^  - id: {sid}\n(?:.*\n)*?(?=^  - id: |\Z)", text, re.MULTILINE)
    return m.group(0).rstrip() if m else f"# (slice for {sid} not found)"


def to_previewer(
    scene: dict[str, Any], idx: int, raw: str, markers: dict[str, Any]
) -> dict[str, Any]:
    sid = scene["id"]
    cues = pc.score_cues(scene)
    cues.extend(pc.hand_cues(scene, sid))
    # EVERY hit, since v5.67. The desk used to thin these to PULSE_CAP — the
    # 200 strongest — because that was all the device could hold: each cue was
    # a compiled ESPHome action with static RAM behind it. A scene is a cue
    # file on the card now (tools/gen_scene_cards.py), the castle plays all
    # 1,200 of a dense track's hits, and a desk that still showed 200 would be
    # the side that was lying. pd.thin_pulses is still the authority on WHICH
    # hits are strongest and is still held byte-equal against core/src/pulse.rs
    # (tests/test_pulse_rust.py); nothing in the show calls it any more.
    cues.extend(pc.pulse_cues(scene, markers))
    cues.sort(key=lambda c: c["t"])

    for eff in scene["base"].values():
        if eff not in KNOWN_EFFECTS:
            sys.exit(f"scene {sid}: unknown base effect {eff!r}")

    return {
        "id": sid,
        "name": scene["name"],
        "kind": scene["kind"] + (" · loops" if scene.get("loop") else ""),
        "dur": scene["duration_ms"],
        "loop": bool(scene.get("loop")),
        "volume": float(scene.get("volume", 0.8)),
        "blurb": " ".join(str(scene.get("blurb", "")).split()),
        "base": scene["base"],
        "levels": scene.get("levels") or {},
        "zones": scene.get("zones") or {},
        "cues": cues,
        "file": f"{idx:02d}_{sid}.mp3",
        # What this scene costs in flash. The budget is the single hardest
        # constraint on the show — ~2.9 MB for everything — so the number
        # belongs next to the scene, not only in the render log.
        "bytes": (AUDIO / f"{idx:02d}_{sid}.mp3").stat().st_size
        if (AUDIO / f"{idx:02d}_{sid}.mp3").exists()
        else 0,
        "yaml": scene_yaml_slice(raw, sid),
    }


# ── The lean page the studio serves ──────────────────────────────────
# The committed build inlines the rendered audio as data URIs so the file is
# portable: open it from disk, publish it as an artifact, copy it to the
# card. Served by the studio that portability buys nothing and costs every
# phone on the LAN ~1.9 MB of base64 it may never play — so the studio serves
# the lean rewrite instead: the same page, each data URI replaced by a
# /studio/scene-audio/<id> link the studio answers with Range support from
# the audio/ directory the page was built from. Same bytes, fetched when
# played. The rewrite happens at serve time, cached by the page's (mtime,
# size), so `make preview` keeps one artefact and one source of truth.
# The route itself lives with the budget that causes it to be used —
# re-exported here because this is where callers have always found it.
AUDIO_ROUTE = pgb.AUDIO_ROUTE
_DATA_URI = re.compile(r'"(\w+)": ?"data:audio/mpeg;base64,[A-Za-z0-9+/=]*"')
_lean_cache: dict[tuple[str, int, int], bytes] = {}


def lean(html: str, route: str = AUDIO_ROUTE, suffix: str = "") -> str:
    """The page with every inlined scene audio swapped for its URL.

    The studio serves `/studio/scene-audio/<id>` (the default); the device
    build gets `/site/<id>.mp3` — sd_sync pushes the files beside the page
    (grade report 2026-08-23 A5/G1), served by the firmware's existing /site/* handler.
    """
    return _DATA_URI.sub(lambda m: f'"{m[1]}": "{route}{m[1]}{suffix}"', html)


def lean_page(page: Path) -> tuple[bytes, str]:
    """(body, etag) of the lean rewrite of `page`, computed once per
    (mtime, size) — the rewrite is one pass over ~2.4 MB, not per request."""
    st = page.stat()
    key = (str(page), st.st_mtime_ns, st.st_size)
    if key not in _lean_cache:
        _lean_cache.clear()
        _lean_cache[key] = lean(page.read_text()).encode()
    return _lean_cache[key], f'"{st.st_mtime_ns}-{st.st_size}-lean"'


def scene_audio(audio_dir: Path, sid: str) -> Path | None:
    """The rendered file for scene `sid` in `audio_dir` (NN_<sid>.mp3), or
    None. The id is matched as a whole name: no separators, no traversal."""
    # \w with re.ASCII, so the class is exactly [A-Za-z0-9_] and not the
    # Unicode half a bare \w would let through a path guard.
    if not re.fullmatch(r"\w+", sid, re.ASCII):
        return None
    return next(iter(sorted(audio_dir.glob(f"[0-9][0-9]_{sid}.mp3"))), None)


def inject_bundle(html: str) -> str:
    """Build web/src with esbuild and splice the result into the page.

    The output has to stay a single self-contained file with no external
    requests: the published artifact runs under a strict CSP, and the plan to
    serve a cut-down copy off the device rules out a CDN as well. So the
    bundle is inlined rather than referenced.
    """
    if not (WEB / "node_modules").exists():
        sys.exit("web/node_modules missing — run `cd web && npm install` first")

    r = subprocess.run(
        # --minify: the page is re-sent on every studio restart and every
        # phone load, and the unminified bundle was a quarter of it. Debug
        # against `npm run watch`'s dist/bundle.js, not the spliced page.
        [
            "npx",
            "esbuild",
            "src/main.ts",
            "--bundle",
            "--minify",
            "--format=iife",
            "--target=es2020",
            "--outfile=dist/bundle.js",
        ],
        cwd=WEB,
        capture_output=True,
        text=True,
        check=False,  # handled below
    )
    if r.returncode != 0:
        sys.exit(f"esbuild failed:\n{r.stdout}\n{r.stderr}")

    js = BUNDLE.read_text()
    i = html.find(BUNDLE_MARK)
    if i < 0:
        sys.exit(f"{BUNDLE_MARK} marker not found in {TEMPLATE}")
    j = html.index("*/", i) + 2
    # Nothing in the bundle may contain a literal </script>; esbuild will not
    # produce one from this source, but check rather than trust.
    if "</script>" in js:
        sys.exit("bundle contains a literal </script> — it would close the tag early")
    return html[:i] + js + html[j:]


def inject_styles(html: str) -> str:
    """Inline previewer/styles.css.

    Split out of the template purely so both files stay inside the 500-line
    cap — markup and styling are a real seam, and the check caught the
    combined file honestly rather than being exempted around.
    """
    i = html.find(STYLE_MARK)
    if i < 0:
        sys.exit(f"{STYLE_MARK} marker not found in {TEMPLATE}")
    j = html.index("*/", i) + 2
    css = (
        STYLES.read_text().rstrip()
        + "\n\n"
        + PANELS.read_text().rstrip()
        + "\n\n"
        + MOBILE.read_text().rstrip()
    )
    return html[:i] + css + html[j:]


def main() -> int:
    raw = SRC.read_text()
    doc = yaml.safe_load(raw)
    markers = scene_schema.load_markers(MARKERS_FILE)
    scenes = [
        to_previewer(s, i, raw, markers) for i, s in enumerate(doc["scenes"], start=1)
    ]

    audio: dict[str, str] = {}
    missing = []
    for sc in scenes:
        mp3 = AUDIO / sc["file"]
        if mp3.exists():
            b64 = base64.b64encode(mp3.read_bytes()).decode()
            audio[sc["id"]] = f"data:audio/mpeg;base64,{b64}"
        else:
            missing.append(sc["file"])
    if missing:
        print(
            f"note: no rendered audio for {missing} — run `make audio` first;"
            " previewer will fall back to live synth for those scenes"
        )

    html = TEMPLATE.read_text()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(html):
        sys.exit(f"markers not found in {TEMPLATE} — expected {START} ... {END}")
    # Styles and bundle first, then the data block: fit_budget re-renders the
    # page once per scene it gives away, and esbuild must not run each time.
    html = inject_styles(html)
    html = inject_bundle(html)

    def page(src: dict[str, str]) -> bytes:
        block = (
            f"{START} (written by tools/gen_previewer.py — do not edit, "
            "do not format)\n"
            f"  window.CASTLE_GEN = "
            f"{json.dumps({'scenes': scenes, 'audio': src})};\n"
            f"  {END}"
        )
        # sub() treats backslashes in the replacement as escapes, and the
        # audio is base64 so it will contain them eventually. Pass a function.
        return pattern.sub(lambda _: block, html).encode()

    # Weighed BEFORE it is written: an over-budget build leaves the last good
    # page in the tree rather than replacing it with the one that failed.
    body, linked = pgb.fit_budget(page, audio, pgb.PAGE_BUDGET_KB * 1024)
    complaint = pgb.enforce_budget(body, audio, HTML)
    if complaint is not None:
        print(complaint, file=sys.stderr)
        return 1

    HTML.parent.mkdir(parents=True, exist_ok=True)
    HTML.write_bytes(body)

    inlined = len(audio) - len(linked)
    kb = sum(len(v) for k, v in audio.items() if k not in set(linked)) // 1024
    total_kb = len(body) // 1024
    print(
        f"wrote {len(scenes)} scenes + {inlined} inlined audio files "
        f"(~{kb} KB base64) into {bp.rel(HTML)} ({total_kb / 1024:.1f} MB "
        f"of the {pgb.PAGE_BUDGET_KB // 1024} MB budget)"
    )
    if linked:
        print(
            f"note: {len(linked)} scene(s) over the budget kept their audio "
            f"OUT of the page — {', '.join(linked)}. They play from "
            f"{AUDIO_ROUTE}<id> under the studio or the castle; opened from "
            "disk with no server, those scenes fall back to the live synth."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
