"""Tests for the stems module — the parts that need no Demucs.

The separation itself is a 25-second GPU job and is not worth a unit test;
what IS worth testing is everything around it: the traversal guards, the
freshness check that stops stale stems validating audio that no longer
exists, and — the point of the whole feature — that per-channel analysis
actually hears a hard-panned sound in its channel and nothing in the other.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import import_reason as ir
import numpy as np
import stems
from scipy.io import wavfile

SR = 44100


def write_left_clicks(path: Path, seconds: float = 6.0, sr: int = 44100) -> int:
    """A stereo WAV whose LEFT channel clicks twice a second and whose right
    channel is silence — the exact material a mono downmix half-swallows."""
    n = int(seconds * sr)
    left = np.zeros(n)
    clicks = 0
    t = 0.5
    while t < seconds - 0.5:
        a = int(t * sr)
        left[a : a + int(0.005 * sr)] = 0.9
        clicks += 1
        t += 0.5
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for v in left:
            frames += struct.pack("<hh", int(v * 32000), 0)
        w.writeframes(bytes(frames))
    return clicks


class StemsCase(unittest.TestCase):
    sandbox: Path
    _old: tuple[Path, Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.sandbox = Path(tempfile.mkdtemp(prefix="castle-stems-test-"))
        cls._old = (stems.TRACKS, stems.STEMS)
        stems.TRACKS = cls.sandbox
        stems.STEMS = cls.sandbox / "stems"

    @classmethod
    def tearDownClass(cls) -> None:
        stems.TRACKS, stems.STEMS = cls._old
        shutil.rmtree(cls.sandbox, ignore_errors=True)


class TestGuards(StemsCase):
    def test_track_file_strips_traversal(self) -> None:
        (self.sandbox / "passwd.mp3").write_bytes(b"x")
        p = stems.track_file("../../passwd")
        self.assertEqual(
            p,
            self.sandbox / "passwd.mp3",
            "the directory part must be stripped, not resolved",
        )

    def test_stem_file_serves_only_the_two_stem_layers(self) -> None:
        d = self.sandbox / "stems" / "song"
        d.mkdir(parents=True, exist_ok=True)
        (d / "vocals.mp3").write_bytes(b"x")
        (d / "analysis.json").write_text("{}", encoding="utf-8")
        self.assertIsNotNone(stems.stem_file("song", "vocals"))
        # `combined` streams via /api/track; anything else is not a layer.
        self.assertIsNone(stems.stem_file("song", "combined"))
        self.assertIsNone(stems.stem_file("song", "analysis.json"))
        # Name-stripped, same as /api/track: the directory part is discarded,
        # so a traversal can only ever land back inside the stems dir.
        self.assertEqual(
            stems.stem_file("../../../song", "vocals"),
            stems.stem_file("song", "vocals"),
        )

    def test_analysis_names_each_missing_state(self) -> None:
        self.assertEqual(stems.analysis("nope")["error"], "no such track")
        (self.sandbox / "raw.mp3").write_bytes(b"x")
        self.assertEqual(stems.analysis("raw")["error"], "not split yet")


class TestFreshness(StemsCase):
    def test_stems_go_stale_when_the_track_is_reimported(self) -> None:
        src = self.sandbox / "tune.mp3"
        src.write_bytes(b"abc")
        d = self.sandbox / "stems" / "tune"
        d.mkdir(parents=True, exist_ok=True)
        st = src.stat()
        (d / "analysis.json").write_text(
            json.dumps({"src_bytes": st.st_size, "src_mtime": int(st.st_mtime)}),
            encoding="utf-8",
        )
        self.assertTrue(stems.fresh("tune"))
        # A re-import rewrites the file; the old split must stop counting.
        src.write_bytes(b"different content")
        self.assertFalse(stems.fresh("tune"))


class TestChannelAnalysis(StemsCase):
    def test_a_hard_left_sound_is_heard_left_and_not_right(self) -> None:
        wav = self.sandbox / "leftclicks.wav"
        clicks = write_left_clicks(wav)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            data = stems.analyse_layers({"combined": wav})
        self.assertIn("combined  left", out.getvalue())
        chans = data["layers"]["combined"]

        left_hits = sum(len(v) for v in chans["left"]["onsets"].values())
        right_hits = sum(len(v) for v in chans["right"]["onsets"].values())
        self.assertGreaterEqual(
            left_hits,
            clicks,
            "the left channel must hear its own clicks "
            "(broadband, so several bands fire)",
        )
        self.assertEqual(right_hits, 0, "silence has no onsets")
        # The raw levels are the tell the normalised strips would hide.
        self.assertGreater(chans["left"]["level"], 0.5)
        self.assertEqual(chans["right"]["level"], 0.0)
        self.assertEqual(len(chans["left"]["peaks"]), stems.PEAKS)
        self.assertAlmostEqual(data["duration"], 6.0, places=1)


def fake_sources(seconds: float = 2.0) -> dict[str, np.ndarray]:
    """What htdemucs hands back, stereo float: a kick every half second, a
    bass hum, a high 'other' tone and a sung mid tone. The kick and bass are
    loud enough that their raw sum would clip."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    drums = np.zeros(n)
    for at in np.arange(0.25, seconds - 0.1, 0.5):
        a = int(at * SR)
        drums[a : a + 400] = 0.8
    tone = {
        "drums": drums,
        "bass": 0.6 * np.sin(2 * np.pi * 55 * t),
        "other": 0.2 * np.sin(2 * np.pi * 2000 * t),
        "vocals": 0.3 * np.sin(2 * np.pi * 440 * t) * (t > 1.0),
    }
    return {k: np.stack([v, v], axis=1).astype(np.float32) for k, v in tone.items()}


def fake_demucs(
    src: Path, out: Path, _device: str, _fast: bool = False
) -> subprocess.CompletedProcess[str]:
    """Writes the four sources where `demucs.separate -n htdemucs` would."""
    where = out / "htdemucs" / src.stem
    where.mkdir(parents=True)
    for name, x in fake_sources().items():
        wavfile.write(where / f"{name}.wav", SR, x)
    return subprocess.CompletedProcess([], 0, "", "")


class TestFourStemMix(StemsCase):
    def test_backing_is_the_three_instruments_summed_then_guarded(self) -> None:
        sep = self.sandbox / "mix-sep"
        fake_demucs(Path("song.mp3"), sep, "cpu")
        wavs = stems.mix_stems(sep, self.sandbox / "mix-out")
        self.assertEqual(set(wavs), {"vocals", "backing", "drums", "bass", "other"})
        src = fake_sources()
        raw = src["drums"] + src["bass"] + src["other"]
        peak = float(np.abs(raw).max())
        self.assertGreater(peak, 1.0, "the fixture must exercise the guard")
        _, backing = wavfile.read(wavs["backing"])
        # demucs's two-stem `no_vocals`: summed raw, then rescaled once.
        np.testing.assert_allclose(backing, raw / (1.01 * peak), atol=1e-6)
        # A stem that never clips is passed through untouched.
        _, drums = wavfile.read(wavs["drums"])
        np.testing.assert_allclose(drums, src["drums"], atol=1e-6)

    def test_a_missing_source_is_named(self) -> None:
        sep = self.sandbox / "half-sep"
        (sep / "htdemucs" / "x").mkdir(parents=True)
        wavfile.write(sep / "htdemucs" / "x" / "drums.wav", SR, np.zeros((10, 2)))
        err = io.StringIO()
        with self.assertRaises(SystemExit) as c, contextlib.redirect_stderr(err):
            stems.mix_stems(sep, self.sandbox / "half-out")
        # The owner reads a sentence; the stem that was missing is the detail.
        self.assertEqual(str(c.exception), ir.GENERIC)
        self.assertIn("    demucs produced no bass stem", err.getvalue())


class TestSeparateOut(StemsCase):
    def test_out_splits_beside_the_library_not_into_it(self) -> None:
        src = fake_sources()
        mix = sum(src.values(), np.zeros_like(src["drums"])) / 2
        wavfile.write(self.sandbox / "song.wav", SR, mix)
        scratch = self.sandbox / "scratch"
        with (
            mock.patch.object(stems, "_run_demucs", side_effect=fake_demucs),
            mock.patch.object(stems, "_encode", side_effect=shutil.copyfile),
            mock.patch.object(stems.importlib.util, "find_spec", return_value=True),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(stems.separate("song", out=scratch), 0)
        self.assertFalse(
            (self.sandbox / "stems" / "song").exists(), "the library was written"
        )
        dest = scratch / "song"
        for name in ("vocals", "backing", "drums", "bass", "other"):
            self.assertTrue((dest / f"{name}.mp3").is_file(), name)
        data = json.loads((dest / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(data["layers"]), stems.LAYERS)
        for layer in data["layers"].values():
            self.assertEqual(set(layer), set(stems.CHANNELS))
        kick = data["layers"]["drums"]["both"]["onsets"].get("onset_low", [])
        self.assertGreaterEqual(len(kick), 3, "the drum stem hears its kicks")
        self.assertIn("drums     both", out.getvalue())
        self.assertTrue(stems.fresh("song", scratch))
        self.assertFalse(stems.fresh("song"), "the library holds no split")
        # A current split is not redone without --force.
        with (
            mock.patch.object(stems, "_run_demucs") as rerun,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            stems.separate("song", out=scratch)
        rerun.assert_not_called()

    def test_a_two_stem_cache_is_still_current(self) -> None:
        """Splits made before drums/bass/other existed stay valid."""
        (self.sandbox / "old.mp3").write_bytes(b"abc")
        d = self.sandbox / "stems" / "old"
        d.mkdir(parents=True, exist_ok=True)
        st = (self.sandbox / "old.mp3").stat()
        layers: dict[str, object] = {
            k: {"both": {"peaks": []}} for k in ("vocals", "backing", "combined")
        }
        (d / "analysis.json").write_text(
            json.dumps(
                {
                    "layers": layers,
                    "src_bytes": st.st_size,
                    "src_mtime": int(st.st_mtime),
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(stems.fresh("old"))
        got = stems.analysis("old")
        self.assertTrue(got["ok"])
        self.assertFalse(got["stale"])
        self.assertEqual(set(got["layers"]), {"vocals", "backing", "combined"})

    def test_stem_file_does_not_serve_the_instrument_stems(self) -> None:
        d = self.sandbox / "stems" / "kit"
        d.mkdir(parents=True, exist_ok=True)
        (d / "drums.mp3").write_bytes(b"x")
        # The studio's Rust twin (studio_media.rs) serves vocals/backing only.
        self.assertIsNone(stems.stem_file("kit", "drums"))


class TestFastCpu(unittest.TestCase):
    """The CPU shortcut is opt-in, CPU-only, and reaches demucs's argv."""

    SRC, OUT = Path("song.wav"), Path("sep")

    def test_the_default_argv_is_demucs_own_defaults(self) -> None:
        argv = stems.demucs_argv(self.SRC, self.OUT, "cpu")
        self.assertNotIn("--overlap", argv)
        self.assertNotIn("-j", argv)
        self.assertEqual(argv[-1], "song.wav")

    def test_fast_adds_overlap_and_one_job_per_core_on_cpu(self) -> None:
        with mock.patch.object(stems.os, "cpu_count", return_value=6):
            argv = stems.demucs_argv(self.SRC, self.OUT, "cpu", fast=True)
        at = argv.index("--overlap")
        self.assertEqual(argv[at : at + 4], ["--overlap", "0.1", "-j", "6"])
        self.assertEqual(argv[-1], "song.wav", "the track stays the last word")

    def test_fast_leaves_a_gpu_run_alone(self) -> None:
        self.assertEqual(
            stems.demucs_argv(self.SRC, self.OUT, "mps", fast=True),
            stems.demucs_argv(self.SRC, self.OUT, "mps"),
        )

    def test_the_environment_opts_in(self) -> None:
        for value, want in (("1", True), ("", False), ("0", False)):
            with mock.patch.dict(stems.os.environ, {stems.FAST_ENV: value}):
                self.assertIs(stems.fast_cpu(), want, value)

    def test_separate_and_the_cli_hand_fast_to_demucs(self) -> None:
        with (
            mock.patch.object(stems, "track_file", return_value=Path("x.wav")),
            mock.patch.object(stems, "fresh", return_value=False),
            mock.patch.object(stems.importlib.util, "find_spec", return_value=True),
            mock.patch.object(stems.platform, "system", return_value="Windows"),
            mock.patch.object(
                stems,
                "_run_demucs",
                return_value=subprocess.CompletedProcess([], 1, "", "boom"),
            ) as run,
            mock.patch.object(sys, "argv", ["stems.py", "x", "--fast-cpu"]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as c,
        ):
            stems.main()
        self.assertEqual(run.call_args.args[2:], ("cpu", True))
        self.assertEqual(str(c.exception), ir.GENERIC)
        self.assertIn("    boom", err.getvalue())

    def test_demucs_words_become_the_owners(self) -> None:
        said = {
            "RuntimeError: MPS backend out of memory": ir.OUT_OF_MEMORY,
            "OSError: [Errno 28] No space left on device": ir.DISK_FULL,
        }
        for stderr, want in said.items():
            with (
                mock.patch.object(stems, "track_file", return_value=Path("x.wav")),
                mock.patch.object(stems, "fresh", return_value=False),
                mock.patch.object(stems.importlib.util, "find_spec", return_value=1),
                mock.patch.object(stems.platform, "system", return_value="Linux"),
                mock.patch.object(
                    stems,
                    "_run_demucs",
                    return_value=subprocess.CompletedProcess([], 1, "", stderr),
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as c,
            ):
                stems.separate("x")
            self.assertEqual(str(c.exception), want)

    def test_no_demucs_is_named_for_the_owner(self) -> None:
        with (
            mock.patch.object(stems, "track_file", return_value=Path("x.wav")),
            mock.patch.object(stems, "fresh", return_value=False),
            mock.patch.object(stems.importlib.util, "find_spec", return_value=None),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as c,
        ):
            stems.separate("x")
        self.assertEqual(str(c.exception), ir.DEMUCS_MISSING)
        self.assertIn("pip install demucs", err.getvalue())


if __name__ == "__main__":
    unittest.main()
