"""Build the silent light-show lab: choreography candidates + one local page.

    .venv/bin/python demo/castle-radio/show_lab.py            # both steps
    .venv/bin/python demo/castle-radio/lab_server.py      # then /show-lab.html
    .venv/bin/python demo/castle-radio/show_lab.py --notes  # the flags made there

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import harmony
import voice_kinds
from choreography import STYLES, choreograph
from colour_show import choreograph_colour
from ensemble_show import choreograph_ensemble
from lab_server import NOTES as NOTES_FILE
from lab_server import report
from palette_show import choreograph_palette
from pulse_clarity import encode_preview
from render_cues import waveform
from rich_show import build, cue_file, preview_from_blob
from sections_show import choreograph_sections
from sections_v2 import choreograph_sections_v2
from spectrum_glow import choreograph_spectrum_glow
from spectrum_show import choreograph_spectrum
from spin_show import choreograph_spin
from voice_pitch import Pitch, track

HERE = Path(__file__).resolve().parent
LIBRARY = HERE / ".radio-data" / "tracks"
OUTPUT = HERE / ".radio-data" / "comparison"
DOWNLOADS = Path.home() / "Downloads"
PAGE = "show-lab.html"
SHOW = ".show.json"
BASELINE = ".baseline"  # the lab's own prepared show, for a song never prepared
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
    "sections": "Dressed by the song's structure: one look per kind of passage, "
    "held for the whole section, so a chorus comes back looking like itself. "
    "Hits follow how hard each beat is played; the towers' glow follows the "
    "band. Louder sections get a rising build, a colour swell, a beat of dark "
    "and a white slam; the band stopping dead goes dark but for the singer. "
    "Held notes glow on the door. Written for v5.70's castle with Soften on.",
    "sections2": "Option 9 for the NEW firmware (v5.71, cue format v2), which "
    "a v5.70 castle refuses — it would play the song dark. The singer is on "
    "the second light layer, so a sung note and a band hit add up instead of "
    "cutting each other off; the towers' chase turns once a bar on the beat "
    "(a slow meteor in quiet looks); hand-overs and swells use the door's "
    "halves. No Soften compensation: v5.71 only softens real strobes.",
    "spin": "Option 10 in motion (v5.71 with arcs). The castle still lands "
    "together on every bar's 'one'; between those the lights travel. Each sung "
    "note lights one arc of the door and the arc walks with the melody — "
    "clockwise as the tune climbs, back as it falls — so a sung line circles "
    "the door; held notes spin a chase round it, high notes burn whiter. Tower "
    "hits land where the tempo-locked chase is, a light races across the castle "
    "at each section change, and builds spin faster into the drop.",
    "ensemble": "Option 11 with every part of the band and every kind of voice "
    "given its own light. Drums as three instruments: kick low on the towers, "
    "snare cracking round them with the chase, hi-hats as faint sparkles. Each "
    "bar's chord colours the band against the song's key — home in the look's "
    "colours, other chords answering, minor chords colder, chords from outside "
    "the key a sickly green — and the chase changes palette with them. Spoken "
    "lines turn the door into watching eyes; a choir lights the towers too; "
    "vibrato shimmers; a bright voice burns whiter, a dark one deeper. A slow "
    "start is lit by candles, and the ending matches the song's: a cold stop to "
    "black, a fade with the band, or a last chord left to ring.",
    "colour": "Option 12 with more colour and no more chaos. Every look answers "
    "its first colour with one from across the wheel and has a third, accent "
    "colour; white is kept for drops, stops, the finale and each chorus's first "
    "hit. The race hands a new section's colour across the castle — each part "
    "keeps a glow of it, and the chase changes palette as the light passes. A "
    "section that comes back leads with its answering colour, the final chorus "
    "answers in the accent, the singer's notes are paler the higher they go and "
    "deeper the lower, and no one colour family holds more than 45% of a song.",
    "palette": "Option 13 with the castle's resting light in colour too: the hits "
    "are where they were, but between them the towers and door glow in palettes "
    "instead of fixed reds and ambers. The door wears its own palette, apart "
    "from the towers' and changing section by section, and a long section's "
    "towers turn to a second palette every other phrase. Spoken lines still "
    "turn the door to eyes.",
    "spectrum": "Option 14 with colours from the whole wheel instead of ten named "
    "ones. Every kind of passage gets its own hue — the song's own starting "
    "point plus the golden angle, so neighbouring kinds never sit together — "
    "quiet passages in neighbouring hues, loud ones in opposing hues, the "
    "singer on a third, paler one. A chorus keeps its colours when it comes "
    "back, a little turned, and every hit is its theme's shade by a few "
    "degrees. The resting glow takes the nearest of the castle's four palettes.",
    "glow": "PREVIEW ONLY — the castle cannot show this yet. Option 15 with the "
    "resting glow in the section's exact colours instead of the nearest of the "
    "castle's four palettes: the towers glow in the section's first colour "
    "drifting to its answer, the door the other way round, a chord's mood "
    "leans them toward deep blue or sickly green, and the race hands each "
    "tower the next section's glow as it passes. Compare it with 15.",
}


def analysis_for(library: Path, output: Path, key: str) -> Path | None:
    """The lab's own four-stem analysis when there is one, else the library's."""
    for path in (output / "stems" / key, library / "stems" / key):
        if (path / "analysis.json").is_file():
            return path / "analysis.json"
    return None


def baseline_for(library: Path, output: Path, key: str) -> Path | None:
    """The prepared show in the library, else one the lab prepares for itself
    — the same rich_show.build the Radio runs, written only to `output`."""
    prepared = library / (key + SHOW)
    if prepared.is_file():
        return prepared
    audio = next(
        (
            library / (key + s)
            for s in AUDIO_SUFFIXES
            if (library / (key + s)).is_file()
        ),
        None,
    )
    own = library / "stems" / key / "analysis.json"
    if audio is None or not own.is_file():
        return None
    target = output / (key + BASELINE + SHOW)
    if not target.is_file():
        layers = json.loads(own.read_text(encoding="utf-8"))["layers"]
        _blob, preview = build(key, waveform(audio, 1.1), layers, audio.suffix[1:])
        titles = library / "tracks.json"
        rows = (
            json.loads(titles.read_text(encoding="utf-8")) if titles.is_file() else {}
        )
        preview["name"] = (rows.get(key) or {}).get("title", key)
        target.write_text(json.dumps(preview), encoding="utf-8")
    return target


def songs(library: Path, output: Path) -> list[tuple[str, Path, Path]]:
    """(key, baseline show, analysis) for every song the lab can dress."""
    out = []
    stems = library / "stems"
    for folder in sorted(stems.iterdir()) if stems.is_dir() else []:
        analysis = analysis_for(library, output, folder.name)
        baseline = baseline_for(library, output, folder.name) if analysis else None
        if analysis and baseline:
            out.append((folder.name, baseline, analysis))
    return out


def pitch_for(analysis: Path, output: Path, key: str) -> Pitch | None:
    """The singer's pitch track, cached in the lab's own directory."""
    vocals = analysis.parent / "vocals.mp3"
    if not vocals.is_file():
        return None
    return track(vocals, output / f"{key}.pitch.json")


def ensemble_for(
    source: dict[str, Any], layers: dict[str, Any], analysis: Path, output: Path,
    key: str, choreographer: Callable[..., dict[str, Any]] = choreograph_ensemble,
) -> dict[str, Any]:  # fmt: skip
    """Option 12 (or 13-16, by `choreographer`), with the stems' voice and
    harmony analyses cached in the lab's own directory."""
    stems = analysis.parent
    vocals = stems / "vocals.mp3"
    voice = (
        voice_kinds.track(vocals, output / f"{key}.voice.json")
        if vocals.is_file()
        else None
    )
    chroma = harmony.track(stems, output / f"{key}.harmony.json")
    pitch = pitch_for(analysis, output, key)
    return choreographer(source, layers, pitch, voice, chroma)


def _write(output: Path, key: str, style_id: str, planned: dict[str, Any],
           name: str, label: str) -> dict[str, Any]:  # fmt: skip
    blob = encode_preview(planned)
    decoded = preview_from_blob(key, blob)
    decoded["name"] = name
    # A preview-only candidate's glow (spectrum_glow.py): drawn by the page,
    # never on the card, so it joins the cues after the card is decoded.
    decoded["cues"] = sorted([*decoded["cues"], *planned.get("preview", [])],
                             key=lambda c: c["t"])  # fmt: skip
    # The timeline the page draws; not part of the card bytes.
    decoded["lab"] = {
        "style": label, "note": NOTES.get(style_id, ""),
        "bpm": planned["bpm"], "beats": planned["beats"],
        "sections": planned["sections"], "breaks": planned.get("breaks", []),
        # Which castle the show is written for; the page renders it on that one.
        "firmware": planned.get("firmware", "v5.70"),
        "version": cue_file.decode(blob)["version"],
    }  # fmt: skip
    (output / f"{key}.{style_id}.cue").write_bytes(blob)
    (output / f"{key}.{style_id}{SHOW}").write_text(
        json.dumps(decoded), encoding="utf-8"
    )
    return {"song": name, "style": style_id, "bpm": planned["bpm"],
            "cues": len(decoded["cues"]), "crc32": decoded["cue_crc32"],
            **planned.get("story", {})}  # fmt: skip


def candidates(library: Path, output: Path) -> list[dict[str, Any]]:
    if output.resolve() == library.resolve():
        raise ValueError("An experiment cannot be written beside its baseline")
    output.mkdir(parents=True, exist_ok=True)
    report = []
    for key, show, analysis in songs(library, output):
        source = json.loads(show.read_text(encoding="utf-8"))
        layers = json.loads(analysis.read_text(encoding="utf-8"))["layers"]
        for style_id, style in STYLES.items():
            planned = choreograph(source, layers, style)
            report.append(
                _write(output, key, style_id, planned, source["name"], style.name)
            )
        planned = choreograph_sections(source, layers)
        report.append(
            _write(output, key, "sections", planned, source["name"], "Sections")
        )
        planned = choreograph_sections_v2(source, layers)
        report.append(
            _write(output, key, "sections2", planned, source["name"],
                   "Sections, new firmware")
        )  # fmt: skip
        planned = choreograph_spin(source, layers, pitch_for(analysis, output, key))
        report.append(_write(output, key, "spin", planned, source["name"], "Spin"))
        planned = ensemble_for(source, layers, analysis, output, key)
        report.append(
            _write(output, key, "ensemble", planned, source["name"], "Ensemble")
        )
        planned = ensemble_for(
            source, layers, analysis, output, key, choreograph_colour
        )
        report.append(_write(output, key, "colour", planned, source["name"], "Colour"))
        planned = ensemble_for(
            source, layers, analysis, output, key, choreograph_palette
        )
        report.append(
            _write(output, key, "palette", planned, source["name"], "Palette")
        )
        planned = ensemble_for(
            source, layers, analysis, output, key, choreograph_spectrum
        )
        report.append(
            _write(output, key, "spectrum", planned, source["name"], "Spectrum")
        )
        planned = ensemble_for(
            source, layers, analysis, output, key, choreograph_spectrum_glow
        )
        report.append(_write(output, key, "glow", planned, source["name"], "Glow"))
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
    rows = []
    for key, show, _analysis in songs(library, output):
        others = [
            p for p in sorted(output.glob(f"{key}.*{SHOW}"))
            if not p.name.endswith(BASELINE + SHOW)
        ]  # fmt: skip
        if not others:
            continue
        rows.append(
            {
                "baseline": json.loads(show.read_text(encoding="utf-8")),
                "audio": link_audio(library, output, key),
                "candidates": [
                    {
                        "id": p.name.removesuffix(SHOW).removeprefix(key + "."),
                        "show": json.loads(p.read_text(encoding="utf-8")),
                    }
                    for p in others
                ],
            }
        )
    html = (HERE / "show-lab.template.html").read_text(encoding="utf-8")
    for name in ("visuals.js", "cue-playback.js", "lab-leds.js",
                 "show-lab-leds.js", "show-lab-blind.js",
                 "show-lab-flags.js"):  # fmt: skip
        html = html.replace(
            f"/*{{{{{name}}}}}*/", (HERE / name).read_text(encoding="utf-8")
        )
    data = json.dumps(rows, separators=(",", ":")).replace("</", "<\\/")
    target = output / PAGE
    target.write_text(html.replace("/*{{songs}}*/null", data), encoding="utf-8")
    return target


def verdicts(path: Path) -> list[str]:
    """What a downloaded blind-test file says: per show, how often it was
    picked over the one beside it, and against which."""
    wins: dict[str, list[int]] = {}
    for pick in json.loads(path.read_text(encoding="utf-8")):
        a, b, verdict = pick["a"], pick["b"], pick["verdict"]
        for show in (a, b):
            wins.setdefault(show, [0, 0, 0])
        if verdict == "same":
            wins[a][1] += 1
            wins[b][1] += 1
        else:
            winner, loser = (a, b) if verdict == "a" else (b, a)
            wins[winner][0] += 1
            wins[loser][2] += 1
    rows = sorted(
        wins.items(), key=lambda kv: -(kv[1][0] + 0.5 * kv[1][1]) / max(1, sum(kv[1]))
    )
    return [
        f"{show:>10}  picked {w:3}  tied {t:3}  passed over {lost:3}"
        f"  ({(w + 0.5 * t) / max(1, w + t + lost):.0%})"
        for show, (w, t, lost) in rows
    ]


def latest_verdicts(folder: Path) -> Path | None:
    """The newest blind-test file the page downloaded into `folder`. A browser
    names a second copy "castle-lab-verdicts (1).json", so any of them."""
    found = sorted(
        folder.glob("castle-lab-verdicts*.json"), key=lambda p: p.stat().st_mtime
    )
    return found[-1] if found else None


def main() -> int:
    """Always the Radio's own library and comparison directory, and no path
    from a command line at all: nothing typed can point the lab's reads or
    writes elsewhere. `--verdicts` reads the newest blind-test file in
    ~/Downloads, where the page's "Download my picks" puts it, and `--notes`
    only prints the flags the page sent the lab server."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verdicts",
        action="store_true",
        help="summarise the newest downloaded blind test",
    )
    parser.add_argument("--notes", action="store_true", help="print the flags")
    args = parser.parse_args()
    if args.notes:
        print("\n".join(report(OUTPUT / NOTES_FILE)))
        return 0
    if args.verdicts:
        found = latest_verdicts(DOWNLOADS)
        if found is None:
            print(f"no castle-lab-verdicts*.json in {DOWNLOADS}")
            return 1
        print("\n".join([str(found), *verdicts(found)]))
        return 0
    for row in candidates(LIBRARY, OUTPUT):
        print(json.dumps(row))
    target = page(LIBRARY, OUTPUT)
    print(f"{target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
