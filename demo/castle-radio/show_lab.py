"""Build the silent light-show lab: choreography candidates + one local page.

    .venv/bin/python demo/castle-radio/show_lab.py            # both steps
    python3 -m http.server 8894 --bind 127.0.0.1 \\
        --directory demo/castle-radio/.radio-data/comparison  # then /show-lab.html

Reads the prepared baselines and stem analyses in the library; writes ONLY to
the output directory, never beside a baseline. The page inlines the Radio's
own renderer (visuals.js) and card cue semantics (cue-playback.js) and plays
every show live at the simulation's 16 ms tick — no device. It is silent until
"Play the song" is ticked; the song it then plays is a symlink in the output's
audio/ directory, heard in that browser only. Every candidate preview is
decoded from its encoded cue bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from choreography import STYLES, choreograph
from pulse_clarity import encode_preview
from rich_show import preview_from_blob

HERE = Path(__file__).resolve().parent
LIBRARY = HERE / ".radio-data" / "tracks"
OUTPUT = HERE / ".radio-data" / "comparison"
PAGE = "show-lab.html"
SHOW = ".show.json"
AUDIO_SUFFIXES = (".mp3", ".opus", ".wav")
NOTES = {
    "beat": "Towers trade the beat left-right, the whole castle lands on every "
    "'one', and the look (base effect + colours) changes every 4 bars. "
    "The door follows the singer between hits.",
    "motion": "A sweep runs tower → door → tower inside every beat and turns "
    "round each bar; the tower rings rotate. Instrument fills stay as sparkles.",
    "show": "Everything together: the pattern is chosen per phrase by how loud "
    "the passage is (breathe, heartbeat, ping-pong, sweep, stomp), and before "
    "the song lifts there is a climbing roll, half a beat of darkness, and a "
    "white slam.",
    "duet": "Option 7 with the stems split cleanly: while there is singing the "
    "door belongs to the singer alone (whole ring, in the look's voice colour) "
    "and the band's beat stays on the two towers. In instrumental passages the "
    "door rejoins the band. Drops and the finale still take the whole castle.",
}


def candidates(library: Path, output: Path) -> list[dict[str, Any]]:
    if output.resolve() == library.resolve():
        raise ValueError("An experiment cannot be written beside its baseline")
    output.mkdir(parents=True, exist_ok=True)
    report = []
    for show in sorted(library.glob("*" + SHOW)):
        key = show.name.removesuffix(SHOW)
        analysis = library / "stems" / key / "analysis.json"
        if not analysis.is_file():
            continue
        source = json.loads(show.read_text())
        layers = json.loads(analysis.read_text())["layers"]
        for style_id, style in STYLES.items():
            planned = choreograph(source, layers, style)
            blob = encode_preview(planned)
            decoded = preview_from_blob(key, blob)
            decoded["name"] = source["name"]
            # The timeline the page draws; not part of the card bytes.
            decoded["lab"] = {
                "style": style.name, "note": NOTES.get(style_id, ""),
                "bpm": planned["bpm"], "beats": planned["beats"],
                "sections": planned["sections"],
            }  # fmt: skip
            (output / f"{key}.{style_id}.cue").write_bytes(blob)
            (output / f"{key}.{style_id}{SHOW}").write_text(json.dumps(decoded))
            report.append(
                {"song": source["name"], "style": style_id, "bpm": planned["bpm"],
                 "cues": len(decoded["cues"]), "crc32": decoded["cue_crc32"]}
            )  # fmt: skip
    return report


def link_audio(library: Path, output: Path, key: str) -> str | None:
    """The song, as a link the static server can follow; the library is only read."""
    source = next(
        (
            library / (key + s)
            for s in AUDIO_SUFFIXES
            if (library / (key + s)).is_file()
        ),
        None,
    )
    if source is None:
        return None
    link = output / "audio" / source.name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(source.resolve())
    return f"audio/{source.name}"


def page(library: Path, output: Path) -> Path:
    songs = []
    for show in sorted(library.glob("*" + SHOW)):
        key = show.name.removesuffix(SHOW)
        others = sorted(output.glob(f"{key}.*{SHOW}"))
        if not others:
            continue
        songs.append(
            {
                "baseline": json.loads(show.read_text()),
                "audio": link_audio(library, output, key),
                "candidates": [
                    {
                        "id": p.name.removesuffix(SHOW).removeprefix(key + "."),
                        "show": json.loads(p.read_text()),
                    }
                    for p in others
                ],
            }
        )
    html = (HERE / "show-lab.template.html").read_text()
    for name in ("visuals.js", "cue-playback.js"):
        html = html.replace(f"/*{{{{{name}}}}}*/", (HERE / name).read_text())
    data = json.dumps(songs, separators=(",", ":")).replace("</", "<\\/")
    target = output / PAGE
    target.write_text(html.replace("/*{{songs}}*/null", data))
    return target


def main() -> int:
    """Always the Radio's own library and comparison directory: the lab takes
    no paths, so nothing on a command line can point its writes elsewhere."""
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    for row in candidates(LIBRARY, OUTPUT):
        print(json.dumps(row))
    target = page(LIBRARY, OUTPUT)
    print(f"{target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
