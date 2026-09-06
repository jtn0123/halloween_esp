"""The Rust studio's media routes, stated absolutely — one server, no twin.

This is the port of `tests/test_studio_media_rust.py`, which held the Rust
studio's waveform / stems / stem-stream / compare answers against the Python
studio's byte for byte. That comparison dies with `tools/studio.py`
(docs/RETIREMENT.md phase 3): with one server left, "the two agreed" is a
tautology. So every assertion here says what the answer IS — a status code, a
key set, a number with a tolerance — rather than that two servers matched.

It also absorbs what the Python-only suites covered on the same routes: the
waveform reads and the sensitivity query from `test_studio_api.TestReads`, the
non-audio guard, and the two `/studio/probe` refusals from that file's
`TestWrites`. `test_media_failures.TestProbe`'s other arms — a missing yt-dlp,
a timeout, yt-dlp's own last line, an unparseable answer, the happy path's
`duration_text` — reached `studio_media.probe()` through a mocked subprocess
and have no HTTP face at all; the Rust half of that logic is unit-tested in
`core/src/studio_probe.rs`, and only the two guards that answer BEFORE any
subprocess are portable here.

The library is `tests/studio_rs_case.seed_library`, whose click tracks have
beats at arithmetic times — so the onset detector can be graded against the
right answer rather than merely against itself.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rs_case import CARGO, IN_CI, StudioCase

#: What `seed_library` actually wrote. `make_click_track` places a kick every
#: 60/bpm seconds while `t < seconds - 0.3`, so these are the times a correct
#: low-band detector must find — not numbers copied out of an implementation.
ALPHA_BEATS = [0.0, 0.5, 1.0, 1.5]  # t_alpha.wav: 2.0 s at the default 120 bpm
BETA_BEATS = [0.0, 2 / 3, 4 / 3, 2.0, 8 / 3]  # t_beta.wav: 3.0 s at 90 bpm
#: A kick's attack is not a mathematical instant and the analysis hops in
#: ~11 ms steps; 40 ms is a bucket, not a fudge factor.
BEAT_SLOP = 0.04

BANDS = ("onset_low", "onset_mid", "onset_high")


def times(body: dict[str, Any], band: str) -> list[float]:
    """The hit times of one onset band."""
    return [hit[0] for hit in body["onsets"][band]]


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Waveform(StudioCase):
    """GET /studio/waveform/<id> — everything the clip editor draws."""

    def drawable(self, tid: str, dur: float) -> dict[str, Any]:
        """One waveform, checked for the shape every non-empty one has."""
        status, d = self.json(f"/studio/waveform/{tid}")
        self.assertEqual(status, 200)
        self.assertEqual(set(d), {"id", "duration", "peaks", "onsets", "env"})
        self.assertEqual(d["id"], tid)
        self.assertAlmostEqual(d["duration"], dur, delta=0.05)
        peaks = d["peaks"]
        self.assertEqual(len(peaks), 1000, "PEAKS buckets, no more and no fewer")
        self.assertTrue(all(0.0 <= p <= 1.0 for p in peaks), "a peak left [0, 1]")
        self.assertEqual(max(peaks), 1.0, "peaks are normalised to the loudest one")
        self.assertGreater(sum(1 for p in peaks if p > 0.0), 100, "a flat envelope")
        return d

    def test_waveform_is_the_json_the_clip_editor_draws(self) -> None:
        status, headers, _ = self.req("/studio/waveform/t_alpha")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        self.drawable("t_alpha", 2.0)

    def test_a_longer_track_reports_its_own_length_and_beats(self) -> None:
        """Two tracks, two lengths: a duration hard-coded somewhere in the
        decode path would pass the first check and fail this one."""
        d = self.drawable("t_beta", 3.0)
        found = times(d, "onset_low")
        self.assertEqual(len(found), len(BETA_BEATS), f"onset_low was {found}")
        for got, want in zip(found, BETA_BEATS, strict=True):
            self.assertAlmostEqual(got, want, delta=BEAT_SLOP)

    def test_the_low_band_finds_every_click_and_nothing_else(self) -> None:
        """The kicks are at known times: the one place the detector can be
        graded against the truth rather than against its own last answer."""
        d = self.drawable("t_alpha", 2.0)
        found = times(d, "onset_low")
        self.assertEqual(len(found), len(ALPHA_BEATS), f"onset_low was {found}")
        for got, want in zip(found, ALPHA_BEATS, strict=True):
            self.assertAlmostEqual(got, want, delta=BEAT_SLOP)

    def test_every_onset_hit_carries_a_time_a_velocity_and_a_pan(self) -> None:
        _, d = self.json("/studio/waveform/t_alpha")
        for band in BANDS:
            hits = d["onsets"][band]
            self.assertTrue(hits, f"{band} found nothing in a click track")
            for hit in hits:
                self.assertEqual(len(hit), 3, f"{band} hit without a pan: {hit}")
                t, vel, pan = hit
                self.assertGreaterEqual(t, 0.0, band)
                self.assertLessEqual(t, d["duration"], band)
                self.assertGreater(vel, 0.0, band)
                self.assertLessEqual(vel, 1.0, f"{band} velocity above 1.0: {hit}")
                self.assertGreaterEqual(pan, -1.0, band)
                self.assertLessEqual(pan, 1.0, band)

    def test_the_level_series_riding_along_are_pairs_not_hits(self) -> None:
        """`onsets` also carries `level_*` series — the per-band loudness the
        desk shades with. They are not hits and have no pan, which is why the
        pan check above is scoped to `onset_*`."""
        _, d = self.json("/studio/waveform/t_alpha")
        levels = {k: v for k, v in d["onsets"].items() if k.startswith("level_")}
        self.assertTrue(levels, "no level_* series came back at all")
        for band, rows in levels.items():
            for row in rows:
                self.assertEqual(len(row), 2, f"{band} row is not [t, level]: {row}")
                self.assertGreaterEqual(row[0], 0.0, band)
                self.assertLessEqual(row[1], 1.0, band)
        self.assertEqual(
            set(d["onsets"]) - set(levels),
            set(BANDS),
            "an onsets key that is neither a band nor a level series",
        )

    def test_the_level_envelope_is_a_rising_series_of_pairs(self) -> None:
        """`env` is the coarse loudness curve under the waveform; one point,
        or one that runs backwards in time, draws nothing."""
        _, d = self.json("/studio/waveform/t_alpha")
        env = d["env"]
        self.assertGreaterEqual(len(env), 4, "a trivial envelope is not a curve")
        self.assertTrue(all(len(row) == 2 for row in env), "env rows are [t, level]")
        ts = [t for t, _ in env]
        self.assertEqual(ts, sorted(ts), "the envelope went backwards in time")
        self.assertGreaterEqual(ts[0], 0.0)
        self.assertLessEqual(ts[-1], d["duration"])
        levels = [v for _, v in env]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in levels), "a level left [0, 1]")
        self.assertGreater(max(levels), 0.0, "the whole envelope reads as silence")

    def test_a_zero_frame_track_keeps_the_short_shape(self) -> None:
        """t_empty.wav is a valid WAV with no frames. The answer is the four
        empty fields — not a 500, and not the five-field shape with nulls in
        it, which is what the page would have to draw around."""
        status, headers, _ = self.req("/studio/waveform/t_empty")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        _, d = self.json("/studio/waveform/t_empty")
        self.assertEqual(
            d, {"id": "t_empty", "duration": 0.0, "peaks": [], "onsets": {}}
        )

    def test_an_unknown_track_is_a_404_saying_so(self) -> None:
        status, d = self.json("/studio/waveform/nope")
        self.assertEqual(status, 404)
        self.assertEqual(d, {"error": "no such track"})

    def test_a_non_audio_file_is_not_readable_as_audio(self) -> None:
        """tracks/ holds other things — the manifest, a README — and none of
        them may be read through the waveform or the streaming route."""
        status, d = self.json("/studio/waveform/tracks.json")
        self.assertEqual(status, 404)
        self.assertEqual(d, {"error": "no such track"})
        status, d = self.json("/studio/track/tracks.json")
        self.assertEqual(status, 404)
        self.assertEqual(d, {"error": "not found"})


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Sensitivity(StudioCase):
    """The clip editor's sliders, in the four spellings the desk sends.

    A slider that does not change the answer is a slider that lies, so these
    say what each spelling DOES: which bands move, which stand still, and
    which way the count goes.
    """

    def bands(self, query: str = "") -> dict[str, list[float]]:
        status, d = self.json("/studio/waveform/t_beta" + query)
        self.assertEqual(status, 200, query)
        return {b: times(d, b) for b in BANDS}

    def test_the_whole_band_knob_loosens_and_tightens_every_band(self) -> None:
        loose = self.bands("?sensitivity=0.4")
        tight = self.bands("?sensitivity=2.5")
        self.assertGreaterEqual(
            len(loose["onset_low"]),
            len(tight["onset_low"]),
            "a looser threshold found fewer clicks than a tighter one",
        )
        # Loose enough to find every kick, and then some: the beats are the
        # floor the knob may never go under while it is being opened up.
        self.assertGreaterEqual(len(loose["onset_low"]), len(BETA_BEATS))
        for band in BANDS:
            self.assertGreaterEqual(len(loose[band]), len(tight[band]), band)

    def test_per_band_knobs_move_only_the_bands_they_name(self) -> None:
        base = self.bands()
        got = self.bands("?sens_low=0.6&sens_high=3.0")
        self.assertEqual(
            got["onset_mid"],
            base["onset_mid"],
            "sens_low/sens_high disturbed the mid band",
        )
        self.assertGreaterEqual(
            len(got["onset_low"]),
            len(base["onset_low"]),
            "sens_low=0.6 is looser than the 1.1 default and must not find less",
        )
        self.assertLessEqual(
            len(got["onset_high"]),
            len(base["onset_high"]),
            "sens_high=3.0 is tighter than the 1.1 default",
        )

    def test_a_non_numeric_sensitivity_falls_back_instead_of_failing(self) -> None:
        """The desk sends whatever is in the box. `?sensitivity=abc` is the
        default analysis, byte for byte — not a 500 and not an empty band."""
        plain = self.req("/studio/waveform/t_beta")
        junk = self.req("/studio/waveform/t_beta?sensitivity=abc")
        self.assertEqual(junk[0], 200)
        self.assertEqual(junk[1]["content-type"], "application/json")
        self.assertEqual(junk[2], plain[2], "a junk sensitivity changed the answer")

    def test_the_mixed_form_composes_the_base_and_the_override(self) -> None:
        """`?sensitivity=0.4&sens_mid=1.9`: 0.4 for the bands nobody named,
        1.9 for the one that was. Each half is exactly what that half asks
        for on its own — the composition adds nothing and drops nothing."""
        mixed = self.bands("?sensitivity=0.4&sens_mid=1.9")
        base_only = self.bands("?sensitivity=0.4")
        mid_only = self.bands("?sens_mid=1.9")
        self.assertEqual(mixed["onset_low"], base_only["onset_low"])
        self.assertEqual(mixed["onset_high"], base_only["onset_high"])
        self.assertEqual(mixed["onset_mid"], mid_only["onset_mid"])
        self.assertNotEqual(
            mixed["onset_mid"],
            base_only["onset_mid"],
            "sens_mid=1.9 made no difference at all",
        )


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Stems(StudioCase):
    """GET /studio/stems/<id> and GET /studio/stem/<id>/<layer>."""

    def test_a_fresh_split_comes_back_with_what_was_recorded(self) -> None:
        """t_alpha's analysis.json was stamped with the track's real size and
        mtime, so it still describes the audio: ok, and not stale. What the
        splitter wrote — peaks, per-layer onsets, its own extra keys — is
        served straight back, with only ok/stale added."""
        status, headers, _ = self.req("/studio/stems/t_alpha")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        _, d = self.json("/studio/stems/t_alpha")
        self.assertIs(d["ok"], True)
        self.assertIs(d["stale"], False)
        self.assertEqual(d["src_bytes"], (self.tracks / "t_alpha.wav").stat().st_size)
        self.assertEqual(
            d["layers"],
            {
                "vocals": {
                    "peaks": [0.1, 0.25, 1.0],
                    "onsets": {"onset_mid": [[0.5, 1.0]]},
                },
                "backing": {"peaks": []},
            },
        )
        self.assertEqual(d["note"], "fixture 🎃", "a non-ASCII field was mangled")

    def test_a_split_of_a_different_file_is_reported_stale(self) -> None:
        """t_beta's analysis.json records a byte count the track no longer
        has. The route still serves it — the desk offers a re-split — but it
        must say so, or the user tunes to stems of the previous import."""
        status, d = self.json("/studio/stems/t_beta")
        self.assertEqual(status, 200)
        self.assertIs(d["ok"], True)
        self.assertIs(d["stale"], True)
        self.assertEqual(d["layers"], {})

    def test_an_unsplit_track_and_an_unknown_one_are_told_apart(self) -> None:
        """Both are 404s, and the sentence is the difference the desk shows:
        "not split yet" offers a Split button, "no such track" does not."""
        status, d = self.json("/studio/stems/t_del")
        self.assertEqual(status, 404)
        self.assertEqual(d, {"ok": False, "error": "not split yet"})
        status, d = self.json("/studio/stems/zzz")
        self.assertEqual(status, 404)
        self.assertEqual(d, {"ok": False, "error": "no such track"})

    def test_a_stem_streams_the_exact_bytes_on_disk(self) -> None:
        status, headers, body = self.req("/studio/stem/t_alpha/vocals")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "audio/mpeg")
        self.assertEqual(
            body, (self.tracks / "stems" / "t_alpha" / "vocals.mp3").read_bytes()
        )

    def test_every_way_of_asking_for_a_stem_that_is_not_there_is_404(self) -> None:
        """A layer nobody split, a track nobody has, and a path with the
        layer missing altogether — one sentence, no traceback, no 500."""
        for path in (
            "/studio/stem/t_alpha/drums",
            "/studio/stem/none/vocals",
            "/studio/stem/short",
        ):
            with self.subTest(path=path):
                status, d = self.json(path)
                self.assertEqual(status, 404)
                self.assertEqual(d, {"error": "no such stem"})


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class CompareAndProbe(StudioCase):
    """The two routes that answer before any encoder or fetcher is started."""

    def test_a_compare_token_nobody_encoded_is_404(self) -> None:
        """The codec-comparison files live in a map POST /studio/compare
        fills. Nothing has been encoded in this process, so every token is
        unknown — which is also what a restarted studio answers."""
        status, d = self.json("/studio/compare/tok/mp3")
        self.assertEqual(status, 404)
        self.assertEqual(d, {"error": "no such comparison"})

    def refusal(self, status: int, d: dict[str, Any], elapsed: float) -> None:
        """One probe refusal: the caller's mistake, and nothing spawned.

        400 rather than 200 — a bad link is the caller's mistake and the
        status code is allowed to say so. `ok: false` because the desk reads
        that field, not the code. And the guard runs BEFORE yt-dlp: over HTTP
        there is no subprocess to mock, so the proof is the clock — reaching
        yt-dlp costs a process spawn and a network timeout, and would come
        back with yt-dlp's own words instead of these.
        """
        self.assertEqual(status, 400)
        self.assertIs(d["ok"], False)
        self.assertEqual(d["error"], "that does not look like a link")
        self.assertIn("link", d["error"])
        self.assertLess(
            elapsed, 5.0, "the refusal took long enough to have shelled out"
        )

    def test_probe_of_a_non_link_is_refused_without_touching_the_network(self) -> None:
        start = time.monotonic()
        status, d = self.json("/studio/probe", "POST", {"url": "not a link at all"})
        self.refusal(status, d, time.monotonic() - start)

    def test_probe_of_an_empty_body_is_the_same_refusal_not_a_crash(self) -> None:
        """An empty body parses as an empty object, so there is no url — the
        same caller's mistake, and never a traceback on the socket."""
        start = time.monotonic()
        status, _, raw = self.req(
            "/studio/probe", "POST", {"Content-Type": "application/json"}, b""
        )
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), parsed
        self.refusal(status, parsed, time.monotonic() - start)


if __name__ == "__main__":
    unittest.main()
