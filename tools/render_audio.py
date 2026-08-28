#!/usr/bin/env python3
"""Render each scene in scenes.yaml to a single pre-mixed audio file.

The MAX98357A plays one stream, and mixing on a single-core ESP32-S2 is not
worth fighting. So every scene becomes one file: all the layering, ducking and
reverb happens here, where CPU is free.

    tools/render_audio.py            # render everything to audio/
    tools/render_audio.py --wav      # keep the intermediate WAVs too
    tools/render_audio.py --only storm

Output is mono at the bitrate set in scenes.yaml, sized to fit the flash left
over after the ESPHome image.
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import subprocess
import sys
import wave
import zlib
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import analyze
import build_paths as bp
import manifest as mf
import synth

ROOT = Path(__file__).resolve().parent.parent
# Both redirectable (build_paths.py): a sandboxed studio renders beside its
# own scenes file, never into the repo's audio/.
SCENES = bp.SCENES
OUT = bp.AUDIO


def card_dir() -> Path:
    """The SD build's copies, rendered at card_bitrate; sd_sync pushes these.
    Derived from OUT at call time, not import time, so a redirected OUT
    (tests, a sandboxed studio) carries the card copies with it — a fixed
    path here once let a test's stale-sweep delete the real render."""
    return OUT / "card"


TARGET_PEAK = 0.89  # leaves headroom for the MP3 encoder's overshoot


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def scene_spec(scene: dict, cfg: dict, out: Path) -> dict:
    """One scene as castle-core's scene_render bin takes it: every
    scenes.yaml default applied, the imported song resolved to a path.

    The missing-song story is orchestration, so it lives here, not in the
    crate: an absent file the manifest knows renders without its song
    (NOT_HERE names it in the summary), an absent file nobody imported is
    a typo and stops the render — same sentences as render_scene_py.
    """
    track = scene.get("audio_file")
    if track:
        path = bp.track_source(track)
        if not path.exists():
            if not known_track(track):
                raise SystemExit(
                    f"scene {scene['id']}: no such audio_file "
                    f"{track} — not on disk, and no record of it "
                    f"in tracks/tracks.json either"
                )
            NOT_HERE.append(scene["id"])
            track = None
    spec: dict = {
        "id": scene["id"],
        "duration_ms": scene["duration_ms"],
        "sample_rate": cfg["sample_rate"],
        "wet": float(scene.get("reverb", 0.0 if track else 0.42)),
        "loop": bool(scene.get("loop")),
        "out": str(out),
        "score": scene.get("score") or [],
    }
    if track:
        spec["track"] = {
            "path": str(bp.track_source(track)),
            "gain": float(scene.get("track_gain", 1.0)),
            "at": float(scene.get("track_at", 0.0)),
            "sensitivity": scene.get("sensitivity", 1.1),
        }
    return spec


@functools.cache
def scene_render_bin() -> Path:
    """castle-core's scene_render, rebuilt when cargo is here to do it —
    once per run, not once per scene (the cache).

    The crate IS the renderer (its CANONICAL float profile makes a scene
    the same bytes on every machine); no binary and no cargo is a hard
    stop, never a silent fall-back to a machine-dependent render.
    """
    exe = ROOT / "core" / "target" / "release" / "scene_render"
    cargo = shutil.which("cargo")
    if cargo:
        subprocess.run(
            [
                cargo,
                "build",
                "--release",
                "--quiet",
                "--bin",
                "scene_render",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            check=True,
        )
    if not exe.exists():
        raise SystemExit(
            "core/target/release/scene_render is missing and there is no "
            "cargo to build it — install rust, or build the binary "
            "elsewhere (cd core && cargo build --release)"
        )
    return exe


def render_scene(scene: dict, cfg: dict, wav: Path) -> dict[str, list]:
    """Render one scene through the crate: the bin writes the WAV where
    `wav` says and answers the beat markers. render_scene_py below is the
    same render in Python, kept as the parity reference —
    tests/test_scene_render_rust.py holds the two byte-equal."""
    run = subprocess.run(
        [str(scene_render_bin())],
        input=json.dumps(scene_spec(scene, cfg, wav)).encode(),
        capture_output=True,
        check=False,
    )
    if run.returncode != 0:
        raise SystemExit(
            run.stderr.decode().strip() or f"scene_render failed on {scene['id']}"
        )
    markers: dict[str, list] = json.loads(run.stdout)
    return markers


def render_scene_py(scene: dict, cfg: dict) -> tuple[np.ndarray, dict[str, list]]:
    """Mix one scene's score into a single buffer.

    Also returns beat markers, keyed by synth name: the ACTUAL musical event
    times AND loudness reported by the synths — both heartbeat thumps, each
    whispered word, organ chord onsets, waltz downbeats. Each marker is
    [ms, velocity]. The cue generators turn these into light pulses, so light
    stays locked to sound even when a synth jitters its own timing, and a
    scene can give each sound its own colour and decay.
    """
    sr = cfg["sample_rate"]
    dur = scene["duration_ms"] / 1000.0
    buf = np.zeros(int(dur * sr))
    markers: dict[str, list] = {}

    # Deterministic per scene, so re-rendering is reproducible and a diff in
    # the output means a real change in the score. NOT Python's hash() — that
    # is salted per process, which would re-randomise every render.
    rng = np.random.default_rng(zlib.crc32(scene["id"].encode()))

    # A scene can be built on an imported file instead of (or as well as) the
    # synths. Nobody tells us where an outside track's beats are, so we detect
    # them — and the detected onsets are reported under the same marker names
    # the synths use, so `pulse:` streams work identically either way.
    track = scene.get("audio_file")
    if track:
        path = bp.track_source(track)
        if not path.exists():
            # tracks/ is gitignored — the music is the user's — so a fresh
            # clone or CI has the scene but not its song. That is a fact about
            # the machine, and the rest of the scene (synth score, cue times,
            # length) still renders, which keeps every downstream step honest:
            # the mp3 exists, the flash build embeds it, the show is the right
            # shape. An audio_file nobody ever imported is a typo, and still
            # stops the render — tracks/tracks.json is what tells them apart.
            if not known_track(track):
                raise SystemExit(
                    f"scene {scene['id']}: no such audio_file "
                    f"{track} — not on disk, and no record of it "
                    f"in tracks/tracks.json either"
                )
            NOT_HERE.append(scene["id"])
            track = None
    if track:
        x = analyze.load_audio(path, sr)
        gain = float(scene.get("track_gain", 1.0))
        synth._place(buf, x * gain, float(scene.get("track_at", 0.0)))
        for band, hits in analyze.analyze_full(
            # A scalar, or a per-band map ({low: 0.8, mid: 1.1, high: 1.6})
            # as the clip editor writes it. Coercing to float here would
            # have thrown on the map and, worse, silently ignored it if it
            # had not — the render must detect the same onsets the editor
            # showed, or the tuning was for nothing.
            x,
            sr,
            sensitivity=scene.get("sensitivity", 1.1),
            # Onsets gain a pan third element; markers grow with them,
            # and the pulse expansions route decisive pans by tower.
            stereo=analyze.load_stereo(path, sr),
        ).items():
            markers.setdefault(band, []).extend(
                [int((h[0] + scene.get("track_at", 0.0)) * 1000), *h[1:]]
                for h in hits
                if h[0] < dur - 0.1
            )

    for ev in scene.get("score") or []:
        name = ev["synth"]
        fn = synth.SYNTHS.get(name)
        if fn is None:
            raise SystemExit(f"scene {scene['id']}: unknown synth {name!r}")
        res = fn(rng, dur=ev["dur"]) if "dur" in ev else fn(rng)
        sig, raw_marks = res if isinstance(res, tuple) else (res, [])
        # A synth may report bare times or (time, velocity) pairs.
        marks: list[tuple[float, float]] = [
            m if isinstance(m, tuple) else (m, 1.0) for m in raw_marks
        ]
        if "take" in ev:  # trim a long piece to fit
            sig = sig[: int(ev["take"] * sr)]
            fade = min(len(sig), int(0.4 * sr))
            if fade:
                sig[-fade:] *= np.linspace(1.0, 0.0, fade)
            marks = [(m, v) for m, v in marks if m < ev["take"]]
        synth._place(buf, sig * float(ev.get("gain", 1.0)), ev["t"])
        markers.setdefault(name, []).extend(
            [int((ev["t"] + m) * 1000), round(v, 3)]
            for m, v in marks
            if ev["t"] + m < dur - 0.1
        )

    # Imported tracks arrive already produced — adding the stone hall on top
    # of someone else's reverb just makes mud. Scenes can override either way.
    wet = float(scene.get("reverb", 0.0 if track else 0.42))
    buf = synth.apply_reverb(buf, wet=wet, rng=rng)

    if scene.get("loop"):
        # Crossfade the tail into the head so the loop point is inaudible.
        xf = min(int(0.6 * sr), len(buf) // 4)
        if xf > 0:
            head = buf[:xf].copy()
            ramp = np.linspace(0.0, 1.0, xf)
            buf[-xf:] = buf[-xf:] * (1.0 - ramp) + head * ramp
    else:
        fade = min(int(0.25 * sr), len(buf))
        buf[-fade:] *= np.linspace(1.0, 0.0, fade)

    buf = synth.limit(buf)

    # Normalise every scene to the same peak. Files should use the full 16-bit
    # range — quiet material stored quietly just sits closer to the DAC noise
    # floor for no benefit. Relative loudness between scenes is a playback
    # concern, set per scene by `volume` in scenes.yaml.
    peak = float(np.max(np.abs(buf)))
    if peak > 1e-6:
        buf *= TARGET_PEAK / peak
    return buf, {k: sorted(v) for k, v in markers.items()}


def card_bitrate(cfg: dict) -> int:
    """What the SD build streams. Absent means "same as flash" — the card
    copies are then redundant and never written."""
    return int(cfg.get("card_bitrate", cfg["bitrate"]))


def encode_mp3(wav: Path, mp3: Path, bitrate: int) -> None:
    subprocess.run(
        [
            "lame",
            "--quiet",
            "-m",
            "m",
            "-b",
            str(bitrate),
            "--resample",
            "44.1",
            str(wav),
            str(mp3),
        ],
        check=True,
    )


#: The speaker test's tones: (stem, seconds). The desk's 🏰 panel plays them
#: off the card root (/api/play takes one path component); sd_sync `tones`
#: pushes them. Each answers one question — see device_panel.ts TONES.
TEST_TONES = (
    ("test_sweep", 12.0),
    ("test_1k", 8.0),
    ("test_200", 8.0),
    ("test_4k", 8.0),
    ("test_silence", 6.0),
)


def test_dir() -> Path:
    """Where the tones land; derived from OUT at call time like card_dir()."""
    return OUT / "test"


def render_test_tones(cfg: dict) -> None:
    """audio/test/test_*.mp3 at -6 dBFS, 128 kbps — clean enough that what
    you hear is the amps and the rail, never the codec. The sweep is the
    log run the porch was diagnosed with (200 Hz-10 kHz, 2026-08-22)."""
    sr = cfg["sample_rate"]
    test_dir().mkdir(parents=True, exist_ok=True)
    for stem, secs in TEST_TONES:
        t = np.arange(int(secs * sr)) / sr
        if stem == "test_sweep":
            k = np.log(10000.0 / 200.0)
            x = 0.5 * np.sin(2 * np.pi * 200.0 * secs / k * (np.exp(t * k / secs) - 1))
        elif stem == "test_silence":
            x = np.zeros_like(t)
        else:
            x = 0.5 * np.sin(
                2 * np.pi * float(stem.split("_")[1].replace("k", "000")) * t
            )
        wav = test_dir() / f"{stem}.wav"
        write_wav(wav, x, sr)
        encode_mp3(wav, test_dir() / f"{stem}.mp3", 128)
        wav.unlink()
    print(
        f"speaker test: {len(TEST_TONES)} tones -> {test_dir().relative_to(OUT.parent)}/"
    )


def render_chirp(cfg: dict) -> None:
    """audio/00_chirp.mp3 — the firmware's no-SD-card fallback beep.

    castle_sd.yaml embeds it (a card can be absent; silence would read as
    a dead speaker), but until now it only existed as a hand-made file on
    one machine — gitignored, so a fresh clone or CI could not validate
    the SD build at all. Synthesised here like everything else in audio/:
    two rising sine blips, deterministic, ~0.4 s.
    """
    sr = cfg["sample_rate"]
    t = np.arange(int(0.4 * sr)) / sr
    x = np.zeros_like(t)
    for at, f in ((0.02, 880.0), (0.20, 1320.0)):
        seg = (t >= at) & (t < at + 0.12)
        ts = t[seg] - at
        x[seg] += np.sin(2 * np.pi * f * ts) * np.exp(-ts * 28) * 0.7
    wav = OUT / "00_chirp.wav"
    write_wav(wav, x * TARGET_PEAK, sr)
    encode_mp3(wav, OUT / "00_chirp.mp3", cfg["bitrate"])
    wav.unlink()


#: Scenes rendered WITHOUT their imported song, because it is not on this
#: machine. Named in the summary — a quiet scene must never be a surprise.
NOT_HERE: list[str] = []


def known_track(track: str) -> bool:
    """Is this an import the manifest has a record of? tracks/tracks.json is
    tracked; the audio beside it is not, so the manifest is the only way to
    tell 'you have not imported this here' from 'that name is a typo'."""
    tid = Path(track).stem
    return mf.get(tid) is not None


def render_one(scene: dict, i: int, cfg: dict, keep_wav: bool) -> tuple[int, dict]:
    """Render scene `i` to NN_<id>.mp3 and report (bytes, markers).

    The WAV is the encoder's input and nothing else's, so it goes again
    unless --wav asked to keep it.
    """
    stem = f"{i:02d}_{scene['id']}"
    wav, mp3 = OUT / f"{stem}.wav", OUT / f"{stem}.mp3"
    markers = render_scene(scene, cfg, wav)
    encode_mp3(wav, mp3, cfg["bitrate"])
    # The same WAV, encoded again for the card. Synthesis is the expensive
    # half and it is already paid for here; a second LAME pass is cents.
    if card_bitrate(cfg) != cfg["bitrate"]:
        card_dir().mkdir(parents=True, exist_ok=True)
        encode_mp3(wav, card_dir() / f"{stem}.mp3", card_bitrate(cfg))
    if not keep_wav:
        wav.unlink()
    size = mp3.stat().st_size
    secs = int(scene["duration_ms"] / 1000.0 * cfg["sample_rate"]) / cfg["sample_rate"]
    print(f"{scene['id']:<12} {secs:>7.1f}s {size / 1024:>8.0f}K   {mp3.name}")
    return size, markers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="render just this scene id")
    ap.add_argument("--wav", action="store_true", help="keep intermediate WAVs")
    args = ap.parse_args()

    doc = yaml.safe_load(SCENES.read_text())
    cfg = doc["hardware"]["audio"]
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.only:
        render_chirp(cfg)
        render_test_tones(cfg)

    scenes = doc["scenes"]
    if args.only:
        scenes = [s for s in scenes if s["id"] == args.only]
        if not scenes:
            raise SystemExit(f"no scene with id {args.only!r}")

    total = 0
    all_markers: dict[str, dict[str, list]] = {}
    produced = {"00_chirp.mp3"}
    NOT_HERE.clear()
    print(f"{'scene':<12} {'length':>8} {'mp3':>9}   file")
    print("-" * 52)
    for i, scene in enumerate(doc["scenes"], start=1):
        if args.only and scene["id"] != args.only:
            continue
        size, markers = render_one(scene, i, cfg, args.wav)
        if markers:
            all_markers[scene["id"]] = markers
        total += size
        produced.add(f"{i:02d}_{scene['id']}.mp3")

    print("-" * 52)
    print(f"{'total':<12} {'':>8} {total / 1024:>8.0f}K")
    if NOT_HERE:
        print(
            f"note: {len(NOT_HERE)} scene(s) rendered WITHOUT their imported "
            f"song — it is not on this machine: {', '.join(NOT_HERE)}"
        )
    if not args.only:  # partial renders must not clobber other scenes' markers
        # A full render owns the numbered files: a scene deleted from the
        # show renumbers the rest, and 11_foo.mp3 beside 10_foo.mp3 in the
        # same directory would otherwise stay forever (judge B, JB2-5d).
        for d in (OUT, card_dir()):
            for stale in sorted(d.glob("[0-9][0-9]_*.mp3")):
                if stale.name not in produced:
                    stale.unlink()
                    print(f"swept stale {stale.relative_to(OUT.parent)}")
        (OUT / "markers.json").write_text(json.dumps(all_markers, indent=0))
        n = sum(len(m) for v in all_markers.values() for m in v.values())
        print(
            f"beat markers: {n} across {len(all_markers)} scenes -> audio/markers.json"
        )
    # 3.87 MB single-app partition minus ~0.97 MB of firmware (measured,
    # PROJECT_NOTES §12.2).
    budget = 2.9 * 1024 * 1024
    pct = total / budget * 100
    verdict = "fits" if total < budget else "OVER BUDGET"
    print(
        f"\nflash build  {cfg['bitrate']:>3} kbps  {total / 1024:>6.0f}K  "
        f"{pct:.0f}% of the ~2.9 MB single-app partition — {verdict}"
    )
    # The card is 31 GB. Printed for symmetry, not as a limit: the only
    # ceiling the SD build has is decode load, and §12.13 measured 96 kbps
    # clean on this chip.
    if card_bitrate(cfg) != cfg["bitrate"]:
        ctotal = sum(f.stat().st_size for f in card_dir().glob("[0-9][0-9]_*.mp3"))
        print(
            f"card  (SD)  {card_bitrate(cfg):>3} kbps  {ctotal / 1024:>6.0f}K  "
            f"streamed off the card — no budget"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
