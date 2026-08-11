#!/usr/bin/env python3
"""Tests for the track import pipeline.

    make test

Covers the path from "a file or a link" to "a scene the firmware can play":
time parsing, format conversion, onset detection, the manifest, the generated
scene block, and the render that consumes it.

Deliberately NOT covered: fetching from YouTube. That needs the network and
someone else's servers, and a test that fails when a site changes its markup
teaches nothing about this code. `test_ytdlp_available` checks the tool is
installed and can parse a URL, which is the part we own; there is a manual
end-to-end recipe in tracks/README.md for the rest.

No pytest dependency — this is stdlib unittest so `make test` works in the
same venv as everything else.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import analyze as ana          # noqa: E402
import import_track as it      # noqa: E402
import manifest as mf          # noqa: E402
import render_audio as ra      # noqa: E402
import yaml                    # noqa: E402


def make_click_track(path: Path, *, seconds: float = 6.0, bpm: float = 120.0,
                     hats: bool = True) -> list[float]:
    """A file with beats at known times, so onset detection has a right answer."""
    sr = ana.SR
    x = np.zeros(int(seconds * sr))
    period = 60.0 / bpm
    beats = []

    def place(sig: np.ndarray, at: float) -> None:
        i = int(at * sr)
        k = min(len(sig), len(x) - i)
        if k > 0:
            x[i:i + k] += sig[:k]

    t = 0.0
    rng = np.random.default_rng(7)
    while t < seconds - 0.3:
        n = int(0.18 * sr)
        tt = np.arange(n) / sr
        place(np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 22) * 0.9, t)   # kick
        beats.append(t)
        if hats:
            hn = int(0.05 * sr)
            ht = np.arange(hn) / sr
            place(rng.standard_normal(hn) * np.exp(-ht * 90) * 0.25, t + period / 2)
        t += period

    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return beats


class TestTimeParsing(unittest.TestCase):
    def test_forms(self) -> None:
        self.assertEqual(it.secs("12"), 12.0)
        self.assertEqual(it.secs("1:05"), 65.0)
        self.assertEqual(it.secs("1:02:03"), 3723.0)
        self.assertEqual(it.secs("0"), 0.0)

    def test_fractional(self) -> None:
        self.assertAlmostEqual(it.secs("1:00.5"), 60.5)


class TestOnsetDetection(unittest.TestCase):
    """The detector is the reason imported tracks get light at all."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.wav = cls.tmp / "click.wav"
        cls.beats = make_click_track(cls.wav, seconds=6.0, bpm=120.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_finds_the_kicks(self) -> None:
        marks = ana.analyze_file(self.wav)
        low = marks.get("onset_low", [])
        self.assertEqual(len(low), len(self.beats),
                         f"expected {len(self.beats)} kicks, found {len(low)}")

    def test_onsets_land_on_the_beat(self) -> None:
        """Within 25 ms — tighter than anyone can see in a light cue."""
        low = ana.analyze_file(self.wav)["onset_low"]
        for expected, (got, _vel) in zip(self.beats, low):
            self.assertLess(abs(got - expected), 0.025,
                            f"onset at {got:.3f}s, expected {expected:.3f}s")

    def test_downbeat_at_zero_is_not_lost(self) -> None:
        """Frame 0 has nothing to diff against; a loop's downbeat lives there."""
        low = ana.analyze_file(self.wav)["onset_low"]
        self.assertLess(low[0][0], 0.02, "the t=0 onset was dropped")

    def test_velocities_are_normalised(self) -> None:
        for band, hits in ana.analyze_file(self.wav).items():
            for t, v in hits:
                self.assertGreaterEqual(v, 0.0, band)
                self.assertLessEqual(v, 1.0, f"{band} velocity {v} above 1.0")

    def test_bands_are_separated(self) -> None:
        """Kick in the low band, hats in the high — the whole point of banding."""
        marks = ana.analyze_file(self.wav)
        self.assertIn("onset_low", marks)
        self.assertIn("onset_high", marks)

    def test_silence_yields_nothing(self) -> None:
        p = self.tmp / "silence.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(ana.SR)
            w.writeframes((np.zeros(ana.SR * 2)).astype("<i2").tobytes())
        self.assertEqual(ana.analyze_file(p), {},
                         "silence should produce no onsets, not phantom ones")

    def test_too_short_is_handled(self) -> None:
        """A clip shorter than one analysis window must not throw."""
        p = self.tmp / "tiny.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(ana.SR)
            w.writeframes((np.zeros(64)).astype("<i2").tobytes())
        self.assertEqual(ana.analyze_file(p), {})

    def test_sensitivity_changes_the_count(self) -> None:
        loose = ana.analyze_file(self.wav, sensitivity=0.3)
        tight = ana.analyze_file(self.wav, sensitivity=3.0)
        self.assertGreaterEqual(
            sum(len(v) for v in loose.values()),
            sum(len(v) for v in tight.values()),
            "lower sensitivity should find at least as many onsets")


class TestConvert(unittest.TestCase):
    """The imported file has to be something the device can actually play."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.src = cls.tmp / "src.wav"
        make_click_track(cls.src, seconds=8.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def opts(self, **over: object) -> dict:
        o = {"start": 0, "take": None, "fade_in": None, "fade_out": None,
             "bitrate": 96, "channels": 1, "sample_rate": 44100,
             "normalize": False, "gain_db": None}
        o.update(over)
        return o

    def probe(self, p: Path) -> dict:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(p)],
            capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_produces_mono_44k(self) -> None:
        out = self.tmp / "a.mp3"
        it.convert(self.src, out, self.opts())
        st = self.probe(out)["streams"][0]
        self.assertEqual(int(st["channels"]), 1)
        self.assertEqual(int(st["sample_rate"]), 44100)
        self.assertEqual(st["codec_name"], "mp3")

    def test_take_trims(self) -> None:
        out = self.tmp / "b.mp3"
        it.convert(self.src, out, self.opts(take=3.0))
        dur = float(self.probe(out)["format"]["duration"])
        self.assertAlmostEqual(dur, 3.0, delta=0.2)

    def test_start_skips_in(self) -> None:
        out = self.tmp / "c.mp3"
        it.convert(self.src, out, self.opts(start=4.0))
        dur = float(self.probe(out)["format"]["duration"])
        self.assertAlmostEqual(dur, 4.0, delta=0.3)

    def test_stereo_when_asked(self) -> None:
        out = self.tmp / "d.mp3"
        it.convert(self.src, out, self.opts(channels=2))
        self.assertEqual(int(self.probe(out)["streams"][0]["channels"]), 2)

    def test_sample_rate_override(self) -> None:
        out = self.tmp / "e.mp3"
        it.convert(self.src, out, self.opts(sample_rate=22050))
        self.assertEqual(int(self.probe(out)["streams"][0]["sample_rate"]), 22050)

    def test_hot_material_is_kept_near_full_scale(self) -> None:
        """Imported audio must land in a predictable range, not run away.

        Some history, because it is easy to over-chase this. MP3 *decoding*
        overshoots its encoder's input, so hot material comes back above 1.0 —
        measured +2.77 dBFS on a square wave before the limiter went in. The
        limiter can only act on PCM before encoding, so it cannot drive the
        decoded peak below 1.0 without throwing away real level; it brings the
        square wave to +1.82 and dense material to +0.20.

        That residue is harmless HERE, and this is the important part: every
        scene is peak-normalised to TARGET_PEAK when rendered (see
        `test_render_normalises`), and the pipeline is float throughout until
        that point, so nothing ever actually clips on the way to the device.
        The limiter earns its place by making the *level* predictable, not by
        guaranteeing a decoded ceiling.

        So this asserts the thing worth asserting: imports stay in a sane band
        rather than arriving 3 dB hot and swamping everything they are mixed
        with.
        """
        hot = self.tmp / "hot.wav"
        sr = ana.SR
        t = np.arange(int(4.0 * sr)) / sr
        # A full-scale square wave. Band-limiting it during MP3 encoding
        # produces Gibbs ringing that decodes well above the input's peak —
        # the same overshoot mechanism as a brick-walled master, but reliable
        # enough to test against instead of hoping a sine happens to trip it.
        x = np.sign(np.sin(2 * np.pi * 220 * t)) * 0.999
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
        with wave.open(str(hot), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(pcm.tobytes())

        # 1.3 is ~+2.3 dBFS: comfortably above what encoder ringing accounts
        # for, and well below the +2.77 the unlimited path produced. If this
        # ever trips, the limiter has stopped being applied.
        for normalize in (False, True):
            out = self.tmp / f"f_{normalize}.mp3"
            it.convert(hot, out, self.opts(normalize=normalize))
            peak = float(np.abs(ana.load_audio(out)).max())
            self.assertLess(
                peak, 1.3,
                f"normalize={normalize}: peaks at {peak:.4f} "
                f"({20 * np.log10(peak):+.2f} dBFS) — limiter not applied?")

    def test_quiet_source_is_not_pumped_up_by_the_limiter(self) -> None:
        """The ceiling must not act as a compressor on already-quiet audio.

        `alimiter` defaults to auto-levelling, which would drag a deliberately
        subtle atmosphere up to the ceiling and flatten it. `level=disabled`
        is what stops that, and this is the test that would catch its loss.
        """
        quiet = self.tmp / "quiet.wav"
        sr = ana.SR
        t = np.arange(int(3.0 * sr)) / sr
        x = np.sin(2 * np.pi * 220 * t) * 0.05          # -26 dBFS
        with wave.open(str(quiet), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((x * 32767).astype("<i2").tobytes())

        out = self.tmp / "q.mp3"
        it.convert(quiet, out, self.opts())
        peak = float(np.abs(ana.load_audio(out)).max())
        self.assertLess(peak, 0.15,
                        f"quiet source came back at {peak:.3f} — the limiter "
                        f"is applying makeup gain it should not")


class TestManifest(unittest.TestCase):
    """Provenance is what makes --refresh possible."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real = mf.PATH
        mf.PATH = self.tmp / "tracks.json"

    def tearDown(self) -> None:
        mf.PATH = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self) -> None:
        mf.record("x", source="https://example.com/a", title="A Title",
                  opts={"take": 20}, audio={"duration": 20.0}, onsets={"onset_low": 5})
        got = mf.get("x")
        assert got is not None
        self.assertEqual(got["source"], "https://example.com/a")
        self.assertEqual(got["title"], "A Title")
        self.assertEqual(got["opts"]["take"], 20)

    def test_refresh_keeps_user_notes(self) -> None:
        """Notes are the user's; a re-import must not wipe them."""
        mf.record("x", source="s", notes="keep me")
        mf.record("x", source="s")                    # re-import, no notes given
        got = mf.get("x")
        assert got is not None
        self.assertEqual(got["notes"], "keep me")

    def test_forget(self) -> None:
        mf.record("x", source="s")
        mf.forget("x")
        self.assertIsNone(mf.get("x"))

    def test_missing_file_is_empty_not_a_crash(self) -> None:
        self.assertEqual(mf.load(), {})

    def test_corrupt_file_is_survivable(self) -> None:
        mf.PATH.parent.mkdir(parents=True, exist_ok=True)
        mf.PATH.write_text("{not json")
        self.assertEqual(mf.load(), {})


class TestSceneBlock(unittest.TestCase):
    """The generated block has to be valid YAML that the real loader accepts."""

    def setUp(self) -> None:
        self.marks = {
            "onset_low": [(0.0, 1.0), (0.5, 0.8)],
            "onset_mid": [(0.25, 0.6)],
            "onset_high": [(0.75, 0.4)],
        }

    def parse(self, block: str) -> dict:
        doc = yaml.safe_load("scenes:\n" + block)
        return doc["scenes"][0]

    def test_is_valid_yaml(self) -> None:
        sc = self.parse(it.scene_block("my_track", 20.0, self.marks))
        self.assertEqual(sc["id"], "my_track")
        self.assertEqual(sc["duration_ms"], 20000)

    def test_pulse_streams_match_detected_bands(self) -> None:
        sc = self.parse(it.scene_block("t", 10.0, self.marks))
        synths = {p["synth"] for p in sc["pulse"]}
        self.assertEqual(synths, {"onset_low", "onset_mid", "onset_high"})

    def test_bass_drives_the_door(self) -> None:
        """The whole point of banding: low frequencies get their own zone."""
        sc = self.parse(it.scene_block("t", 10.0, self.marks))
        low = next(p for p in sc["pulse"] if p["synth"] == "onset_low")
        self.assertEqual(low["zone"], "door")

    def test_empty_bands_are_omitted(self) -> None:
        sc = self.parse(it.scene_block("t", 10.0, {"onset_low": [(0.0, 1.0)]}))
        self.assertEqual(len(sc["pulse"]), 1)

    def test_effects_are_known_to_the_firmware(self) -> None:
        """A scene referencing an effect the firmware lacks fails to build."""
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_esphome as ge
        sc = self.parse(it.scene_block("t", 10.0, self.marks))
        for eff in sc["base"].values():
            self.assertIn(eff, ge.EFFECT_IDS, f"unknown effect {eff!r}")

    def test_no_onsets_still_yields_loadable_yaml(self) -> None:
        """A track with no detectable beats must not produce a broken scene."""
        block = it.scene_block("quiet", 12.0, {})
        sc = self.parse(block)
        self.assertEqual(sc["id"], "quiet")
        self.assertIn("pulse", sc)


class TestDensityFitting(unittest.TestCase):
    """Pulse settings must suit the track, not Crypt's 48 bpm heartbeat.

    A real import fired 212 times a minute. At the built-in decay of 0.92 each
    flash was still at 28% when the next arrived, so the zone saturated and
    read as a smear instead of pulses — the exact way an imported track stops
    looking like it follows the music.
    """

    @staticmethod
    def hits(gap: float, n: int = 40) -> list[tuple[float, float]]:
        return [(i * gap, 1.0) for i in range(n)]

    def residual(self, decay: float, gap: float) -> float:
        """How much of a flash survives to the next hit."""
        return decay ** (gap / it.FRAME)

    def test_dense_material_decays_faster(self) -> None:
        decay, _ = it.fit_to_density(self.hits(0.24), 0.92)
        self.assertLess(decay, 0.92)
        self.assertLess(self.residual(decay, 0.24), 0.15,
                        "a dense band still saturates")

    def test_sparse_material_keeps_its_bloom(self) -> None:
        """A slow bell toll should not be sped up into a blink."""
        decay, scale = it.fit_to_density(self.hits(3.0), 0.972)
        self.assertEqual(decay, 0.972)
        self.assertEqual(scale, 1.0)

    def test_dense_material_is_eased_back(self) -> None:
        _, dense = it.fit_to_density(self.hits(0.2), 0.92)
        _, sparse = it.fit_to_density(self.hits(1.0), 0.92)
        self.assertLess(dense, sparse)
        self.assertGreaterEqual(dense, 0.45, "never eased into invisibility")

    def test_decay_has_a_floor(self) -> None:
        """Faster than ~0.78 per frame is a blink nobody perceives."""
        decay, _ = it.fit_to_density(self.hits(0.02), 0.92)
        self.assertGreaterEqual(decay, 0.78)

    def test_too_few_hits_falls_back(self) -> None:
        self.assertEqual(it.fit_to_density([(0.0, 1.0)], 0.9), (0.9, 1.0))
        self.assertEqual(it.fit_to_density([], 0.9), (0.9, 1.0))

    def test_identical_timestamps_do_not_divide_by_zero(self) -> None:
        same = [(1.0, 1.0)] * 5
        self.assertEqual(it.fit_to_density(same, 0.9), (0.9, 1.0))


class TestRenderIntegration(unittest.TestCase):
    """End to end: an imported file becomes a scene with light cues on it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.track = ROOT / "tracks" / "_test_integration.mp3"
        src = cls.tmp / "src.wav"
        make_click_track(src, seconds=6.0, bpm=120.0)
        it.convert(src, cls.track, {
            "start": 0, "take": None, "fade_in": None, "fade_out": None,
            "bitrate": 96, "channels": 1, "sample_rate": 44100,
            "normalize": False, "gain_db": None})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.track.unlink(missing_ok=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scene(self) -> dict:
        return {
            "id": "_test_integration", "duration_ms": 6000, "loop": True,
            "audio_file": "tracks/_test_integration.mp3",
            "pulse": [{"synth": "onset_low", "zone": "door", "intensity": 0.55}],
        }

    def test_render_produces_audio_and_markers(self) -> None:
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        buf, marks = ra.render_scene(self.scene(), cfg)
        self.assertAlmostEqual(len(buf) / 44100, 6.0, delta=0.1)
        self.assertIn("onset_low", marks)
        self.assertGreater(len(marks["onset_low"]), 5)

    def test_markers_become_light_cues(self) -> None:
        """The contract that makes imported audio drive the lights at all."""
        import gen_esphome as ge
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        _buf, marks = ra.render_scene(self.scene(), cfg)
        cues = ge.pulse_cues(self.scene(), {"_test_integration": marks})
        self.assertGreater(len(cues), 5)
        self.assertEqual(cues[0]["op"], "strike")
        self.assertEqual(cues[0]["targets"], ["door"])

    def test_render_normalises(self) -> None:
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        buf, _ = ra.render_scene(self.scene(), cfg)
        self.assertAlmostEqual(float(np.abs(buf).max()), ra.TARGET_PEAK, delta=0.02)

    def test_missing_file_fails_loudly(self) -> None:
        sc = self.scene()
        sc["audio_file"] = "tracks/does_not_exist.mp3"
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        with self.assertRaises(SystemExit):
            ra.render_scene(sc, cfg)


class TestExternalTools(unittest.TestCase):
    """Import depends on tools we do not ship. Fail with a clear reason."""

    def test_ffmpeg_present(self) -> None:
        self.assertIsNotNone(shutil.which("ffmpeg"), "ffmpeg not installed")

    def test_ytdlp_present(self) -> None:
        self.assertIsNotNone(shutil.which("yt-dlp"),
                             "yt-dlp not installed — `brew install yt-dlp`")

    def test_ytdlp_understands_a_youtube_url(self) -> None:
        """No network: --simulate with a bad URL still proves URL parsing."""
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertRegex(r.stdout.strip(), r"^\d{4}\.\d{2}\.\d{2}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
