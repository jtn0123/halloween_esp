#!/usr/bin/env python3
"""Build the one-file Castle Radio page the castle serves from its SD card.

    demo/castle-radio/device_site.py      writes dist/index.html and index.html.gz

The computer build is a directory of files behind server.py. The castle has
one HTTP task, four sockets, no Python and a strict CSP, so the device gets
the same page folded into ONE self-contained HTML document:

  - castle-direct.js first, which answers every /radio/* route from the
    firmware's own /api (see that file's header);
  - the scene catalog and the synced-import library inlined as JSON, so the
    page loads with one request and no computer in the loop;
  - a short list of exact string rewrites: media streams off /sd/scenes/,
    the Google Fonts import goes (the CSP forbids it and the porch has no
    internet), and the sentences that named the computer's server say what
    is true on the castle instead.

Every rewrite is asserted: a source edit that moves one of these strings
fails the build here, not on the porch. tools/sd_sync.py `site` calls
build() and pushes the result gzipped; the firmware serves index.html.gz.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import device_bridge
import remote_library

HERE = Path(__file__).resolve().parent
DATA = HERE / ".radio-data"
INDEX = "index.html"
APP = "app.js"
IMPORTS = "imports.js"
PREVIEW = "preview.js"
WORDS = "device-words.js"
LINK = "device-link.js"
SCRIPTS = (
    "device-helper.js",
    "visuals.js",
    APP,
    IMPORTS,
    PREVIEW,
    "rig-options.js",
    WORDS,
    LINK,
    "remote-library.js",
    "device-tools.js",
    "desktop-tools.js",
)
STYLES = ("style.css", "device-tools.css")
STYLE_TAGS = "".join(f'<link rel="stylesheet" href="{name}">' for name in STYLES)
SCRIPT_TAGS = "".join(f'<script src="{name}"></script>' for name in SCRIPTS)
FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700"
    "&family=Manrope:wght@400;500;600;700;800&display=swap');"
)
PROBE = (
    "tracks.forEach(t=>{const probe=new Audio();probe.preload='metadata';"
    "probe.src=`media/${t.file}`;probe.onloadedmetadata=()=>{if(!Number.isFinite(probe.duration)){return;}t.duration=probe.duration;"
    "renderTracks();};});"
)
DURATIONS = (
    "tracks.forEach(t=>{const scene=window.castleDirect.scenes.find(s=>s.file===t.file);"
    "if(scene){t.duration=scene.dur/1000;}});renderTracks();"
)
# (file, computer text, castle text) — each must occur exactly once.
REWRITES: tuple[tuple[str, str, str], ...] = (
    ("style.css", FONT_IMPORT, ""),
    # load() itself refuses to give the audio element a source while the
    # castle is the output (Chromium asks for the file even at preload=none,
    # and one such stream holds the castle's single HTTP task for seconds),
    # so all this build has to move is where the browser branch reads from.
    (
        APP,
        "setAudioSource(tracks[id].url||`media/${tracks[id].file}`);",
        "setAudioSource(tracks[id].url||`/sd/scenes/${tracks[id].file}`);",
    ),
    (APP, PROBE, DURATIONS),
    (PREVIEW, "`media/${t.file}`", "`/sd/scenes/${t.file}`"),
    (
        PREVIEW,
        "'Waveform unavailable. Reopen this song to retry.'",
        "'Connect Mac tools to view waveforms, then reopen this song.'",
    ),
    (WORDS, "'Castle unreachable at 10.27.27.81'", "'Castle unreachable'"),
    (
        WORDS,
        "'Control room server is not running'",
        "'Castle not answering'",
    ),
    (
        "device-tools.js",
        "'Sending command to 10.27.27.81'",
        "'Sending command to the castle'",
    ),
    (
        IMPORTS,
        "'Import service ready · files stay in this demo'",
        "(window.castleDesktop?.connected ? 'Mac tools connected · imports are prepared on your Mac' : 'Castle library ready · connect Mac tools to import')",
    ),
    (
        IMPORTS,
        "'Import service unavailable. Start server.py to import songs.'",
        "'Castle library unavailable · retrying'",
    ),
    (
        INDEX,
        '<option value="computer">This computer</option>',
        '<option value="computer">This browser</option>',
    ),
    # The firmware streams a whole file per request (no Range) through the
    # one task that also answers /api/status. A metadata preload of the
    # first song is 2.3 MB and eight seconds of "Castle offline" on every
    # page open. Nothing is fetched until Play in the browser is pressed.
    (
        INDEX,
        '<audio id="audio" preload="metadata">',
        '<audio id="audio" preload="none">',
    ),
)


def rewritten(name: str, text: str) -> str:
    for file, old, new in REWRITES:
        if file != name:
            continue
        if text.count(old) != 1:
            raise SystemExit(f"{name}: expected exactly one {old[:60]!r}")
        text = text.replace(old, new)
    return text


def inline_json(payload: object) -> str:
    """JSON that is safe inside a <script> element."""
    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


def inline_script(text: str) -> str:
    if "</script" in text:
        raise SystemExit("a script closes its own tag; refusing to inline it")
    return text


def catalog_rows(data: Path) -> list[dict]:
    """The imported songs as the castle page lists them: only what can play
    from the card (audio name and size, so the page can check the listing)
    and its lights reduced to the mailbox-rate frames the bridge would have
    streamed — not the raw cues, which run to hundreds of kilobytes."""
    path = data / "catalog.json"
    if not path.exists():
        return []
    rows: list[dict] = []
    for row in json.loads(path.read_text()):
        audio = remote_library.playback_path(data / "tracks", row)
        if audio is None:
            continue
        rows.append(
            {
                "key": row["key"],
                "title": row.get("title", row["key"]),
                "artist": row.get("artist", "Your imports"),
                "style": row.get("style", "Auto rhythm"),
                "duration": row.get("duration", 0),
                "split": False,
                "split_error": None,
                "source_kind": "castle",
                "source_label": "synced from the control room",
                "source_available": False,
                "playback_format": row.get("playback_format", audio.suffix[1:]),
                "playback_bitrate": row.get("playback_bitrate"),
                "playback_bytes": audio.stat().st_size,
                "url": f"/sd/{audio.name}",
                "filename": audio.name,
                "bytes": audio.stat().st_size,
                "frames": device_bridge.imported_light_frames(row.get("cues", [])),
            }
        )
    return rows


def scene_rows(root: Path) -> list[dict]:
    """scenes.json without the YAML source the light studio shows."""
    rows = json.loads((root / "scenes.json").read_text())
    return [{k: v for k, v in row.items() if k != "yaml"} for row in rows]


def build(root: Path = HERE, data: Path = DATA) -> bytes:
    page = rewritten(INDEX, (root / INDEX).read_text())
    if page.count(STYLE_TAGS) != 1 or page.count(SCRIPT_TAGS) != 1:
        raise SystemExit(f"{INDEX} no longer links its styles and scripts as expected")
    styles = "".join(
        f"<style>{rewritten(name, (root / name).read_text())}</style>"
        for name in STYLES
    )
    data_tags = (
        f'<script id="radio-scenes" type="application/json">{inline_json(scene_rows(root))}</script>'
        f'<script id="radio-library" type="application/json">{inline_json(catalog_rows(data))}</script>'
    )
    scripts = "".join(
        f"<script>{inline_script(rewritten(name, (root / name).read_text()))}</script>"
        for name in ("castle-direct.js", *SCRIPTS)
    )
    page = page.replace(STYLE_TAGS, styles).replace(SCRIPT_TAGS, data_tags + scripts)
    for token in ('src="', 'href="http', "@import"):
        if token in page:
            raise SystemExit(f"the castle page must be self-contained; found {token!r}")
    return page.encode()


def main() -> int:
    out = HERE / "dist" / INDEX
    out.parent.mkdir(parents=True, exist_ok=True)
    plain = build()
    out.write_bytes(plain)
    packed = gzip.compress(plain, 9)
    out.with_suffix(".html.gz").write_bytes(packed)
    print(f"{out} ({len(plain) // 1024} KB, {len(packed) // 1024} KB gzipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
